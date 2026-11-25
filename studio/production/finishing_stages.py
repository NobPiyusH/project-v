"""Free real footage, the edit (assembly) and the GPU finishing stages (smooth motion, sharpness). Ports of FootageStage, AssemblyStage,
FinishStage and SharpenStage."""

from __future__ import annotations

import json
import math
import uuid
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any

from studio.agents.jfmt import fixed, jround
from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.media.types import (
    AssemblyLine,
    AssemblyRequest,
    AssemblyResult,
    AssemblyShot,
    ClipEstimate,
    FootageCandidate,
    FootageFetchRequest,
    FootageFetchResult,
    FootageQuery,
    FootageSearchRequest,
    FootageSearchResponse,
    SmoothJob,
    SmoothRequest,
    SmoothResult,
)
from studio.media.wire import from_json, to_json
from studio.production import timing as nt
from studio.production.support import ProductionDeps, StageSupport, diagram_shot, round3
from studio.workflow.types import StageResult


def _hu(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _compact(o: Any) -> str:
    return json.dumps(o, separators=(",", ":"))


def _blank(s: str | None) -> bool:
    return s is None or not s.strip()


def _num(o: Any, default: float) -> float:
    return float(o) if isinstance(o, (int, float, Decimal)) and not isinstance(o, bool) else default


class FootageStage(StageSupport):
    """Free real footage (NASA Image and Video Library, Wikimedia Commons) for shots where it shows the line better than a generated clip.
    Quality first: the studio only SUGGESTS candidates (the Director proposes a search; the Library match stage runs it for free); the owner
    picks. A picked clip replaces that shot's generated clip, so no GPU time is spent on it."""

    def search_request(self, video_id: uuid.UUID, shot_ids: list[uuid.UUID] | None, override_query: str | None) -> FootageSearchRequest:
        """Searches for every shot the Director marked with a footage query (or only shot_ids)."""
        queries: list[FootageQuery] = []
        for s in self.shots(video_id):
            if shot_ids is not None and s["id"] not in shot_ids:
                continue
            fq = json.loads(s["prompt_json"]).get("footageQuery")
            q = override_query.strip() if not _blank(override_query) else ("" if fq is None else str(fq).strip())
            if q:
                queries.append(FootageQuery(str(s["id"]), q))
        return FootageSearchRequest(queries)

    def save_candidates(self, video_id: uuid.UUID, r: FootageSearchResponse) -> StageResult:
        """Keeps the candidates on each shot (shown on the dashboard). Free: nothing is downloaded or used yet."""
        shots_with = total = 0
        with self.db.transaction():
            for res in r.results:
                shot = self._shot(video_id, uuid.UUID(res.shot_id))
                if shot is None:
                    continue
                p = json.loads(shot["prompt_json"])
                p["footageQuery"] = res.query
                p["footageCandidates"] = to_json(res.candidates or [])
                if res.error is not None:
                    p["footageError"] = res.error
                else:
                    p.pop("footageError", None)
                self._save_prompt(shot["id"], p)
                n = len(res.candidates or [])
                total += n
                shots_with += n > 0
        if not r.results:
            summary = "No shots asked for real footage."
        else:
            summary = (f"Found {total} free real clip{'' if total == 1 else 's'} (NASA, Wikimedia Commons) for {shots_with} of {len(r.results)} shots "
                       "that could use real footage. Pick any to use it instead of a generated clip (free; no GPU).")
        return StageResult.of(summary, _compact({"shots": len(r.results), "candidates": total}))

    def fetch_request(self, video_id: uuid.UUID, shot_id: uuid.UUID, nasa_id: str, start_sec: float | None) -> FootageFetchRequest:
        shot = self._shot(video_id, shot_id)
        if shot is None:
            raise RuntimeError("That shot is not part of this video")
        plan = self.plan(video_id)
        return FootageFetchRequest(str(video_id), str(shot_id), int(shot["ord"]) + 1, nasa_id, start_sec,
                                   _num(shot["duration_target_sec"], 4.0), plan.aspectRatio)

    def save_footage(self, video_id: uuid.UUID, req: FootageFetchRequest, r: FootageFetchResult) -> StageResult:
        shot = self._shot(video_id, uuid.UUID(req.shot_id))
        c = next((x for x in self._candidates(shot) if req.nasa_id == x.nasa_id), None)
        commons = req.nasa_id.startswith("commons:")
        source = "Wikimedia Commons" if commons else "NASA"
        if commons:
            credit = "Wikimedia Commons" if c is None or c.credit is None else c.credit
        else:
            credit = "NASA" + ("" if c is None or _blank(c.credit) else " / " + c.credit)
        # Commons: the file's own licence (public domain, CC0 or CC BY) and author are kept in the credit line.
        with self.db.transaction():
            asset_id = self.d.assets.create(NewAsset(
                video_id, "video", r.uri, "video/mp4", r.width, r.height, r.duration_sec, "wikimedia" if commons else "nasa",
                f"{source} {req.nasa_id}", f"{req.nasa_id if c is None else c.title} | credit: {credit} | from {r.start_sec}s of {r.source_url}",
                None, lic.LICENCE_COMMONS if commons else lic.LICENCE_NASA, False))
            self.db.execute("UPDATE asset SET shot_id = %(s)s, clip_kind = 'footage' WHERE id = %(a)s", {"s": uuid.UUID(req.shot_id), "a": asset_id})
            self.db.execute("UPDATE shot SET footage_asset_id = %(a)s, clip_status = 'READY', clip_error = NULL, updated_at = now() WHERE id = %(s)s",
                            {"a": asset_id, "s": uuid.UUID(req.shot_id)})
        soft = ""
        if r.upscale is not None and r.upscale > 1.5:
            soft = f" Note: the source is {r.source_width}x{r.source_height}, so this crop is enlarged {fixed(r.upscale, 1)}x and may look soft."
        return StageResult.of(f"Shot {req.shot_number} now uses real {source} footage ({fixed(r.duration_sec, 1)}s, credit: {credit}).{soft}",
                              _compact(to_json(r)))

    def clear_footage(self, shot_id: uuid.UUID) -> None:
        """Back to the generated clip for this shot."""
        self.db.execute("UPDATE shot SET footage_asset_id = NULL, updated_at = now() WHERE id = %(s)s", {"s": shot_id})

    def _shot(self, video_id: uuid.UUID, shot_id: uuid.UUID) -> dict[str, Any] | None:
        return next((s for s in self.shots(video_id) if s["id"] == shot_id), None)

    def _candidates(self, shot: dict[str, Any] | None) -> list[FootageCandidate]:
        if shot is None:
            return []
        c = json.loads(shot["prompt_json"]).get("footageCandidates")
        return [from_json(FootageCandidate, x) for x in c] if c else []

    def _save_prompt(self, shot_id: uuid.UUID, p: dict[str, Any]) -> None:
        self.db.execute("UPDATE shot SET prompt_json = CAST(%(p)s AS jsonb), updated_at = now() WHERE id = %(s)s", {"p": _compact(p), "s": shot_id})


class AssemblyStage(StageSupport):
    """M2c part 1, the edit: every shot's chosen clip on the narration timeline, voice + ducked music mastered to -14 LUFS, word-timed captions,
    and a Premiere package. Runs on the local worker (free). Every shot must have real moving video (owner-picked footage, else its final, else
    its draft): a shot without one stops the stage before anything is rendered, because a panned still is never acceptable output (rule 10)."""

    def __init__(self, d: ProductionDeps, output_height: int = 0, quality_gate: bool = True):
        super().__init__(d)
        self.quality_gate = quality_gate  # tiers 1-2 of tools/qualitygate after the edit (judge stays off); QUALITY_GATE_ENABLED=0 turns it off
        self.output_height = output_height  # projectv.video.output-height: 0 = 1080p master (default); 1440 = 2560x1440 (16:9)

    def assembly_request(self, video_id: uuid.UUID) -> AssemblyRequest:
        plan = self.plan(video_id)
        rows = self.shots(video_id)
        if not rows:
            raise RuntimeError("There is no shot plan yet")
        uris = self._clip_uris(video_id)
        # Finished versions (smoothed, sharpened) by asset id -> the exact clip each was made from.
        made_from = {str(r["id"]): str(r["derived_from"]) for r in self.db.rows(
            "SELECT id, derived_from FROM asset WHERE video_id = %(v)s AND clip_kind IN ('smooth', 'sharp')", {"v": video_id})}
        shots: list[AssemblyShot] = []
        missing: list[str] = []
        line_start: dict[int, float] = {}
        end = 0.0
        for s in rows:
            n = int(s["ord"]) + 1
            p = json.loads(s["prompt_json"])
            st = p.get("startSec")
            start = float(st) if isinstance(st, (int, float)) and not isinstance(st, bool) else end
            dur = _num(s["duration_target_sec"], 3.0)
            kind = ("footage" if s["footage_asset_id"] is not None else "final" if s["final_clip_asset_id"] is not None
                    else "draft" if s["draft_clip_asset_id"] is not None else None)
            asset_id = None if kind is None else str(s[{"footage": "footage_asset_id", "final": "final_clip_asset_id"}.get(kind, "draft_clip_asset_id")])
            # The smoothed (RIFE 24 fps) version is used only while it was made from the clip still in use.
            if kind is not None and kind != "footage" and s["smooth_clip_asset_id"] is not None \
                    and asset_id == made_from.get(str(s["smooth_clip_asset_id"])):
                asset_id = str(s["smooth_clip_asset_id"])
                kind += "+smooth"
            # The sharpened (SeedVR2 1080p) version likewise, only while made from the clip now in use.
            if kind is not None and kind != "footage" and s["sharp_clip_asset_id"] is not None \
                    and asset_id == made_from.get(str(s["sharp_clip_asset_id"])):
                asset_id = str(s["sharp_clip_asset_id"])
                kind += "+sharp"
            if kind is None or uris.get(asset_id) is None:
                missing.append(f"shot {n}")
                continue
            sfx = p.get("sfxQuery")
            shots.append(AssemblyShot(n, uris[asset_id], kind, round3(start), round3(dur), s["overlay_text"],
                                      sfx if isinstance(sfx, str) and sfx.strip() else None))
            li = p.get("lineIndex")
            if isinstance(li, (int, float)) and not isinstance(li, bool):
                line_start.setdefault(int(li), start)
            end = max(end, start + dur)
        if missing:
            raise RuntimeError(f"{len(missing)} shot{' has' if len(missing) == 1 else 's have'} no moving video yet ({', '.join(missing[:8])}"
                               f"{', ...' if len(missing) > 8 else ''}). Make their drafts (or pick real footage) first.")
        script = self.latest_script(video_id)
        tempo = 1.0 if script is None or script.tempo is None else script.tempo
        narration: list[AssemblyLine] = []
        speech_end = 0.0
        for p in nt.place(script, line_start):
            narration.append(AssemblyLine(p.line.audio_uri, p.start_sec, tempo, p.line.text, 0.0 if p.line.duration_sec is None else p.line.duration_sec))
            speech_end = p.end_sec
        # Never cut words: if the voice runs past the pictures, the last shot holds longer.
        needed = speech_end + nt.TAIL
        if needed > end and shots:
            last = shots.pop()
            shots.append(AssemblyShot(last.n, last.uri, last.kind, last.start_sec, round3(last.duration_sec + needed - end), last.overlay_text,
                                      last.sfx_query))
            end = needed
        music = self.db.rows("SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = %(v)s", {"v": video_id})
        return AssemblyRequest(str(video_id), plan.title, plan.aspectRatio, plan.language, plan.narration != "none", shots, narration,
                               music[0]["uri_hot"] if music else None, round3(end), self.output_height, self.stock_credits(video_id), self.quality_gate)

    def save_master(self, video_id: uuid.UUID, r: AssemblyResult) -> StageResult:
        # Sound-effect credits (Freesound CC BY needs the author) travel with the master for the video description.
        sfx = None if not r.sfx_credits else "Sound effects: " + "; ".join(r.sfx_credits)
        a = self.d.assets
        with self.db.transaction():
            master = a.create(NewAsset(video_id, "video", r.master_uri, "video/mp4", r.width, r.height, r.duration_sec, "projectv",
                                       "ffmpeg assembly (rough cut)", sfx, None, lic.LICENCE_OWN_RENDER, False))
            pack = a.create(NewAsset(video_id, "timeline", r.package_uri, "application/zip", None, None, None, "projectv",
                                     "OpenTimelineIO FCP7 XML + media", None, None, lic.LICENCE_OWN_RENDER, False))
            captions = None if r.srt_uri is None else a.create(NewAsset(
                video_id, "subtitle", r.srt_uri, "application/x-subrip", None, None, None, "projectv", "Whisper word timings + script text", None, None,
                lic.LICENCE_OWN_RENDER, False))
            if r.credits_uri is not None:
                a.create(NewAsset(video_id, "doc", r.credits_uri, "text/plain", None, None, None, "projectv",
                                  "credits.txt (stock footage and sound credits for the description)", None, None, lic.LICENCE_OWN_RENDER, False))
            self.db.execute("UPDATE video SET master_asset_id = %(m)s, master_package_asset_id = %(p)s, master_captions_asset_id = %(c)s, "
                            "updated_at = now() WHERE id = %(v)s", {"m": master, "p": pack, "c": captions, "v": video_id})
        slowed = sum(1 for f in r.fits if str(f.get("fit")).startswith("slowed"))
        slow_mo = sum(1 for f in r.fits if "slow-motion" in str(f.get("fit")))
        fits = ""
        if slowed:
            fits = (f"; {slowed} shot{'' if slowed == 1 else 's'} slowed to fill {'its' if slowed == 1 else 'their'} slot{'' if slowed == 1 else 's'}"
                    + (f" ({slow_mo} noticeably)" if slow_mo > 0 else ""))
        summary = (f"Master ready: {fixed(r.duration_sec, 0)}s, {r.width}x{r.height} @ {r.fps} fps, voice and music mastered to -14 LUFS, "
                   f"{r.caption_chunks} caption{'' if r.caption_chunks == 1 else 's'} ({r.lines_timed_by_whisper} lines word-timed by Whisper){fits}. "
                   "Premiere package and SRT included. Part 2 (GPU) adds smooth motion and upscaling.")
        return StageResult.of(summary, _compact({"masterAssetId": str(master), "packageAssetId": str(pack), "fits": r.fits}))

    def stock_credits(self, video_id: uuid.UUID) -> list[str]:
        """Credit lines for stock clips that need credit (CC BY, Pexels); empty when none: no credits file is made then."""
        return [r["attribution_text"] for r in self.db.rows(
            "SELECT DISTINCT p.attribution_text FROM shot sh JOIN scene sc ON sc.id = sh.scene_id JOIN asset a ON a.id = sh.footage_asset_id "
            "JOIN provenance p ON p.id = a.provenance_id WHERE sc.video_id = %(v)s AND p.credit_required AND p.attribution_text IS NOT NULL "
            "ORDER BY p.attribution_text", {"v": video_id})]

    def _clip_uris(self, video_id: uuid.UUID) -> dict[str, str]:
        return {str(r["id"]): r["uri_hot"] for r in self.db.rows(
            "SELECT id, uri_hot FROM asset WHERE video_id = %(v)s AND kind = 'video' AND shot_id IS NOT NULL", {"v": video_id})}


class FinishStage(StageSupport):
    """M2c part 2a, smooth motion: every shot's chosen generated clip (final, else draft) is re-timed from Wan's 16 fps to a true 24 fps with
    Practical-RIFE in-between frames, on one L4 (projectv-finish). NASA footage is already smooth and is left alone. A smoothed clip remembers
    the exact clip it was made from, so the edit only uses it while that clip is still the one in use."""

    TARGET_FPS = 24.0
    SECONDS_PER_FRAME = 0.05  # L4 seconds per output frame (RIFE v4.25 + encode at 480-720p) and the container start, both estimates
    START_SECONDS = 45.0

    def __init__(self, d: ProductionDeps, public_url: str = "http://127.0.0.1:8080", auto_approve_inr: Decimal = Decimal(0)):
        super().__init__(d)
        self.public_url, self.auto_approve_inr = public_url, auto_approve_inr

    def jobs(self, video_id: uuid.UUID) -> list[SmoothJob]:
        out: list[SmoothJob] = []
        for s in self.db.rows(
                """
                SELECT s.id, s.ord, s.duration_target_sec, s.footage_asset_id, COALESCE(s.final_clip_asset_id, s.draft_clip_asset_id) AS chosen,
                       c.uri_hot AS chosen_uri, c.fps AS chosen_fps, sm.derived_from AS smoothed_from
                FROM shot s JOIN scene sc ON sc.id = s.scene_id
                LEFT JOIN asset c ON c.id = COALESCE(s.final_clip_asset_id, s.draft_clip_asset_id)
                LEFT JOIN asset sm ON sm.id = s.smooth_clip_asset_id
                WHERE sc.video_id = %(v)s ORDER BY s.ord
                """, {"v": video_id}):
            chosen = s["chosen"]
            if s["footage_asset_id"] is not None or chosen is None or s["chosen_uri"] is None:
                continue  # real footage is already smooth; no clip yet = nothing to smooth
            if isinstance(s["chosen_fps"], (int, float, Decimal)) and float(s["chosen_fps"]) >= self.TARGET_FPS - 0.1:
                continue  # already 24 fps (Gemini Omni): nothing to re-time
            if chosen == s["smoothed_from"]:
                continue  # already smoothed from exactly this clip
            out.append(SmoothJob(str(s["id"]), int(s["ord"]) + 1, str(chosen), s["chosen_uri"], 16.0))
        return out

    def estimate_inr(self, jobs: list[SmoothJob]) -> Decimal:
        if not jobs:
            return Decimal(0)
        # Output frames ~ 5 s x 24 per clip at most; drafts are usually shorter.
        seconds = self.START_SECONDS + len(jobs) * 5 * self.TARGET_FPS * self.SECONDS_PER_FRAME
        return _hu(self.d.pricing.modal_gpu_inr("L4", seconds), 2)

    def estimate(self, video_id: uuid.UUID) -> ClipEstimate:
        jobs = self.jobs(video_id)
        inr = self.estimate_inr(jobs)
        if not jobs:
            summary = "Every clip is already smooth (or is real footage)."
        else:
            left = _hu(max(self.d.budget.gpu_remaining(), Decimal(0)), 0)
            summary = (f"Ready to smooth {len(jobs)} clip{'' if len(jobs) == 1 else 's'} to 24 fps on one Modal L4: about "
                       f"₹{inr.quantize(Decimal(1), rounding=ROUND_UP)} and {max(1, jround((self.START_SECONDS + len(jobs) * 6) / 60))} min. "
                       f"₹{left} of this month's GPU cap left.")
        return ClipEstimate("smooth", len(jobs), 0, inr, float(inr) / self.d.pricing.fx(), 0, bool(jobs) and inr > self.auto_approve_inr, summary)

    def request(self, video_id: uuid.UUID) -> SmoothRequest:
        jobs = self.jobs(video_id)
        inr = self.estimate_inr(jobs)
        self.d.budget.require_gpu(inr, f"{len(jobs)} clips to smooth", video_id)
        return SmoothRequest(str(video_id), self.public_url, self.TARGET_FPS, jobs, inr)

    def save(self, video_id: uuid.UUID, req: SmoothRequest, r: SmoothResult) -> StageResult:
        ok, gpu, errors = self._store(video_id, r, "modal_rife", "Practical-RIFE v4.25 16->24 fps", lic.LICENCE_RIFE, "smooth", "smooth_clip_asset_id")
        billed = gpu + (self.START_SECONDS if r.containers > 0 else 0)
        if billed > 0:
            self.d.ledger.record(video_id, "modal_l4", self.d.pricing.modal_gpu_inr("L4", billed), Decimal(str(billed)), "gpu_seconds", True,
                                 f"SMOOTH {ok} clips to 24 fps (RIFE, incl. estimated start-up)")
        if ok == 0 and req.jobs and not r.stopped:
            raise RuntimeError("No clips were smoothed: " + " | ".join(str(e) for e in errors))
        where = "on the laptop GPU (free)" if gpu <= 0 else f"in {fixed(gpu, 0)} s of L4 time"
        failed = "" if not errors else f"; {len(errors)} failed: {errors[0]}"
        return StageResult.of(f"{ok}/{len(req.jobs)} clips smoothed to a true 24 fps (RIFE in-between frames) {where}{failed}.",
                              _compact({"ok": ok, "failed": errors}))

    def _store(self, video_id: uuid.UUID, r: SmoothResult, provider: str, model: str, licence: uuid.UUID, clip_kind: str,
               column: str) -> tuple[int, float, list[Any]]:
        ok, gpu = 0, 0.0
        errors: list[Any] = []
        with self.db.transaction():
            for o in r.clips:
                gpu += o.gpu_seconds
                if o.uri is None:
                    if o.error != "stopped":
                        errors.append(o.error)
                    continue
                shot = uuid.UUID(o.shot_id)
                asset = self.d.assets.create(NewAsset(video_id, "video", o.uri, "video/mp4", None, None, o.frames / o.fps if o.fps > 0 else None,
                                                      provider, model, None, None, licence, True))
                self.db.execute(f"UPDATE asset SET shot_id = %(s)s, clip_kind = '{clip_kind}', derived_from = %(d)s WHERE id = %(a)s",
                                {"s": shot, "d": uuid.UUID(o.source_asset_id), "a": asset})
                self.db.execute(f"UPDATE shot SET {column} = %(a)s, updated_at = now() WHERE id = %(s)s", {"a": asset, "s": shot})
                ok += 1
        return ok, gpu, errors


class SharpenStage(StageSupport):
    """M2c part 2b, sharpness: every shot's clip in use (its fresh smoothed clip, else its chosen final/draft) is restored to a 1080 px short side
    by SeedVR2 7B on one H100 (projectv-upscale). NASA footage is left alone. Like smoothing, a sharpened clip remembers the exact clip it was made
    from, so the edit uses it only while that clip is still in use. Requests and results reuse the smoothing records."""

    # H100 seconds per 2-4 s clip in one-batch "fit" mode (18 Nov A/B: 2 clips in 136 s incl. one model load), ~30 s to load the model per group of
    # 8 clips, plus container start and the 30 s idle before it shuts down. Re-check against the next real video.
    SECONDS_PER_CLIP = 60.0
    SECONDS_PER_GROUP = 30.0
    START_SECONDS = 90.0
    GROUP = 8

    def __init__(self, d: ProductionDeps, finish: FinishStage, public_url: str = "http://127.0.0.1:8080", auto_approve_inr: Decimal = Decimal(0)):
        super().__init__(d)
        self.finish, self.public_url, self.auto_approve_inr = finish, public_url, auto_approve_inr

    def jobs(self, video_id: uuid.UUID) -> list[SmoothJob]:
        out: list[SmoothJob] = []
        for s in self.db.rows(
                """
                SELECT s.id, s.ord, s.footage_asset_id, s.prompt_json::text AS prompt_json, c.id AS chosen, c.uri_hot AS chosen_uri,
                       c.fps AS chosen_fps, sm.id AS smooth, sm.uri_hot AS smooth_uri, sm.derived_from AS smoothed_from,
                       sh.derived_from AS sharpened_from
                FROM shot s JOIN scene sc ON sc.id = s.scene_id
                LEFT JOIN asset c ON c.id = COALESCE(s.final_clip_asset_id, s.draft_clip_asset_id)
                LEFT JOIN asset sm ON sm.id = s.smooth_clip_asset_id
                LEFT JOIN asset sh ON sh.id = s.sharp_clip_asset_id
                WHERE sc.video_id = %(v)s ORDER BY s.ord
                """, {"v": video_id}):
            chosen = s["chosen"]
            if s["footage_asset_id"] is not None or diagram_shot(s) or chosen is None or s["chosen_uri"] is None:
                continue  # real footage and drawn diagrams (native vector render) are left as they are; no clip yet = nothing to sharpen
            smooth_fresh = s["smooth"] is not None and chosen == s["smoothed_from"] and s["smooth_uri"] is not None
            source = s["smooth"] if smooth_fresh else chosen
            if source == s["sharpened_from"]:
                continue  # already sharpened from exactly the clip in use
            fps = FinishStage.TARGET_FPS if smooth_fresh else _num(s["chosen_fps"], 16.0)
            out.append(SmoothJob(str(s["id"]), int(s["ord"]) + 1, str(source), s["smooth_uri"] if smooth_fresh else s["chosen_uri"], fps))
        return out

    @classmethod
    def gpu_seconds(cls, clips: int) -> float:
        return 0.0 if clips == 0 else cls.START_SECONDS + clips * cls.SECONDS_PER_CLIP + math.ceil(clips / cls.GROUP) * cls.SECONDS_PER_GROUP

    def estimate_inr(self, clips: int) -> Decimal:
        return _hu(self.d.pricing.modal_gpu_inr("H100", self.gpu_seconds(clips)), 2)

    def estimate(self, video_id: uuid.UUID) -> ClipEstimate:
        """Before smoothing runs, a shot about to be smoothed will need sharpening too (its clip in use changes), so the estimate counts those
        as well; the request, made after smoothing, sees the real clips."""
        shots = {j.shot_id for j in self.jobs(video_id)} | {j.shot_id for j in self.finish.jobs(video_id)}
        n = len(shots)
        inr = self.estimate_inr(n)
        if n == 0:
            summary = "Every clip is already sharp at 1080p (or is real footage)."
        else:
            left = _hu(max(self.d.budget.gpu_remaining(), Decimal(0)), 0)
            summary = (f"Ready to sharpen {n} clip{'' if n == 1 else 's'} to 1080p with SeedVR2 on one Modal H100: about "
                       f"₹{inr.quantize(Decimal(1), rounding=ROUND_UP)} and {max(1, jround(self.gpu_seconds(n) / 60))} min. ₹{left} of this month's GPU cap left.")
        return ClipEstimate("sharp", n, self.gpu_seconds(n), inr, float(inr) / self.d.pricing.fx(), self.gpu_seconds(n) / 60,
                            n > 0 and inr > self.auto_approve_inr, summary)

    def request(self, video_id: uuid.UUID) -> SmoothRequest:
        jobs = self.jobs(video_id)
        inr = self.estimate_inr(len(jobs))
        self.d.budget.require_gpu(inr, f"{len(jobs)} clips to sharpen", video_id)
        return SmoothRequest(str(video_id), self.public_url, FinishStage.TARGET_FPS, jobs, inr)

    def save(self, video_id: uuid.UUID, req: SmoothRequest, r: SmoothResult) -> StageResult:
        ok, gpu, errors = self.finish._store(video_id, r, "modal_seedvr2", "SeedVR2 7B fp16 restore to 1080p", lic.LICENCE_SEEDVR2, "sharp",
                                             "sharp_clip_asset_id")
        # Group times already include each group's model load; add the container start and the idle tail.
        billed = gpu + (self.START_SECONDS if r.containers > 0 else 0)
        if billed > 0:
            self.d.ledger.record(video_id, "modal_h100", self.d.pricing.modal_gpu_inr("H100", billed), Decimal(str(billed)), "gpu_seconds", True,
                                 f"SHARPEN {ok} clips to 1080p (SeedVR2, incl. estimated start-up)")
        if ok == 0 and req.jobs and not r.stopped:
            raise RuntimeError("No clips were sharpened: " + " | ".join(str(e) for e in errors))
        failed = "" if not errors else f"; {len(errors)} failed: {errors[0]}"
        return StageResult.of(f"{ok}/{len(req.jobs)} clips sharpened to 1080p (SeedVR2) in {fixed(gpu, 0)} s of H100 time{failed}.",
                              _compact({"ok": ok, "failed": errors}))
