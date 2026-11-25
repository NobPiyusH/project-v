"""M2b: real moving video, one Wan 2.2 image-to-video clip per shot, started from the shot's approved keyframe. Port of VideoStage.

- draft: fast mode (4 steps) at 480p for every shot: cheap, to judge motion;
- final: hybrid mode (12 steps, full model on the composition expert) at 720p, only for shots whose draft is approved and not marked
  "keep the draft". 4K upscaling and 24 fps come from the finishing stages.

Every batch is estimated first; the owner approves it unless it is under the auto-approve amount, and the monthly GPU cap stops it either way.
Every clip is an asset with provenance + licence, and every clip a cost entry.

Hybrid (owner, 18 Nov 2026): drafts on Wan (a few rupees, to judge motion), finals on Gemini Omni Flash at 720p. Set per kind with
`provider_draft / provider_final` (wan | omni); Omni is held to the monthly Gemini cap.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from studio.agents.jfmt import fixed, jround, string_hash
from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.ledger.budget_guard import GEMINI, BudgetExceededError
from studio.media.types import ClipEstimate, ClipJob, ClipRequest, ClipResult
from studio.planner.imported import load as load_imported
from studio.production import camera
from studio.production.support import ProductionDeps, StageSupport, diagram_shot
from studio.production.video_gpu import DRAFT, FINAL, POD, VideoGpu
from studio.routing.router import Candidate
from studio.workflow.types import StageResult

MAX_FRAMES = 81  # Wan 2.2: ~5 s at 16 fps
MIN_FRAMES = 33  # ~2 s: shorter clips lose motion quality
FPS = 16
MAX_CONTAINERS = 4  # = max_containers in workers/modal_apps/wan.py
DIAGRAM_FPS = 24
WAN = "modal_wan"
OMNI = "gemini_omni"


def _hu(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def frames(seconds: float) -> int:
    """4n+1 frames covering the shot at 16 fps, between ~2 s and Wan's ~5 s limit (longer shots are retimed in M2c)."""
    n = math.ceil(seconds * FPS / 4.0) * 4 + 1
    return max(MIN_FRAMES, min(MAX_FRAMES, n))


def omni_resolution(kind: str) -> str:
    """720p for drafts and finals: SeedVR2 upscales every clip to 4K, so 1080p from Omni would cost 50% more for little."""
    return "720p"


def omni_seconds(shot_seconds: float) -> int:
    """Omni clip length: the shot's length in whole seconds, 3-10 s."""
    return max(3, min(10, math.ceil(shot_seconds)))


def containers(kind: str, clips: int) -> int:
    """Drafts render in turn on ONE GPU: start-up + model load (~2 min of H100 time) is paid once, and a draft takes only ~12 s, so parallel
    GPUs would mostly pay for start-ups. Finals (~8 min each) spread over up to 4 GPUs."""
    return 0 if clips == 0 else 1 if kind == DRAFT else min(MAX_CONTAINERS, clips)


def _num(o: Any) -> float:
    return float(o) if isinstance(o, (int, float, Decimal)) and not isinstance(o, bool) else 0.0


def _jstr(v: Any) -> str:
    return "" if v is None else str(v)


