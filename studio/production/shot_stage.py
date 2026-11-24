"""Shot plan (narration-anchored timing), keyframes, and per-shot redo/versions. Port of ShotStage, plus StockStage (the stock footage lane,
run right after the shot plan so a shot with no match is back on the AI lane before keyframes are made)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from studio.agents.director import TimedLine
from studio.agents.jfmt import fixed, jround, string_hash
from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.ledger.budget_guard import GEMINI
from studio.media.types import (
    KeyframeJob,
    KeyframeRequest,
    KeyframeResult,
    StockProvenance,
    StockRequest,
    StockResult,
)
from studio.planner.bank import BeatPlan, bank_problems, beat_prompt, clean_ids, known_ids, references
from studio.production import timing as nt
from studio.production.records import ImagePolicy, StoredScript
from studio.production.support import ProductionDeps, StageSupport, diagram_shot, round3, stock_shot
from studio.production.video_gpu import POD, VideoGpu
from studio.routing.router import Candidate
from studio.workflow.types import StageResult

# Image and video models can't spell (a planned "text overlay" came out as "NO KO"); words a viewer must read are overlayText, drawn by the
# edit. Every keyframe prompt ends with this.
NO_WRITING = ". No text, letters, numbers, logos or writing anywhere in the picture"
GEMINI_IMAGE = "gemini_image"
ZIMAGE = "modal_zimage"
log = logging.getLogger(__name__)


def _j(v: Any) -> str:
    """Java string concatenation of a possibly missing value."""
    return "null" if v is None else str(v)


def _blank(s: str | None) -> bool:
    return s is None or not s.strip()


class ShotStage(StageSupport):
    def __init__(self, d: ProductionDeps, router: Any = None, video_gpu: VideoGpu | None = None, diagrams: Any = None,
                 stock_lane: bool = False, shot_bank: bool = True):
        super().__init__(d)
        self.router = router
        self.video_gpu = video_gpu  # None in tests; FLUX keyframes on the pod only with projectv.video.provider-keyframes=broker
        self.diagrams = diagrams  # None in tests; used only with projectv.video.diagram-lane=true
        self.stock_lane = stock_lane  # projectv.video.stock-lane (default off)
        self.shot_bank = shot_bank  # VIDEO_SHOT_BANK (default on): bank descriptions pasted into every shot that names them

    def release_gpu(self, video_id: uuid.UUID, why: str) -> None:
        """Lets go of the rented GPU pod after a keyframe batch (no-op unless provider-keyframes=broker)."""
        if self.video_gpu is not None:
            self.video_gpu.release(video_id, why)

    def route_keyframes(self, video_id: uuid.UUID, hero: bool, images: int, policy: ImagePolicy) -> str:
        """M3a: one routed decision per class of shot (hero / standard) instead of a fixed rule. The owner's image policy is the preference
        (PREMIUM = Gemini, FREE = Z-Image); in AUTO the router's floors decide: hero keyframes need quality 7 (Gemini), standard ones are
        fine at 5-6 (Z-Image, cheaper). Gemini's estimate includes the reserve kept for voice and music, so a batch never eats it.
        Returns the job-level provider id the media worker expects."""
        if self.router is None:  # tests without a router: the pre-M3 rule
            return "gemini_api" if policy == ImagePolicy.PREMIUM or (policy == ImagePolicy.AUTO and hero) else "modal_zimage"
        pricing = self.d.pricing
        gemini = pricing.image_inr("1K") * images
        if policy != ImagePolicy.PREMIUM:
            gemini += pricing.gemini_reserve_inr()
        preferred = {ImagePolicy.PREMIUM: GEMINI_IMAGE, ImagePolicy.FREE: ZIMAGE, ImagePolicy.AUTO: None}[policy]
        choice = self.router.choose(video_id, "KEYFRAMES", "image.keyframe.hero" if hero else "image.keyframe.standard",
                                    [Candidate(GEMINI_IMAGE, gemini), Candidate(ZIMAGE, pricing.zimage_inr(images))], preferred, True)
        return "gemini_api" if choice.provider_id == GEMINI_IMAGE else "modal_zimage"

    # ---------------------------------------------------------------------------------------------- shot plan

    def shot_plan(self, video_id: uuid.UUID, owner_notes: str | None) -> StageResult:
        plan = self.plan(video_id)
        concept = self.latest_concept(video_id)
        if concept is None:
            raise RuntimeError("No concept yet")
        script = self.latest_script(video_id)
        if script is None:
            raise RuntimeError("No script yet")
        beats = self.beat_times(video_id)
        bpm = self.bpm(video_id)
        total = float(plan.durationSec)
        timed = [TimedLine(ln.index, ln.text, ln.spoken, ln.duration_sec if ln.duration_sec is not None else 6.0) for ln in script.lines]
        sp = self.d.director.plan(video_id, plan, concept, timed, bpm, total, owner_notes)  # paid call: its cost commits before the shots are stored
        if not sp.shots:
            raise RuntimeError("The director returned no shots")
        shots = sp.shots
        is_short = plan.format == "short"
        bank = sp.bank if self.shot_bank else None
        dropped = 0
        for line in bank_problems(bank):  # a bad bank entry is not pasted anywhere; the owner sees it in the summary
            log.warning("shot plan bank (%s): %s", video_id, line)

        # Narration drives the timeline: fixed start per line from the real voice lengths (no drift, nothing cut).
        timing = nt.plan([nt.Line(ln.index, ln.spoken and ln.duration_sec is not None, 0.0 if ln.duration_sec is None else ln.duration_sec)
                          for ln in script.lines], total, is_short)
        total = timing.total_sec
        anchors: dict[int, float] = {}
        seen_lines: set[int] = set()
        for i, s in enumerate(shots):
            li = s.lineIndex
            if li is not None and li not in seen_lines:
                seen_lines.add(li)
                if li in timing.starts:
                    # Cut to the line's shot a moment before its first word.
                    anchors[i] = max(0.0, timing.starts[li] - nt.LEAD)
        times = nt.fit_anchored([s.durationSec for s in shots], anchors, total, beats, 1.2 if is_short else 2.5)

        # Hero shots get the premium (paid) image model; models over-assign them, so enforce <= 20% in code. The opening and the final shot
        # keep hero status first, then the director's picks in order.
        hero_cap = max(1, jround(len(shots) * 0.20))
        hero_idx: list[int] = []
        for i in (0, len(shots) - 1):
            if shots[i].priority == "hero" and len(hero_idx) < hero_cap and i not in hero_idx:
                hero_idx.append(i)
        for i in range(len(shots)):
            if len(hero_idx) >= hero_cap:
                break
            if shots[i].priority == "hero" and i not in hero_idx:
                hero_idx.append(i)

        with self.db.transaction():
            locked = self.lock_script(video_id)
            starts = {str(k): v for k, v in timing.starts.items()}
            self.write_script(video_id, StoredScript(locked.version, locked.lines, locked.voice_name, locked.voice_style, locked.music_brief,
                                                     starts, timing.tempo))
            self.db.execute("DELETE FROM scene WHERE video_id = %(v)s", {"v": video_id})
            scene_id: uuid.UUID | None = None
            scene_title: str | None = None
            scene_ord = 0
            heroes = 0
            for i, s in enumerate(shots):
                title = "Scene" if _blank(s.sceneTitle) else s.sceneTitle
                if scene_id is None or title != scene_title:
                    scene_id, scene_title = uuid.uuid4(), title
                    self.db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(id)s, %(v)s, %(o)s, %(t)s)",
                                    {"id": scene_id, "v": video_id, "o": scene_ord, "t": title})
                    scene_ord += 1
                priority = "hero" if i in hero_idx else "standard"
                heroes += priority == "hero"
                prompt: dict[str, Any] = {"keyframePrompt": s.keyframePrompt, "motionPrompt": s.motionPrompt, "lineIndex": s.lineIndex}
                if bank is not None and not bank.empty():
                    chars, locs = known_ids(bank, s.characterIds, s.locationIds)
                    dropped += len(clean_ids(s.characterIds)) + len(clean_ids(s.locationIds)) - len(chars) - len(locs)
                    beat = BeatPlan(prompt=s.keyframePrompt, characterIds=chars, locationIds=locs, continuity=s.continuity)
                    if not _blank(s.keyframePrompt):  # a diagram or stock shot may have none: nothing to pin down
                        prompt["keyframePrompt"] = beat_prompt(bank, beat, "image")
                    if not _blank(s.motionPrompt):
                        beat.prompt = s.motionPrompt
                        prompt["motionPrompt"] = beat_prompt(bank, beat, "video")
                    if chars or locs:
                        prompt["characterIds"], prompt["locationIds"] = chars, locs
                        refs = references(bank, chars, locs)
                        if refs:
                            prompt["bankReferences"] = refs
                if s.continuity and s.continuity.strip():
                    prompt["continuity"] = s.continuity.strip()
                claims = [c.strip() for c in s.claims or [] if c is not None and c.strip()]
                if claims:
                    prompt["claims"] = claims  # for a later verifier; nothing reads them yet
                if not _blank(s.sfxQuery):
                    prompt["sfxQuery"] = s.sfxQuery.strip()
                if not _blank(s.referenceQuery):
                    prompt["referenceQuery"] = s.referenceQuery.strip()
                if not _blank(s.footageQuery):
                    prompt["footageQuery"] = s.footageQuery.strip()
                if s.diagram() and self.diagrams is not None and self.diagrams.enabled() and plan.aspectRatio == "16:9":
                    # Diagram lane: drawn, not generated. The spec comes from this shot's narration line (validated, one repair).
                    line = next((ln.text for ln in script.lines if s.lineIndex is not None and ln.index == s.lineIndex), s.description)
                    prompt["lane"] = "diagram"
                    prompt["diagramSpec"] = self.diagrams.spec_for(video_id, plan.title, line, s.description, times[i][1])
                if s.stock() and self.stock_lane and prompt.get("lane") is None:
                    prompt["lane"] = "stock"
                    prompt["stockQuery"] = s.description
                prompt["startSec"] = round3(times[i][0])
                self.db.execute(
                    "INSERT INTO shot (id, scene_id, ord, duration_target_sec, shot_type, camera_move, description, prompt_json, priority, overlay_text) "
                    "VALUES (%(id)s, %(scene)s, %(ord)s, %(dur)s, %(type)s, %(move)s, %(desc)s, CAST(%(prompt)s AS jsonb), %(prio)s, %(overlay)s)",
                    {"id": uuid.uuid4(), "scene": scene_id, "ord": i, "dur": round3(times[i][1]), "type": s.shotType, "move": s.cameraMove,
                     "desc": s.description, "prompt": json.dumps(prompt, separators=(",", ":")), "prio": priority,
                     "overlay": None if _blank(s.overlayText) else s.overlayText})
        warning = "" if timing.warning is None else " " + timing.warning
        if bank is not None and not bank.empty():
            warning += f" Bank: {len(bank.characters or [])} characters, {len(bank.locations or [])} locations pasted word for word."
            if dropped:
                warning += f" {dropped} unknown bank id(s) were ignored."
        return StageResult.of(f"{len(shots)} shots in {scene_ord} scenes over {fixed(total, 0)}s ({heroes} hero); every narration line starts "
                              f"on its own shot, other cuts on the {fixed(bpm, 0)} BPM beat grid.{warning}",
                              json.dumps(sp.model_dump(), separators=(",", ":")))

    # ---------------------------------------------------------------------------------------------- keyframes

    def keyframe_request(self, video_id: uuid.UUID, owner_notes: str | None) -> KeyframeRequest:
        return self._build_keyframe_request(video_id, None, owner_notes, ImagePolicy.AUTO)

    def shot_keyframe_request(self, video_id: uuid.UUID, shot_ids: list[uuid.UUID], owner_notes: str | None,
                              policy: ImagePolicy) -> KeyframeRequest:
        """Per-shot redo: only the given shots, with the owner's notes and image policy."""
        r = self._build_keyframe_request(video_id, set(shot_ids), owner_notes, policy)
        premium = sum(1 for j in r.jobs if j.provider == "gemini_api")
        self.d.budget.require(self.d.pricing.image_inr("1K") * premium, f"SHOT REDO ({premium} premium images)")
        return r

    def _build_keyframe_request(self, video_id: uuid.UUID, only: set[uuid.UUID] | None, owner_notes: str | None,
                                policy: ImagePolicy) -> KeyframeRequest:
        plan = self.plan(video_id)
        preset = self.d.presets.by_id_or_default(plan.stylePreset)
        vertical = plan.aspectRatio == "9:16"
        width, height = (768, 1344) if vertical else (1344, 768)
        jobs: list[KeyframeJob] = []
        attempt = self.next_keyframe_attempt(video_id)
        # diagram shots are drawn, stock shots are real clips: no keyframe
        wanted = [s for s in self.shots(video_id) if (only is None or s["id"] in only) and not diagram_shot(s) and not stock_shot(s)]
        heroes = sum(1 for s in wanted if s["priority"] == "hero")
        on_pod = self.video_gpu is not None and self.video_gpu.keyframes()
        # M3a: the router picks the image provider per class of shot (logged; budget- and failure-aware)
        hero_provider = POD if on_pod else None
        standard_provider = POD if on_pod else None
        if not on_pod:
            hero_provider = None if heroes == 0 else self.route_keyframes(video_id, True, heroes, policy)
            standard_provider = None if heroes == len(wanted) else self.route_keyframes(video_id, False, len(wanted) - heroes, policy)
        for shot in wanted:
            p = json.loads(shot["prompt_json"])
            notes = "" if _blank(owner_notes) else ". Director's notes: " + owner_notes
            prompt = f"{_j(p.get('keyframePrompt'))}. Visual style: {preset.visual_style}{NO_WRITING}{notes}"
            provider = hero_provider if shot["priority"] == "hero" else standard_provider
            shot_id = str(shot["id"])
            # Real reference photos only help the image model that accepts them (Gemini), on hero shots.
            reference = "" if provider != "gemini_api" or p.get("referenceQuery") is None else str(p["referenceQuery"])
            jobs.append(KeyframeJob(shot_id, attempt, provider, prompt, width, height, plan.aspectRatio, "1K", reference,
                                    abs(string_hash(shot_id)) + attempt, "flux2-klein" if on_pod else None))
        if not jobs:
            raise RuntimeError("No shots to render; run the shot plan first" if only is None else "None of those shots belong to this video")
        d = self.d
        if on_pod:  # FLUX.2 klein pictures on the rented pod (same broker session as Dream, same daily cap)
            sec = self.video_gpu.picture_seconds(video_id, len(jobs))
            if self.router is not None:
                self.router.choose(video_id, "KEYFRAMES", "image.dream", [Candidate(POD, self.video_gpu.inr(sec))], POD, True)
            session = self.video_gpu.session(video_id, sec)
            return KeyframeRequest(str(video_id), d.image_model, d.modal_app, d.modal_class, jobs, session.url, session.ready_timeout_seconds)
        return KeyframeRequest(str(video_id), d.image_model, d.modal_app, d.modal_class, jobs)

    def mark_shots(self, video_id: uuid.UUID, shot_ids: list[uuid.UUID], status: str | None, error: str | None) -> None:
        """Status shown on each shot card while a redo runs (REDOING / READY / FAILED)."""
        with self.db.transaction():
            for s in shot_ids:
                self.db.execute("UPDATE shot SET status = %(st)s, error = %(err)s, updated_at = now() "
                                "WHERE id = %(s)s AND scene_id IN (SELECT id FROM scene WHERE video_id = %(v)s)",
                                {"st": status, "err": error, "s": s, "v": video_id})

    def edit_shot_prompt(self, shot_id: uuid.UUID, keyframe_prompt: str) -> None:
        """Owner edits a shot's image prompt directly; the next redo uses it (no AI rewrite involved)."""
        with self.db.transaction():
            rows = self.db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(s)s", {"s": shot_id})
            if not rows:
                raise LookupError(f"No shot {shot_id}")
            p = json.loads(rows[0]["p"])
            p["keyframePrompt"] = keyframe_prompt.strip()
            p["editedByOwner"] = True
            self.db.execute("UPDATE shot SET prompt_json = CAST(%(p)s AS jsonb), updated_at = now() WHERE id = %(s)s",
                            {"p": json.dumps(p, separators=(",", ":")), "s": shot_id})

    def shot_versions(self, shot_id: uuid.UUID) -> list[dict[str, Any]]:
        """All keyframe versions ever made for a shot, newest first."""
        return self.db.rows(
            "SELECT a.id, a.created_at, p.provider, p.model, (a.id = s.keyframe_asset_id) AS active "
            "FROM asset a JOIN shot s ON s.id = a.shot_id LEFT JOIN provenance p ON p.id = a.provenance_id "
            "WHERE a.shot_id = %(s)s AND a.kind = 'image' ORDER BY a.created_at DESC", {"s": shot_id})

    def select_keyframe(self, shot_id: uuid.UUID, asset_id: uuid.UUID) -> None:
        """Switch a shot back (or forward) to any of its versions. Free: nothing is generated."""
        rows = self.db.rows(
            "UPDATE shot SET keyframe_asset_id = %(a)s, status = 'READY', error = NULL, updated_at = now() "
            "WHERE id = %(s)s AND EXISTS (SELECT 1 FROM asset WHERE id = %(a)s AND shot_id = %(s)s) RETURNING id", {"a": asset_id, "s": shot_id})
        if not rows:
            raise RuntimeError("That image is not a version of this shot")

    def video_of_shot(self, shot_id: uuid.UUID) -> uuid.UUID:
        rows = self.db.rows("SELECT sc.video_id FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE s.id = %(s)s", {"s": shot_id})
        if not rows:
            raise LookupError(f"No shot {shot_id}")
        return rows[0]["video_id"]

    def save_keyframes(self, video_id: uuid.UUID, request: KeyframeRequest, r: KeyframeResult) -> StageResult:
        jobs = {j.shot_id: j for j in request.jobs}
        try:
            return self._store_keyframes(video_id, jobs, r)
        finally:
            # M3a health, per provider: a provider whose every image failed cools down. Reported even when the stage fails (and so rolls back).
            if self.router is not None:
                for prov in ("gemini_api", "modal_zimage"):
                    good = bad = 0
                    sample: str | None = None
                    for o in r.results:
                        job = jobs.get(o.shot_id)
                        if job is None or job.provider != prov:
                            continue
                        if o.uri is not None:
                            good += 1
                        else:
                            bad += 1
                            sample = o.error if sample is None else sample
                    self.router.report_batch(GEMINI_IMAGE if prov == "gemini_api" else ZIMAGE, good, bad, sample)

    def _store_keyframes(self, video_id: uuid.UUID, jobs: dict[str, KeyframeJob], r: KeyframeResult) -> StageResult:
        d = self.d
        ok = gem = 0
        errors: list[str | None] = []
        with self.db.transaction():
            for o in r.results:
                if o.uri is None:
                    errors.append(o.error)
                    self.db.execute("UPDATE shot SET status = 'FAILED', error = %(e)s, updated_at = now() WHERE id = %(s)s",
                                    {"e": o.error, "s": uuid.UUID(o.shot_id)})
                    continue
                job = jobs[o.shot_id]
                is_gemini = o.provider == "gemini_api"
                flux = job.provider == POD and job.pod_model == "flux2-klein"
                prompt = job.prompt + ("" if not o.references else " | reference photos: " + "; ".join(o.references))
                asset_id = d.assets.create(NewAsset(
                    video_id, "image", o.uri, "image/png" if o.uri.endswith(".png") else "image/jpeg", job.width, job.height, None, o.provider,
                    o.model, prompt, o.seed, lic.LICENCE_GEMINI if is_gemini else lic.LICENCE_FLUX2_KLEIN if flux else lic.LICENCE_ZIMAGE, True))
                self.db.execute("UPDATE asset SET shot_id = %(s)s WHERE id = %(a)s", {"s": uuid.UUID(o.shot_id), "a": asset_id})
                self.db.execute("UPDATE shot SET keyframe_asset_id = %(a)s, status = 'READY', error = NULL, updated_at = now() WHERE id = %(s)s",
                                {"a": asset_id, "s": uuid.UUID(o.shot_id)})
                if is_gemini:
                    gem += 1
                    d.ledger.record(video_id, GEMINI, d.pricing.image_inr("1K"), Decimal(1), "image", True, f"KEYFRAME hero ({o.model})")
                ok += 1
            if r.modal_gpu_seconds > 0:
                d.ledger.record(video_id, "modal_l40s", d.pricing.modal_gpu_inr("L40S", r.modal_gpu_seconds), Decimal(str(r.modal_gpu_seconds)),
                                "gpu_seconds", True, "KEYFRAMES Z-Image (free Modal credit; excludes container start-up)")
            if ok == 0:
                raise RuntimeError("No keyframes were produced: " + " | ".join(_j(e) for e in errors))
        on_rented_pod = any(j.provider == POD for j in jobs.values())
        standard_label = "FLUX.2 klein on a rented pod" if on_rented_pod else "Z-Image on Modal"  # the label says where the pictures really came from
        failed = "" if not errors else f"; {len(errors)} failed: {_j(errors[0])}"
        summary = f"{ok}/{len(jobs)} keyframes ({gem} hero via Nano Banana, {ok - gem} via {standard_label}){failed}."
        return StageResult.of(summary, json.dumps({"ok": ok, "failed": errors}, separators=(",", ":")))