class VideoStage(StageSupport):
    def __init__(self, d: ProductionDeps, wan_app: str = "projectv-wan", wan_class: str = "Wan", public_url: str = "http://127.0.0.1:8080",
                 auto_approve_inr: Decimal = Decimal(0), hero_takes: int = 2, draft_provider: str = "wan", final_provider: str = "omni",
                 omni_model: str = "gemini-omni-1.2-flash", router: Any = None, video_gpu: VideoGpu | None = None,
                 diagram_width: int = 1920, diagram_height: int = 1080, camera_presets: bool = False, camera_allow_untested: bool = False):
        super().__init__(d)
        self.wan_app, self.wan_class, self.public_url = wan_app, wan_class, public_url
        self.auto_approve_inr = auto_approve_inr
        self.hero_takes = max(1, min(4, hero_takes))
        self.draft_provider = "wan" if draft_provider is None else draft_provider.strip().lower()
        self.final_provider = "wan" if final_provider is None else final_provider.strip().lower()
        self.omni_model = omni_model
        self.router = router
        self.video_gpu = video_gpu  # None in tests; used only with provider-draft/final=broker
        self.diagram_width, self.diagram_height = diagram_width, diagram_height
        # VIDEO_CAMERA_PRESETS (wiring default: on): vertical clips on the rented pod run the Wan 2.2 Fun Camera graph with a real camera
        # preset per shot (tools/film4k/camera_presets.py) instead of the SVI graph, whose camera barely moves.
        self.camera_presets, self.camera_allow_untested = camera_presets, camera_allow_untested

    def _on_broker(self, kind: str) -> bool:
        return self.video_gpu is not None and self.video_gpu.clips(kind)

    def _camera_on(self, kind: str, use_omni: bool, vertical: bool) -> bool:
        return self.camera_presets and not use_omni and vertical and self._on_broker(kind)

    def shot_cameras(self, video_id: uuid.UUID) -> dict[str, str]:
        """shot id -> Fun Camera preset, for every shot that is animated by a generated clip. The plan's (or the Director's) camera move wins
        when it names a preset; the rest are varied tested presets. An untested preset named by an imported plan is refused."""
        shots = [s for s in self._clip_shots(video_id) if not diagram_shot(s) and s["footage_asset_id"] is None]
        imp = load_imported(self.db, video_id)
        names = camera.assign([s["camera_move"] for s in shots], self.camera_allow_untested or (imp is not None and imp.allowUntested),
                              strict=imp is not None)
        return {str(s["id"]): n for s, n in zip(shots, names, strict=True)}

    def omni(self, kind: str) -> bool:
        """The owner's preferred provider for this kind (config); the router may still pick another (M3a)."""
        return (self.draft_provider if kind == DRAFT else self.final_provider) == "omni"

    def route_omni(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None, log: bool) -> bool:
        """M3a: the router chooses Wan or Omni for this batch. Each candidate is priced on its own job list (Omni finals are hero shots only),
        the owner's configured provider wins when it fits its budget, and otherwise the router falls back (e.g. Gemini cap used up: finals on
        Wan). Logged in routing_decision unless log is false."""
        if self.router is None:  # tests without a router: the configured provider, as before M3
            return self.omni(kind)
        wan_jobs = self.jobs(video_id, kind, only, False)
        omni_jobs = self.jobs(video_id, kind, only, True)
        candidates = [Candidate(WAN, self.d.pricing.modal_gpu_inr("H100", self.gpu_seconds(wan_jobs, kind))),
                      Candidate(OMNI, self.omni_inr(omni_jobs, kind))]
        choice = self.router.choose(video_id, "DRAFT_GEN" if kind == DRAFT else "FINAL_GEN", f"video.i2v.{kind}", candidates,
                                    OMNI if self.omni(kind) else WAN, log)
        return choice.provider_id == OMNI

    def omni_inr(self, jobs: list[ClipJob], kind: str) -> Decimal:
        total = Decimal(0)
        for j in jobs:
            total += self.d.pricing.omni_inr(j.num_frames / 24.0, omni_resolution(kind))
        return total

    # ------------------------------------------------------------------------------------------- estimate & request

    def estimate(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None) -> ClipEstimate:
        if self._on_broker(kind):
            return self._broker_estimate(video_id, kind, only)
        d = self.d
        try:
            use_omni = self.route_omni(video_id, kind, only, False)  # preview: not logged
        except BudgetExceededError:
            use_omni = self.omni(kind)  # nothing fits: show the preferred plan; the request itself is then refused with reasons
        jobs = self.jobs(video_id, kind, only, use_omni)
        drawn = len(self.diagram_jobs(video_id, kind, only))
        drawn_note = "" if drawn == 0 else f" Plus {drawn} diagram shot{'' if drawn == 1 else 's'} drawn on the laptop (free)."
        fx = d.pricing.fx()
        if use_omni:
            inr = _hu(self.omni_inr(jobs, kind), 2)
            what = f"{kind} clips (Gemini Omni, {omni_resolution(kind)})"
            wall = 0.0 if not jobs else math.ceil(len(jobs) / 3.0) * 0.75
            if not jobs:
                summary = f"No shots need {what}."
            else:
                left = _hu(max(d.budget.gemini_remaining(), Decimal(0)), 0)
                summary = (f"Ready to make {len(jobs)} {what}: about ₹{_hu(inr, 0)} (${fixed(float(inr) / fx, 2)}) of Gemini and "
                           f"{fixed(math.ceil(wall), 0)} min. ₹{left} of this month's Gemini cap left.")
            if kind == FINAL and jobs:
                # Owner's choice (18 Nov): Omni only where the Wan draft isn't good enough. Before approving, mark "keep the draft" on any of
                # these shots whose draft already looks right: it then costs nothing.
                shots = list(dict.fromkeys(f"#{j.shot_number}" for j in jobs))
                summary += " Shots: " + ", ".join(shots) + '. Mark "keep the draft" on any whose Wan draft already looks right (₹0), then approve.'
            return ClipEstimate(kind, len(jobs) + drawn, 0, inr, float(inr) / fx, wall, bool(jobs) and inr > self.auto_approve_inr,
                                summary + drawn_note)
        gpu = self.gpu_seconds(jobs, kind)
        inr = _hu(d.pricing.modal_gpu_inr("H100", gpu), 2)
        usd = float(inr) / fx
        n = containers(kind, len(jobs))
        start = d.pricing.gpu_container_start_seconds()
        wall = 0.0 if not jobs else (start + (gpu - n * start) / n) / 60
        what = "draft clips (fast, 480p)" if kind == DRAFT else "final clips (hybrid, 720p)"
        if not jobs:
            summary = f"No shots need {what}."
        else:
            left = _hu(max(d.budget.gpu_remaining(), Decimal(0)), 0)
            summary = (f"Ready to make {len(jobs)} {what} on Modal H100: about ₹{_hu(inr, 0)} (${fixed(usd, 2)}) of GPU and "
                       f"{fixed(math.ceil(wall), 0)} min. ₹{left} of this month's GPU cap left.")
        return ClipEstimate(kind, len(jobs) + drawn, gpu, inr, usd, wall, bool(jobs) and inr > self.auto_approve_inr, summary + drawn_note)

    def clip_request(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None) -> ClipRequest:
        """The batch for the media worker. Refused (non-retryable) when it would break the monthly GPU cap."""
        if self._on_broker(kind):
            return self._broker_request(video_id, kind, only)
        use_omni = self.route_omni(video_id, kind, only, True)  # the decision, logged (rule 6)
        jobs = self.jobs(video_id, kind, only, use_omni)
        drawn = self.diagram_jobs(video_id, kind, only)  # diagram lane: drawn on the laptop, free, no provider
        if not jobs and not drawn:
            raise RuntimeError("No shots with images to animate" if kind == DRAFT else "No approved drafts need a final render")
        if not jobs:
            return ClipRequest(str(video_id), self.wan_app, self.wan_class, self.public_url, drawn, 0, 0, Decimal(0), "wan", None, None)
        if use_omni:
            inr = self.omni_inr(jobs, kind)
            self.d.budget.require(inr, f"{len(jobs)} {kind} clips on Gemini Omni")
            return ClipRequest(str(video_id), self.wan_app, self.wan_class, self.public_url, jobs + drawn, 0, 0, _hu(inr, 2), "omni",
                               omni_resolution(kind), self.omni_model)
        gpu = self.gpu_seconds(jobs, kind)
        inr = self.d.pricing.modal_gpu_inr("H100", gpu)
        self.d.budget.require_gpu(inr, f"{len(jobs)} {kind} clips")
        return ClipRequest(str(video_id), self.wan_app, self.wan_class, self.public_url, jobs + drawn, containers(kind, len(jobs)), gpu,
                           _hu(inr, 2), "wan", None, None)

    # ------------------------------------------------------------------------------------------- GPU broker route

    def _broker_estimate(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None) -> ClipEstimate:
        """Same Wan 2.2 jobs as the Modal route (same pictures, sizes, takes); they run one after another on the rented pod."""
        gpu = self.video_gpu
        jobs = self.jobs(video_id, kind, only, False)
        drawn = len(self.diagram_jobs(video_id, kind, only))
        sec = 0.0 if not jobs else gpu.clip_seconds(video_id, jobs)
        inr = _hu(gpu.inr(sec), 2)
        fx = self.d.pricing.fx()
        if not jobs:
            summary = f"No shots need {kind} clips."
        else:
            summary = (f"Ready to make {len(jobs)} {kind} clips on a rented GPU pod (cheapest live price, Auto): about ₹{_hu(inr, 0)} "
                       f"(${fixed(float(inr) / fx, 2)}) and {fixed(math.ceil(sec / 60), 0)} min, inside the daily GPU cap.")
        if drawn > 0:
            summary += f" Plus {drawn} diagram shot{'' if drawn == 1 else 's'} drawn on the laptop (free)."
        return ClipEstimate(kind, len(jobs) + drawn, sec, inr, float(inr) / fx, math.ceil(sec / 60), bool(jobs) and inr > self.auto_approve_inr,
                            summary)

    def _broker_request(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None) -> ClipRequest:
        gpu = self.video_gpu
        jobs = self.jobs(video_id, kind, only, False)
        drawn = self.diagram_jobs(video_id, kind, only)
        if not jobs and not drawn:
            raise RuntimeError("No shots with images to animate" if kind == DRAFT else "No approved drafts need a final render")
        if not jobs:
            return ClipRequest(str(video_id), self.wan_app, self.wan_class, self.public_url, drawn, 0, 0, Decimal(0), "wan", None, None)
        sec = gpu.clip_seconds(video_id, jobs)
        inr = _hu(gpu.inr(sec), 2)
        if self.router is not None:  # rule 6: the routing decision names the pod route
            self.router.choose(video_id, "DRAFT_GEN" if kind == DRAFT else "FINAL_GEN", "video.i2v.dream", [Candidate(POD, inr)], POD, True)
        session = gpu.session(video_id, sec)  # refused with the reason when the daily cap or every provider says no
        chain = self._chain(jobs, only)
        return ClipRequest(str(video_id), self.wan_app, self.wan_class, self.public_url, jobs + drawn, 1, sec, inr, "pod", None, None, 0,
                           session.url, session.ready_timeout_seconds, chain, camera.CHAIN_BEATS if chain else 0)

    @staticmethod
    def _chain(jobs: list[ClipJob], only: list[uuid.UUID] | None) -> bool:
        """Camera clips continue from the last frame of the shot before, two shots per chain, then restart from the next shot's own keyframe
        (25 Nov motion block: from the third chained beat the scene drifts blue and soft). Only for a whole batch of consecutive shots 1..n:
        a redo of single shots starts each from its own keyframe (no chain to break)."""
        cams = [j for j in jobs if j.camera]
        return bool(cams) and only is None and [j.shot_number for j in jobs] == list(range(1, len(jobs) + 1))

    def release_gpu(self, video_id: uuid.UUID, why: str) -> None:
        """The batch is saved: let go of the pod (the rental is booked on this Video's ledger when it ends)."""
        if self.video_gpu is not None:
            self.video_gpu.release(video_id, why)

    # ------------------------------------------------------------------------------------------- jobs

    def diagram_jobs(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None) -> list[ClipJob]:
        """Diagram-lane shots (the Director's lane = diagram): the job's prompt is the shot's scene spec (JSON) and mode "diagram"; the media
        worker draws it with Manim (workers/diagram). Drafts only: a drawn clip is final quality, so nothing is re-rendered for a "final".
        Already-drawn shots are skipped unless the owner picked them for a redo."""
        if kind != DRAFT:
            return []
        wanted = None if only is None else {str(u) for u in only}
        out: list[ClipJob] = []
        for s in self._clip_shots(video_id):
            sid = str(s["id"])
            if (not diagram_shot(s) or s["footage_asset_id"] is not None or (wanted is not None and sid not in wanted)
                    or (wanted is None and s["draft_clip_asset_id"] is not None)):
                continue
            p = json.loads(s["prompt_json"])
            sec = max(5.0, min(12.0, _num(s["duration_target_sec"])))
            spec = p.get("diagramSpec")
            out.append(ClipJob(sid, int(s["ord"]) + 1, int(s["draft_takes"]) + 1, kind, "diagram", None,
                               "{}" if spec is None else json.dumps(spec, separators=(",", ":")), self.diagram_width, self.diagram_height,
                               jround(sec * DIAGRAM_FPS), abs(string_hash(sid))))
        return out

    def jobs(self, video_id: uuid.UUID, kind: str, only: list[uuid.UUID] | None, use_omni: bool | None = None) -> list[ClipJob]:
        if use_omni is None:
            use_omni = self.omni(kind)
        plan = self.plan(video_id)
        preset = self.d.presets.by_id_or_default(plan.stylePreset)
        vertical = plan.aspectRatio == "9:16"
        draft = kind == DRAFT
        long_side = 832 if draft else 1280
        short_side = 480 if draft else 720
        if not vertical and not use_omni and self._on_broker(kind):
            long_side, short_side = 1280, 720  # 16:9 on the rented pod: 720p-class drafts too (the pod worker's limit is 1280x720)
        wanted = None if only is None else {str(u) for u in only}
        cams = self.shot_cameras(video_id) if self._camera_on(kind, use_omni, vertical) else {}
        jobs: list[ClipJob] = []
        for s in self._clip_shots(video_id):
            sid = str(s["id"])
            if wanted is not None and sid not in wanted:
                continue
            if s["keyframe_uri"] is None or s["footage_asset_id"] is not None or diagram_shot(s):
                continue  # no image to animate, real footage chosen, or a diagram shot (drawn, see diagram_jobs)
            if cams and not draft and wanted is None:
                continue  # a Fun Camera clip is already 720x1280 and final quality: a final pass only when the owner picks the shot
            if not draft and wanted is None and (s["draft_clip_asset_id"] is None or s["final_wanted"] is not True):
                continue  # finals only for shots with a draft that the owner didn't mark "keep the draft"
            if not draft and wanted is None and use_omni and s["priority"] != "hero":
                continue  # cost (owner, 18 Nov): Omni finals only for hero shots; others keep their Wan draft unless picked
            p = json.loads(s["prompt_json"])
            parts = [_jstr(p.get("motionPrompt")).strip(), _jstr(s["description"]).strip(), "Visual style: " + preset.visual_style]
            prompt = re.sub(r"^(\. )+", "", ". ".join(parts))
            first = int(s["draft_takes" if draft else "final_takes"]) + 1
            # Renders vary like seeds (owner, 17 Nov: one FA3 render "had better motion"): hero shots get several draft takes with different
            # seeds, so the best motion can be picked. Finals render the chosen draft once.
            takes = self.hero_takes if draft and s["priority"] == "hero" and sid not in cams else 1
            if use_omni:
                long_side, short_side = 1280, 720
            if sid in cams:  # Fun Camera: the tested size, 81 frames at 16 fps whatever the shot's length (the edit trims it)
                for take in range(first, first + takes):
                    jobs.append(ClipJob(sid, int(s["ord"]) + 1, take, kind, "camera", s["keyframe_uri"], prompt, camera.WIDTH, camera.HEIGHT,
                                        camera.FRAMES, abs(string_hash(sid)) + take, camera=cams[sid]))
                continue
            for take in range(first, first + takes):
                jobs.append(ClipJob(sid, int(s["ord"]) + 1, take, kind, "omni" if use_omni else "fast" if draft else "hybrid", s["keyframe_uri"], prompt,
                                    short_side if vertical else long_side, long_side if vertical else short_side,
                                    omni_seconds(_num(s["duration_target_sec"])) * 24 if use_omni else frames(_num(s["duration_target_sec"])),
                                    abs(string_hash(sid)) + take))
        return jobs

    def gpu_seconds(self, jobs: list[ClipJob], kind: str) -> float:
        per81 = self.d.pricing.wan_seconds_per_81_frames(kind)
        render = sum(per81 * j.num_frames / MAX_FRAMES for j in jobs)
        return render + containers(kind, len(jobs)) * self.d.pricing.gpu_container_start_seconds()

    # ------------------------------------------------------------------------------------------- save results

    def mark_clips(self, video_id: uuid.UUID, shot_ids: list[uuid.UUID], status: str | None, error: str | None) -> None:
        with self.db.transaction():
            for s in shot_ids:
                self.db.execute("UPDATE shot SET clip_status = %(st)s, clip_error = %(err)s, updated_at = now() "
                                "WHERE id = %(s)s AND scene_id IN (SELECT id FROM scene WHERE video_id = %(v)s)",
                                {"st": status, "err": error, "s": s, "v": video_id})

    def save_clips(self, video_id: uuid.UUID, request: ClipRequest, r: ClipResult) -> StageResult:
        health: dict[str, Any] = {}
        try:
            return self._store_clips(video_id, request, r, health)
        finally:
            # M3a health: a batch where every clip failed cools the provider down. Reported even when the stage fails and rolls back.
            if self.router is not None and health.get("ok", 0) + health.get("bad", 0) > 0:
                self.router.report_batch(OMNI if request.provider == "omni" else WAN, health["ok"], health["bad"], health.get("error"))

    def _store_clips(self, video_id: uuid.UUID, request: ClipRequest, r: ClipResult, health: dict[str, Any]) -> StageResult:
        d = self.d
        jobs = {f"{j.shot_id}:{j.take}": j for j in request.jobs}
        activated: set[str] = set()  # first good take of each shot becomes the one in use
        ok = ai_ok = ai_bad = stopped = 0
        gpu = 0.0
        errors: list[str] = []
        with self.db.transaction():
            for o in r.clips:
                job = jobs.get(f"{o.shot_id}:{o.take}")
                if job is None:
                    continue
                shot_id = uuid.UUID(o.shot_id)
                diagram = job.mode == "diagram"  # drawn on the laptop: no provider, no cost, not a router signal
                pod = request.provider == "pod"  # the rental is booked when the broker ends it, not per clip
                gpu += o.gpu_seconds
                if o.output_tokens > 0:
                    d.ledger.record(video_id, GEMINI, d.pricing.omni_tokens_inr(o.output_tokens), Decimal(o.output_tokens), "output_tokens", False,
                                    f"{job.kind.upper()} clip shot {job.shot_number} take {job.take} (Gemini Omni {request.resolution})")
                if o.gpu_seconds > 0 and not pod and not diagram:
                    d.ledger.record(video_id, "modal_h100", d.pricing.modal_gpu_inr("H100", o.gpu_seconds), Decimal(str(o.gpu_seconds)),
                                    "gpu_seconds", True, f"{job.kind.upper()} clip shot {job.shot_number} take {job.take} (Wan {o.mode})")
                if o.uri is None:
                    was_stopped = o.error == "stopped"
                    stopped += was_stopped
                    if not was_stopped:
                        errors.append(f"shot {job.shot_number}: {o.error}")
                        ai_bad += 0 if diagram else 1
                    self.db.execute("UPDATE shot SET clip_status = %(st)s, clip_error = %(e)s, updated_at = now() WHERE id = %(s)s",
                                    {"st": "STOPPED" if was_stopped else "FAILED", "e": None if was_stopped else o.error, "s": shot_id})
                    continue
                omni = request.provider == "omni"
                provider = "diagram_manim" if diagram else POD if pod else "gemini_omni" if omni else "modal_wan"
                model = ("manim 1080p24 diagram" if diagram else f"wan2.2-fun-camera-a14b fp8 (rented pod, {job.camera})" if pod and job.camera
                         else "wan2.2-i2v-a14b fp8 (rented pod)" if pod
                         else f"{request.omni_model} {request.resolution}" if omni else f"wan2.2-i2v-a14b {o.mode}")
                licence = lic.LICENCE_OWN_RENDER if diagram else lic.LICENCE_GEMINI if omni else lic.LICENCE_WAN
                asset_id = d.assets.create(NewAsset(video_id, "video", o.uri, "video/mp4", job.width, job.height,
                                                    o.frames / o.fps if o.fps > 0 else None, provider, model, job.prompt, job.seed, licence, True))
                self.db.execute("UPDATE asset SET shot_id = %(s)s, clip_kind = %(k)s, fps = %(f)s WHERE id = %(a)s",
                                {"s": shot_id, "k": job.kind, "f": o.fps if o.fps > 0 else FPS, "a": asset_id})
                if o.shot_id not in activated:
                    activated.add(o.shot_id)
                    column = "draft_clip_asset_id" if job.kind == DRAFT else "final_clip_asset_id"
                    self.db.execute(f"UPDATE shot SET {column} = %(a)s, clip_status = 'READY', clip_error = NULL, updated_at = now() WHERE id = %(s)s",
                                    {"a": asset_id, "s": shot_id})
                ok += 1
                ai_ok += 0 if diagram else 1
            if r.start_ups() > 0 and request.provider != "pod":  # observed cold starts, not containers requested
                start = r.start_ups() * d.pricing.gpu_container_start_seconds()
                d.ledger.record(video_id, "modal_h100", d.pricing.modal_gpu_inr("H100", start), Decimal(str(start)), "gpu_seconds", True,
                                f"Wan GPU start-up + model load ({r.start_ups()} cold start{'' if r.start_ups() == 1 else 's'}, estimated)")
            kind = DRAFT if not request.jobs else request.jobs[0].kind
            health.update(ok=ai_ok, bad=ai_bad, error=errors[0] if errors else None)
            if ok == 0 and stopped == 0:
                raise RuntimeError(f"No {kind} clips were made: " + " | ".join(errors))
        per_shot: dict[str, int] = {}
        for j in request.jobs:
            per_shot[j.shot_id] = per_shot.get(j.shot_id, 0) + 1
        multi = sum(1 for n in per_shot.values() if n > 1)
        takes_note = f"; {multi} hero shot{'' if multi == 1 else 's'} ha{'s' if multi == 1 else 've'} takes to compare" if multi > 0 else ""
        how = f"Gemini Omni, {request.resolution}" if request.provider == "omni" else "fast, 480p" if kind == DRAFT else "hybrid, 720p"
        summary = (f"{ok}/{len(jobs)} {kind} clips ({how})" + (f" in {fixed(gpu, 0)} s of GPU render time" if gpu > 0 else "")
                   + (f"; stopped by owner before {stopped} finished" if stopped > 0 else "")
                   + (f"; {len(errors)} failed: {errors[0]}" if errors else "") + takes_note + ".")
        return StageResult.of(summary, json.dumps({"ok": ok, "failed": errors, "stopped": stopped, "gpuSeconds": jround(gpu)}, separators=(",", ":")))

    # ------------------------------------------------------------------------------------------- owner actions

    def set_final_wanted(self, shot_id: uuid.UUID, wanted: bool) -> None:
        """False = keep the draft as this shot's clip (no final render)."""
        self.db.execute("UPDATE shot SET final_wanted = %(w)s, updated_at = now() WHERE id = %(s)s", {"w": wanted, "s": shot_id})

    def select_clip(self, shot_id: uuid.UUID, asset_id: uuid.UUID) -> None:
        """Switch a shot to any of its clip takes (draft or final). Free: nothing is generated."""
        rows = self.db.rows("SELECT clip_kind FROM asset WHERE id = %(a)s AND shot_id = %(s)s AND kind = 'video'", {"a": asset_id, "s": shot_id})
        if not rows:
            raise RuntimeError("That clip is not a take of this shot")
        column = "final_clip_asset_id" if rows[0]["clip_kind"] == FINAL else "draft_clip_asset_id"
        self.db.execute(f"UPDATE shot SET {column} = %(a)s, updated_at = now() WHERE id = %(s)s", {"a": asset_id, "s": shot_id})

    def clip_versions(self, shot_id: uuid.UUID) -> list[dict[str, Any]]:
        """Every clip take of a shot, newest first."""
        return self.db.rows(
            "SELECT a.id, a.clip_kind AS kind, a.created_at, p.model, (a.id = s.draft_clip_asset_id OR a.id = s.final_clip_asset_id) AS active "
            "FROM asset a JOIN shot s ON s.id = a.shot_id LEFT JOIN provenance p ON p.id = a.provenance_id "
            "WHERE a.shot_id = %(s)s AND a.kind = 'video' ORDER BY a.created_at DESC", {"s": shot_id})

    def _clip_shots(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows(
            """
            SELECT s.id, s.ord, s.duration_target_sec, s.camera_move, s.description, s.prompt_json::text AS prompt_json, s.priority,
                   s.draft_clip_asset_id, s.final_clip_asset_id, s.final_wanted, s.footage_asset_id, a.uri_hot AS keyframe_uri,
                   (SELECT count(*) FROM asset x WHERE x.shot_id = s.id AND x.kind = 'video' AND x.clip_kind = 'draft') AS draft_takes,
                   (SELECT count(*) FROM asset x WHERE x.shot_id = s.id AND x.kind = 'video' AND x.clip_kind = 'final') AS final_takes
            FROM shot s JOIN scene sc ON sc.id = s.scene_id LEFT JOIN asset a ON a.id = s.keyframe_asset_id
            WHERE sc.video_id = %(v)s ORDER BY s.ord
            """, {"v": video_id})