class StockStage(StageSupport):
    """Stock footage lane (docs/stock-footage-lane.md). A found clip is stored like picked NASA footage (shot.footage_asset_id), so keyframes,
    clip generation, upscale and smoothing skip it and assembly uses it. Everything is off unless projectv.video.stock-lane is true."""

    def __init__(self, d: ProductionDeps, lane: bool = False, pexels: bool = False, pixabay: bool = False, commons: bool = False,
                 archive: bool = False):
        super().__init__(d)
        self.lane, self.pexels, self.pixabay, self.commons, self.archive = lane, pexels, pixabay, commons, archive

    def sources(self) -> list[str]:
        return [n for n, on in (("pexels", self.pexels), ("pixabay", self.pixabay), ("commons", self.commons), ("archive", self.archive)) if on]

    def enabled(self) -> bool:
        return self.lane and bool(self.sources())

    def requests(self, video_id: uuid.UUID) -> list[StockRequest]:
        """One request per stock shot that has no clip yet; empty when the lane (or every source) is off."""
        out: list[StockRequest] = []
        if not self.enabled():
            return out
        plan = self.plan(video_id)
        for s in self.shots(video_id):
            if not stock_shot(s) or s["footage_asset_id"] is not None:
                continue
            p = json.loads(s["prompt_json"])
            query = p.get("stockQuery")
            text = str(query if query is not None else (s["description"] if s["description"] is not None else "")).strip()
            dur = s["duration_target_sec"]
            seconds = float(dur) if isinstance(dur, (int, float, Decimal)) else 4.0
            out.append(StockRequest(str(video_id), str(s["id"]), int(s["ord"]) + 1, text, plan.aspectRatio, seconds, self.sources(), self.d.text_model))
        return out

    def save(self, video_id: uuid.UUID, req: StockRequest, r: StockResult | None) -> StageResult:
        """A clip: stored with provenance and attached to the shot. No clip: the shot goes back to the AI lane, never fails."""
        shot_id = uuid.UUID(req.shot_id)
        shot = next((s for s in self.shots(video_id) if s["id"] == shot_id), None)
        if shot is None:
            return StageResult.of("Shot is no longer part of this video.", None)
        p = json.loads(shot["prompt_json"])
        with self.db.transaction():
            if r is None or not r.found or r.uri is None or r.provenance is None:
                reason = "no result" if r is None or r.reason is None else r.reason
                p["lane"] = "ai_video"
                p["stockFallback"] = reason
                self._save_prompt(shot_id, p)
                return StageResult.of(f"Shot {req.shot_number}: no stock clip ({reason}); using the AI lane.", None)
            pv: StockProvenance = r.provenance
            is_commons = pv.source == "commons"
            licence = (lic.LICENCE_COMMONS if is_commons else lic.LICENCE_ARCHIVE if pv.source == "archive"
                       else lic.LICENCE_PEXELS if pv.source == "pexels" else lic.LICENCE_PIXABAY)
            provider = "wikimedia" if is_commons else "internet_archive" if pv.source == "archive" else pv.source
            asset_id = self.d.assets.create(NewAsset(
                video_id, "video", r.uri, "video/mp4", r.width, r.height, r.duration_sec, provider, f"stock footage {pv.source} {pv.source_id}",
                req.description + " | queries: " + ", ".join(r.queries or []), None, licence, False))
            fetched = pv.fetched_at if pv.fetched_at is not None else datetime.now(UTC).isoformat()
            self.db.execute(
                "UPDATE provenance SET source_url = %(u)s, source_id = %(i)s, author = %(a)s, attribution_text = %(t)s, credit_required = %(c)s, "
                "fetched_at = CAST(%(f)s AS timestamptz) WHERE asset_id = %(asset)s",
                {"u": pv.page_url, "i": pv.source_id, "a": pv.author, "t": pv.attribution, "c": pv.credit_required, "f": fetched, "asset": asset_id})
            self.db.execute("UPDATE asset SET shot_id = %(s)s, clip_kind = 'footage', sha256 = %(h)s WHERE id = %(a)s",
                            {"s": shot_id, "h": None if _blank(pv.sha256) else pv.sha256, "a": asset_id})
            self.db.execute("UPDATE shot SET footage_asset_id = %(a)s, clip_status = 'READY', clip_error = NULL, updated_at = now() WHERE id = %(s)s",
                            {"a": asset_id, "s": shot_id})
            p.pop("stockFallback", None)
            self._save_prompt(shot_id, p)
        credit = " Credit required (see credits.txt)." if pv.credit_required else ""
        return StageResult.of(f"Shot {req.shot_number}: real stock clip from {pv.source} ({fixed(r.duration_sec or 0.0, 1)}s).{credit}", None)

    def _save_prompt(self, shot_id: uuid.UUID, p: dict[str, Any]) -> None:
        self.db.execute("UPDATE shot SET prompt_json = CAST(%(p)s AS jsonb), updated_at = now() WHERE id = %(s)s",
                        {"p": json.dumps(p, separators=(",", ":")), "s": shot_id})

