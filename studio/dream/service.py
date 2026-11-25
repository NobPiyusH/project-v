"""Dream Shorts: the work behind each step. Port of DreamService. Reuses the studio's engines unchanged (the media activities, the asset
store, the router, the budget caps) and keeps its own records in dream_short / dream_shot. Every asset gets provenance, every paid job a
cost entry, every provider choice a routing decision, all linked to the Short (rule 6).

Differences from Java, on purpose: the writer's LLM calls (and the similar-Short check) run BEFORE the step's database transaction opens,
so their cost entries are kept even if the step then fails (same choice as the S5 stages)."""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from studio.agents.jfmt import fixed, string_hash
from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.db import Db
from studio.dream import branches, idea_packs, looks, svi
from studio.dream.evidence import DreamEvidence
from studio.dream.gpu import DreamGpu
from studio.dream.lessons import DreamLessons
from studio.dream.llm_types import DreamWorld, PlannedDreamShot
from studio.dream.looks import Look
from studio.dream.music import BED, DreamMusic
from studio.dream.scout import DreamScout, Scouted
from studio.dream.settings import DreamSettings, clean_picture_model
from studio.dream.wire_types import (
    DreamBedRequest,
    DreamBedResult,
    DreamCheckRequest,
    DreamCheckResult,
    DreamEstimate,
    DreamFinishClip,
    DreamFinishRequest,
    DreamFinishResult,
    DreamScoutRequest,
    DreamScoutResult,
)
from studio.ledger.budget_guard import GEMINI, BudgetExceededError
from studio.media.types import (
    ClipJob,
    ClipRequest,
    ClipResult,
    KeyframeJob,
    KeyframeRequest,
    KeyframeResult,
    MusicRequest,
    MusicResult,
)
from studio.media.wire import to_json
from studio.production import redo_stop_rule
from studio.routing.router import Candidate

log = logging.getLogger("studio.dream")

ZIMAGE = "modal_zimage"
WAN = "modal_wan"
POD = "gpu_pod"  # the rented-pod route's provider id (pictures and clips)
FLUX2_KLEIN_NAME = "FLUX.2 klein 4B"
LYRIA = "gemini_lyria"
CHECK_ESTIMATE_INR = Decimal("0.50")  # quality check: a ~360p copy watched by Gemini (estimate until measured)
COST_OK_GRACE = timedelta(minutes=1)
SHOTS_FIELD = ("\"shots\": [{\"scene\": what this shot shows (one or two sentences, concrete), \"motion\": "
               "the camera move and\nthe small motion inside the scene}]")
GPU_SOON = ["SCOUT:RUNNING", "SCOUT:WAITING_REVIEW", "WRITE:RUNNING", "WRITE:WAITING_REVIEW", "PICTURES:RUNNING", "PICTURES:WAITING_BATCH",
            "PICTURES:WAITING_REVIEW", "CLIPS:RUNNING", "CLIPS:WAITING_BATCH"]
FREE_STEPS = ["SCOUT", "WRITE"]
BEFORE_CLIPS = ["SCOUT", "WRITE", "PICTURES"]

_WS = __import__("re").compile(r"\s+")


def _squash(s: str) -> str:
    return _WS.sub(" ", s)


def _hu(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _blank_to_none(s: str | None) -> str | None:
    return None if s is None or not s.strip() else s.strip()


def uuid_hash(u: uuid.UUID) -> int:
    """Java UUID.hashCode(): the two halves xor-ed, then the two 32-bit halves of that xor-ed."""
    hilo = (u.int >> 64) ^ (u.int & 0xFFFFFFFFFFFFFFFF)
    h = ((hilo >> 32) ^ hilo) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def storage_id(dream_id: uuid.UUID) -> str:
    """The id the media worker files things under (videos/<id>/...) and reports clip progress / checks Stop with."""
    return str(dream_id)


def picture_model_of(d: dict[str, Any]) -> str:
    return clean_picture_model(d.get("picture_model"), "zimage")


def clip_kind(quality: str | None) -> str:
    return "wan.fast." + ("720p" if quality == "720p" else "480p")


def continues(chain: bool, group: int, ord_: int) -> bool:
    """Whether shot ord (0-based) continues from the clip before it, instead of starting from a picture of its own."""
    return chain and ord_ > 0 and (group <= 0 or ord_ % group != 0)


def path_choice(d: dict[str, Any]) -> bool:
    """'Choose the path' is on and both options are written."""
    return d.get("path_choice") is True and _blank_to_none(d.get("path_a")) is not None and _blank_to_none(d.get("path_b")) is not None


def scene_seed(dream_id: uuid.UUID, d: dict[str, Any]) -> int:
    """The world's seed: inherited down an episode chain (scene_seed), else derived from the Short's id."""
    return int(d["scene_seed"]) if d.get("scene_seed") is not None else abs(uuid_hash(dream_id))


def picture_prompt(w: DreamWorld, s: PlannedDreamShot, lk: Look) -> str:
    hero = (w.hero or "").strip()
    return _squash(" ".join([(s.scene or "").strip(), "" if not hero else f"The same {hero} in every shot, exactly as described.",
                             "World:", (w.world or "").strip(), "Palette:", (w.palette or "").strip(), "Light:", (w.light or "").strip(),
                             "Style:", lk.picture_style]))


def motion_prompt(s: PlannedDreamShot, lk: Look) -> str:
    return _squash((s.motion or "").strip() + ". " + (s.scene or "").strip() + ". " + lk.motion_style)


def locked_motion_prompt(w: DreamWorld, s: PlannedDreamShot, lk: Look) -> str:
    """Locked scene: every clip restates the one world and the one camera path, so the shots stay the same scene."""
    path = (w.cameraPath or "").strip()
    return _squash(motion_prompt(s, lk) + ("" if not path else f" One continuous camera path for the whole video: {path}.")
                   + f" The same world throughout: {(w.world or '').strip()} Palette: {(w.palette or '').strip()} Light: {(w.light or '').strip()}")


class DreamService:
    def __init__(self, db: Db, llm: Any, assets: Any, pricing: Any, budget: Any, router: Any, clip_progress: Any, settings: DreamSettings,
                 gpu: DreamGpu | None = None, lessons: DreamLessons | None = None):
        self.db, self.llm, self.assets, self.pricing, self.budget, self.router = db, llm, assets, pricing, budget, router
        self.clip_progress = clip_progress
        self.settings = settings
        self.zimage_app, self.zimage_class = settings.zimage_app, settings.zimage_class
        self.wan_app, self.wan_class = settings.wan_app, settings.wan_class
        self.public_url = settings.public_url
        self.auto_approve_inr = settings.auto_approve_inr
        self.shot_seconds = settings.shot_seconds
        self.music_dir = settings.music_dir
        self.listen_model = settings.listen_model
        self.brief_model = settings.brief_model
        self.quality_check = settings.quality_check
        self.picture_model = settings.picture_model
        self.evidence = DreamEvidence(db)
        self.scout = DreamScout(self, db, llm, pricing, budget, router, self.evidence, settings)
        self.music = DreamMusic(self, db, assets, pricing, budget, router, self.evidence, settings)
        self.gpu = gpu if gpu is not None and gpu.enabled() else None  # None on the Modal route (the default)
        self.lessons_ = lessons  # optional so hand-built services in tests work without it

    def default_picture_model(self) -> str:
        return self.picture_model

    def lessons(self, target: str) -> str:
        return "" if self.lessons_ is None else self.lessons_.for_prompt(target)

    # ------------------------------------------------------------------------------------------- GPU pod rhythm

    def _warm_gpu(self, dream_id: uuid.UUID) -> None:
        # Collect first, rent later: in a batch the pod is rented when the Shorts reach their pictures (gpu_batch_ahead).
        if self.gpu is not None and (self.gpu.users_on_pod() > 0 or not self._others_still_writing(dream_id)):
            self.gpu.warm(dream_id)

    def _release_gpu_if_done(self, dream_id: uuid.UUID, step: str, status: str) -> None:
        """The pod is released as soon as it cannot be needed soon: GPU steps over, Short ended, or a step failed (Retry rents afresh)."""
        if self.gpu is not None and (status in ("COMPLETED", "CANCELLED", "FAILED_WAITING") or step in ("MUSIC", "FINISH")):
            # GPU power Manual: the pod stays on after the GPU steps, for a quick redo. It goes off by the owner's button, after
            # the idle time, or when the Short is cancelled.
            if self.gpu.hold_manual(dream_id) and status != "CANCELLED":
                return
            self.gpu.release(dream_id, f"{step} {status}")

    def release_gpu_if_nobody_waits(self) -> None:
        """Shut down at empty (21 Nov 2026): when no Short will use the shared pod soon, it ends now instead of sitting through review
        windows and the in-pod idle tail (that guard stays the safety net, rule 8). Called after every status change and, in S7, every 30 s."""
        if self.gpu is None or self.gpu.users_on_pod() == 0:
            return
        soon = self.db.rows(
            "SELECT count(*) AS n FROM dream_short WHERE ((step || ':' || status) = ANY(%(k)s) AND updated_at > now() - interval '2 hours') "
            "OR (step = 'CLIPS' AND status = 'WAITING_COST' AND updated_at > now() - make_interval(secs => %(grace)s))",
            {"k": GPU_SOON, "grace": COST_OK_GRACE.total_seconds()})[0]["n"]
        if soon == 0:
            self.gpu.release_all_unless_held("no Short is waiting for GPU work")

    def release_gpu_if_nobody_waits_scheduled(self) -> None:
        try:
            self.release_gpu_if_nobody_waits()
        except Exception as e:
            log.warning("GPU empty check failed: %s", e)

    def gpu_batch_ahead(self, dream_id: uuid.UUID, step: str) -> int:
        """How many other Shorts are working right now on steps that come before this one's GPU work (a Short waiting for the owner
        never holds others up)."""
        if self.gpu is None:
            return 0
        if step == "CLIPS":
            ahead = BEFORE_CLIPS
        elif step == "PICTURES" and self.gpu.users_on_pod() == 0:
            ahead = FREE_STEPS
        else:
            return 0
        return self.db.rows("SELECT count(*) AS n FROM dream_short WHERE id <> %(id)s AND status = 'RUNNING' AND step = ANY(%(s)s) "
                            "AND updated_at > now() - interval '2 hours'", {"id": dream_id, "s": ahead})[0]["n"]

    def _others_still_writing(self, dream_id: uuid.UUID) -> bool:
        return self.db.rows("SELECT count(*) AS n FROM dream_short WHERE id <> %(id)s AND status = 'RUNNING' AND step = ANY(%(s)s) "
                            "AND updated_at > now() - interval '2 hours'", {"id": dream_id, "s": FREE_STEPS})[0]["n"] > 0

    # ------------------------------------------------------------------------------------------- WRITE

    def write_shots(self, dream_id: uuid.UUID, notes: str | None) -> str:
        self._warm_gpu(dream_id)
        d = self.dream(dream_id)
        if looks.unfiltered(d):
            return self._write_unfiltered(dream_id, d)
        lk = looks.look(d.get("look"))
        count = int(d["shot_count"])
        scouted = self.scouted(d)
        chain = d.get("chain_shots") is True
        group = int(d["chain_group"])
        locked = chain and group == 0 and d.get("chain_locked") is True
        hook = d.get("hook_title") is True
        paths = d.get("path_choice") is True
        seed = idea_packs.resolve(d.get("idea_ref"))
        is_svi = svi.on(d)
        svi_scenes = svi.scenes(d) if is_svi else 0
        svi_beats = svi.beats_for(svi.seconds(d)) if is_svi else 0
        # The owner's chosen Scout angle replaces the look's shot structure (19 Nov: as a user-message note it lost twice).
        if is_svi:
            structure = svi.structure(svi_scenes, svi.seconds(d), svi_beats)
        elif scouted is not None and scouted.chosen() is not None:
            c = scouted.chosen()
            structure = (f"Invent {count} shots that are consecutive moments of ONE idea the owner chose, in the same place, flowing "
                         f"into each other: {c.title} - {c.idea}")
        elif locked:
            structure = looks.LOCKED_STRUCTURE % count
        elif d.get("chain_bridge") is True:
            structure = looks.BRIDGE_STRUCTURE % count
        elif chain:
            structure = looks.CHAIN_GROUP_STRUCTURE % (count, group, group) if group > 0 else looks.CHAIN_STRUCTURE % count
        else:
            structure = lk.structure % count
        feel = looks.FEEL_ARCHITECTURE if d.get("look") == "architecture" else looks.FEEL
        fields = svi.json_fields(svi_scenes, svi_beats) if is_svi else SHOTS_FIELD
        extra = (looks.HOOK_FIELD if hook else "") + (looks.CAMERA_PATH_FIELD if locked else "") + (branches.PATH_FIELD if paths else "")
        tail = "" if is_svi else f" with exactly {count} shots"
        system = (
            "You are the writer of a short, wordless, music-led vertical video (YouTube Shorts / Reels) for a channel\n"
            f"about dreams and impossible places. {structure} Every shot must be a fresh, specific image (not generic).\n"
            f"{feel}\n"
            f"Look: {lk.brief}\n"
            "Return JSON: {\"title\": short title, \"world\": one paragraph describing the shared feeling and world (materials,\n"
            "objects, scale, mood), \"palette\": the exact colour palette, \"light\": the light and atmosphere,\n"
            "\"hero\": the main object that appears in several shots, described exactly once so it can be drawn the same\n"
            "every time (shape, size, materials, colours, every part and where it sits; e.g. \"green-painted steel bus\n"
            "shelter, rusty corrugated tin roof sloping back, three glass side panels, wooden slat bench inside, one\n"
            "round lamp under the roof\"; empty if no object recurs), never mention the hero's look differently in a scene,\n"
            f"{fields}{extra}}}{tail}.\n") + self.lessons("WRITER")
        user = (f"Idea: {d.get('idea')}" + ("" if not notes or not notes.strip() else f"\nOwner's notes for this rewrite: {notes}")
                + ("" if seed is None else idea_packs.for_writer(seed)) + ("" if scouted is None else scouted.for_writer())
                + self.continuation(d))
        since = datetime.now(UTC)
        w = self.llm.call(None, "dream.write", system, user, DreamWorld, self.brief_model)  # Claude when configured, else the Gemini list
        beats = None
        if is_svi and w is not None:  # the beats as shots, so every check below (and the dashboard) reads them as usual
            beats = svi.flatten(w.scenes, svi_scenes, svi_beats)
            w = w.model_copy(update={"shots": [PlannedDreamShot(scene=b.scene_text, motion=b.motion) for b in beats]})
        if w is None or not w.shots:
            raise RuntimeError("The writer returned no shots")
        rewrite = None if scouted is None else self.scout.too_similar(dream_id, w, scouted)
        if rewrite is not None and not is_svi:  # one rewrite: never copy a Short that already exists
            again = self.llm.call(None, "dream.write", system, user + "\nRewrite: " + rewrite, DreamWorld, self.brief_model)
            if again is not None and again.shots:
                w = again
        self.link_llm_costs(dream_id, "dream.", since)
        with self.db.transaction():
            if is_svi:
                return self._save_svi_beats(dream_id, w, beats, lk)
            shots = w.shots[:count] if len(w.shots) > count else w.shots
            self.db.execute("DELETE FROM dream_shot WHERE dream_short_id = %(d)s", {"d": dream_id})
            for ord_, s in enumerate(shots):
                self.db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt) "
                                "VALUES (%(id)s, %(d)s, %(o)s, %(scene)s, %(pp)s, %(mp)s)",
                                {"id": uuid.uuid4(), "d": dream_id, "o": ord_, "scene": s.scene, "pp": picture_prompt(w, s, lk),
                                 "mp": locked_motion_prompt(w, s, lk) if locked else motion_prompt(s, lk)})
            self.db.execute("UPDATE dream_short SET title = %(t)s, world_json = CAST(%(w)s AS jsonb), final_asset_id = NULL, "
                            "music_asset_id = NULL, updated_at = now() WHERE id = %(d)s", {"t": w.title, "w": w.model_dump_json(), "d": dream_id})
            if hook and w.hook and w.hook.strip():  # the owner's (or the idea pack's) own hook is never replaced
                self.db.execute("UPDATE dream_short SET hook_text = %(h)s WHERE id = %(d)s AND NOT hook_locked",
                                {"h": w.hook.strip(), "d": dream_id})
            options = branches.clean_paths(w.paths) if paths else None
            if options is not None:  # the owner's own edited options are never replaced
                self.db.execute("UPDATE dream_short SET path_a = %(a)s, path_b = %(b)s WHERE id = %(d)s AND NOT path_locked",
                                {"a": options[0], "b": options[1], "d": dream_id})
        return f"\"{w.title}\": {len(shots)} shots in one world ({(w.palette or '').strip()})."

    def _save_svi_beats(self, dream_id: uuid.UUID, w: DreamWorld, beats: list[svi.Beat], lk: Look) -> str:
        """SVI scenes: one dream_shot row per beat. Beat 1 carries the scene's picture prompt (the world, palette, light and the one
        shared style line); every beat has its own motion line plus the same style line, so the scenes look like one film."""
        style = (w.style or "").strip()
        styled = "" if not style else f" Style: {style}."
        self.db.execute("DELETE FROM dream_shot WHERE dream_short_id = %(d)s", {"d": dream_id})
        for ord_, b in enumerate(beats):
            s = PlannedDreamShot(scene=b.scene_text, motion=b.motion)
            motion = motion_prompt(s, lk) if b.beat == 1 else _squash(b.motion + ". " + lk.motion_style)
            self._insert_beat(dream_id, ord_, b, (picture_prompt(w, s, lk) + styled).strip(), (motion + styled).strip())
        self.db.execute("UPDATE dream_short SET title = %(t)s, shot_count = %(n)s, world_json = CAST(%(w)s AS jsonb), final_asset_id = NULL, "
                        "music_asset_id = NULL, updated_at = now() WHERE id = %(d)s",
                        {"t": w.title, "n": len(beats), "w": w.model_dump_json(), "d": dream_id})
        d = self.dream(dream_id)
        if d.get("hook_title") is True and w.hook and w.hook.strip():
            self.db.execute("UPDATE dream_short SET hook_text = %(h)s WHERE id = %(d)s AND NOT hook_locked", {"h": w.hook.strip(), "d": dream_id})
        options = branches.clean_paths(w.paths) if d.get("path_choice") is True else None
        if options is not None:
            self.db.execute("UPDATE dream_short SET path_a = %(a)s, path_b = %(b)s WHERE id = %(d)s AND NOT path_locked",
                            {"a": options[0], "b": options[1], "d": dream_id})
        scenes = len({b.scene for b in beats})
        return (f"\"{w.title}\": {scenes} SVI scene{'' if scenes == 1 else 's'}, {len(beats)} beats in one world "
                f"({(w.palette or '').strip()}).")

    def _insert_beat(self, dream_id: uuid.UUID, ord_: int, b: svi.Beat, picture: str, motion: str) -> None:
        self.db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt, scene_no, beat_no) "
                        "VALUES (%(id)s, %(d)s, %(o)s, %(scene)s, %(pp)s, %(mp)s, %(sn)s, %(bn)s)",
                        {"id": uuid.uuid4(), "d": dream_id, "o": ord_, "scene": b.scene_text, "pp": picture, "mp": motion,
                         "sn": b.scene, "bn": b.beat})

    @staticmethod
    def _title_of(first: str) -> str:
        words = first.split()
        return " ".join(words[:6]) + ("…" if len(words) > 6 else "")

    def _write_unfiltered(self, dream_id: uuid.UUID, d: dict[str, Any]) -> str:
        """Unfiltered "writing": no model is called. Each non-empty line of the prompt becomes one shot, word for word (at most 8)."""
        with self.db.transaction():
            if svi.on(d):  # SVI: a blank line separates scenes, each line is a beat; words exactly as written
                beats = svi.unfiltered(d.get("idea"))
                if not beats:
                    raise RuntimeError("The prompt is empty")
                title = self._title_of(beats[0].motion)
                self.db.execute("DELETE FROM dream_shot WHERE dream_short_id = %(d)s", {"d": dream_id})
                for ord_, b in enumerate(beats):
                    self._insert_beat(dream_id, ord_, b, b.scene_text, b.motion)
                w = DreamWorld(title=title, world="", palette="", light="", hero="",
                               shots=[PlannedDreamShot(scene=b.scene_text, motion=b.motion) for b in beats])
                self.db.execute("UPDATE dream_short SET title = %(t)s, shot_count = %(n)s, world_json = CAST(%(w)s AS jsonb), "
                                "final_asset_id = NULL, music_asset_id = NULL, updated_at = now() WHERE id = %(d)s",
                                {"t": title, "n": len(beats), "w": w.model_dump_json(), "d": dream_id})
                scenes = len({b.scene for b in beats})
                return (f"Unfiltered SVI: {scenes} scene{'' if scenes == 1 else 's'}, {len(beats)} beats, your words exactly as written "
                        "(nothing added, no writer called).")
            lines = [ln.strip() for ln in (d.get("idea") or "").splitlines() if ln.strip()][:8]
            if not lines:
                raise RuntimeError("The prompt is empty")
            title = self._title_of(lines[0])
            shots = [PlannedDreamShot(scene=ln, motion=ln) for ln in lines]
            self.db.execute("DELETE FROM dream_shot WHERE dream_short_id = %(d)s", {"d": dream_id})
            for ord_, s in enumerate(shots):
                self.db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt) "
                                "VALUES (%(id)s, %(d)s, %(o)s, %(scene)s, %(pp)s, %(mp)s)",
                                {"id": uuid.uuid4(), "d": dream_id, "o": ord_, "scene": s.scene, "pp": s.scene, "mp": s.motion})
            w = DreamWorld(title=title, world=" ".join(lines), palette="", light="", hero="", shots=shots)
            self.db.execute("UPDATE dream_short SET title = %(t)s, shot_count = %(n)s, world_json = CAST(%(w)s AS jsonb), final_asset_id = NULL, "
                            "music_asset_id = NULL, updated_at = now() WHERE id = %(d)s",
                            {"t": title, "n": len(shots), "w": w.model_dump_json(), "d": dream_id})
        return (f"Unfiltered: {len(shots)} shot{'' if len(shots) == 1 else 's'}, your words exactly as written "
                "(nothing added, no writer called).")

    # ------------------------------------------------------------------------------------------- SCOUT (see DreamScout)

    def scouted(self, d: dict[str, Any]) -> Scouted | None:
        return self.scout.scouted(d)

    def scout_wanted(self, dream_id: uuid.UUID) -> bool:
        return self.scout.scout_wanted(dream_id)

    def scout_request(self, dream_id: uuid.UUID, notes: str | None) -> DreamScoutRequest:
        return self.scout.scout_request(dream_id, notes)

    def save_scout(self, dream_id: uuid.UUID, req: DreamScoutRequest | None, r: DreamScoutResult) -> str:
        with self.db.transaction():
            return self.scout.save_scout(dream_id, req, r)

    def link_llm_costs(self, dream_id: uuid.UUID, purpose_prefix: str, since: datetime) -> None:
        """LLM costs are recorded without a Short; link this Short's calls (by purpose, since a moment) to it."""
        self.db.execute("UPDATE cost_entry SET dream_short_id = %(d)s WHERE dream_short_id IS NULL AND video_id IS NULL "
                        "AND note LIKE %(p)s AND ts >= %(since)s", {"d": dream_id, "p": purpose_prefix + "%", "since": since})

    def llm_costs_since(self, dream_id: uuid.UUID, purpose_prefix: str, since: datetime) -> float:
        return float(self.db.rows("SELECT COALESCE(sum(amount_inr), 0) AS s FROM cost_entry WHERE dream_short_id = %(d)s "
                                  "AND note LIKE %(p)s AND ts >= %(since)s", {"d": dream_id, "p": purpose_prefix + "%", "since": since})[0]["s"])

    # ------------------------------------------------------------------------------------------- PICTURES

    def picture_request(self, dream_id: uuid.UUID, only: list[str] | None, notes: str | None) -> KeyframeRequest:
        with self.db.transaction():
            jobs: list[KeyframeJob] = []
            wanted = None if only is None else set(only)
            chain = self.chained(dream_id)
            group = self.chain_group(dream_id) if chain else 0
            d = self.dream(dream_id)
            from_parent = d.get("start_clip_asset_id") is not None
            for s in self.shots(dream_id):
                shot_id = str(s["id"])
                if continues(chain, group, int(s["ord"])) or svi.beat_of(s) > 1:
                    continue  # chained / a later SVI beat: it continues from the clip before, so it needs no picture
                if from_parent and int(s["ord"]) == 0:
                    continue  # a continued episode: shot 1 starts from the previous episode's last frame
                if (s.get("picture_asset_id") is not None) if wanted is None else (shot_id not in wanted):
                    continue
                prompt = s["picture_prompt"] + ("" if looks.unfiltered(d) else self.lessons("PICTURE"))
                if notes and notes.strip():
                    prompt += " Owner's notes: " + notes.strip()
                attempt = int(s["picture_takes"]) + 1
                # One seed for the whole Short (owner, 19 Nov: the shelter changed shape every shot); a redo moves it on.
                jobs.append(KeyframeJob(shot_id, attempt, POD if self.gpu is not None else ZIMAGE, prompt, 768, 1344, "9:16", "1K", None,
                                        scene_seed(dream_id, d) + attempt - 1, picture_model_of(d) if self.gpu is not None else None))
            if self.gpu is not None:
                if not jobs:
                    return KeyframeRequest(storage_id(dream_id), None, self.zimage_app, self.zimage_class, jobs)
                inr = self.gpu.inr(self.gpu.picture_seconds(dream_id, len(jobs)))
                self.route(dream_id, "DREAM_PICTURES", "image.dream", POD, inr)
                session = self.gpu.session(dream_id)
                return KeyframeRequest(storage_id(dream_id), None, self.zimage_app, self.zimage_class, jobs, session.url,
                                       session.ready_timeout_seconds)
            if jobs:
                inr = self.pricing.zimage_inr(len(jobs))
                self.route(dream_id, "DREAM_PICTURES", "image.dream", ZIMAGE, inr)
                self.budget.require_gpu(inr, f"{len(jobs)} Dream Short pictures", dream_id)
            return KeyframeRequest(storage_id(dream_id), None, self.zimage_app, self.zimage_class, jobs)

    def save_pictures(self, dream_id: uuid.UUID, req: KeyframeRequest, r: KeyframeResult) -> str:
        with self.db.transaction():
            return self._save_pictures(dream_id, req, r)

    def _save_pictures(self, dream_id: uuid.UUID, req: KeyframeRequest, r: KeyframeResult) -> str:
        jobs = {j.shot_id: j for j in req.jobs}
        ok = 0
        errors: list[str | None] = []
        for o in r.results:
            job = jobs.get(o.shot_id)
            if job is None:
                continue
            shot_id = uuid.UUID(o.shot_id)
            if o.uri is None:
                errors.append(o.error)
                self.db.execute("UPDATE dream_shot SET error = %(e)s, updated_at = now() WHERE id = %(s)s", {"e": o.error, "s": shot_id})
                continue
            flux = job.pod_model == "flux2-klein"
            asset = self.assets.create(NewAsset(
                None, "image", o.uri, "image/png", job.width, job.height, None, job.provider,
                FLUX2_KLEIN_NAME if flux else (o.model if o.model is not None else "Tongyi-MAI/Z-Image-Turbo"), job.prompt, o.seed,
                lic.LICENCE_FLUX2_KLEIN if flux else lic.LICENCE_ZIMAGE, True))
            self.db.execute("UPDATE asset SET dream_short_id = %(d)s WHERE id = %(a)s", {"d": dream_id, "a": asset})
            # A new picture makes the old clip stale: it must be made again from this picture.
            self.db.execute("UPDATE dream_shot SET picture_asset_id = %(a)s, picture_takes = picture_takes + 1, clip_asset_id = NULL, "
                            "error = NULL, updated_at = now() WHERE id = %(s)s", {"a": asset, "s": shot_id})
            if svi.on(self.dream(dream_id)):  # SVI: every beat of this scene grew from this picture, so all are stale
                self.db.execute("UPDATE dream_shot b SET clip_asset_id = NULL, updated_at = now() FROM dream_shot s WHERE s.id = %(s)s "
                                "AND b.dream_short_id = s.dream_short_id AND b.scene_no = s.scene_no", {"s": shot_id})
            if self.bridged(dream_id):  # first-last frame: the shot before ends on this picture, so its clip is stale too
                self.db.execute("UPDATE dream_shot b SET clip_asset_id = NULL, updated_at = now() FROM dream_shot s WHERE s.id = %(s)s "
                                "AND b.dream_short_id = s.dream_short_id AND b.ord = s.ord - 1", {"s": shot_id})
            ok += 1
        if r.modal_gpu_seconds > 0:
            self.cost(dream_id, "modal_l40s", self.pricing.modal_gpu_inr("L40S", r.modal_gpu_seconds), r.modal_gpu_seconds, "gpu_seconds", True,
                      f"Dream Short pictures (Z-Image, {ok})")
        self.router.report_batch(POD if self.gpu is not None else ZIMAGE, ok, len(errors), None if not errors else errors[0])
        if ok == 0:
            raise RuntimeError("No pictures were made: " + " | ".join(e for e in errors if e is not None))
        self._stale_final(dream_id)
        any_flux = any(j.pod_model == "flux2-klein" for j in req.jobs)
        return (f"{ok}/{len(req.jobs)} pictures made ({FLUX2_KLEIN_NAME if any_flux else 'Z-Image'}"
                f"{' on a rented pod' if self.gpu is not None else ''})"
                + ("" if not errors else f"; {len(errors)} failed: {errors[0]}") + ".")

    # ------------------------------------------------------------------------------------------- CLIPS

    def clip_estimate(self, dream_id: uuid.UUID, only: list[str] | None) -> DreamEstimate:
        jobs = self.clip_jobs(dream_id, only)
        d = self.dream(dream_id)
        is_svi = svi.on(d)
        if is_svi and self.gpu is None:
            raise RuntimeError("SVI scenes need the rented GPU pod (the GPU broker is off)")
        if self.gpu is not None and jobs:
            sec = self.pod_clip_seconds(dream_id, jobs, is_svi)
            inr = self.gpu.inr(sec)
            mins = fixed(math.ceil(sec / 60), 0)
            if is_svi:
                summary = (f"{len(jobs)} SVI beats in {len({j.scene for j in jobs})} scene(s) on a rented GPU pod (Wan fp8 + SVI 2.0 Pro, 480p): "
                           f"about ₹{_hu(inr, 0)} and {mins} min at the cheapest live price.")
            else:
                summary = (f"{len(jobs)} clips on a rented GPU pod (Wan fp8, {d.get('quality')}): about ₹{_hu(inr, 0)} and {mins} min "
                           "at the cheapest live price.")
            return DreamEstimate("CLIPS", inr, sec / 60, inr > self.auto_approve_inr, summary)
        gpu_sec = self.gpu_seconds(jobs, d.get("quality"))
        inr = _hu(self.pricing.modal_gpu_inr("H100", gpu_sec), 2)
        ask = bool(jobs) and inr > self.auto_approve_inr
        if not jobs:
            summary = "No shots need a clip."
        else:
            left = _hu(max(self.budget.gpu_remaining(), Decimal(0)), 0)
            summary = (f"{len(jobs)} clips on Modal Wan (fast, {d.get('quality')}): about ₹{_hu(inr, 0)} of GPU and "
                       f"{fixed(math.ceil(gpu_sec / 60), 0)} min. ₹{left} of this month's GPU cap left.")
        return DreamEstimate("CLIPS", inr, gpu_sec / 60, ask, summary)

    def clip_request(self, dream_id: uuid.UUID, only: list[str] | None) -> ClipRequest:
        with self.db.transaction():
            jobs = self.clip_jobs(dream_id, only)
            if not jobs:
                raise RuntimeError("No shots with a picture need a clip")
            d = self.dream(dream_id)
            is_svi = svi.on(d)
            if is_svi and self.gpu is None:
                raise RuntimeError("SVI scenes need the rented GPU pod (the GPU broker is off)")
            if self.gpu is not None:
                sec = self.pod_clip_seconds(dream_id, jobs, is_svi)
                inr = self.gpu.inr(sec)
                # rule 6: the routing decision names the clip method (stage DREAM_CLIPS_SVI for SVI scenes)
                self.route(dream_id, "DREAM_CLIPS_SVI" if is_svi else "DREAM_CLIPS", "video.i2v.dream", POD, inr)
                session = self.gpu.session(dream_id)
                return ClipRequest(storage_id(dream_id), self.wan_app, self.wan_class, self.public_url, jobs, 1, sec, inr, "pod", None, None, 0,
                                   session.url, session.ready_timeout_seconds, (not is_svi) and self.chained(dream_id), self.chain_group(dream_id),
                                   (not is_svi) and self.locked(dream_id), is_svi)
            gpu_sec = self.gpu_seconds(jobs, d.get("quality"))
            inr = self.pricing.modal_gpu_inr("H100", gpu_sec)
            self.route(dream_id, "DREAM_CLIPS", "video.i2v.dream", WAN, inr)
            self.budget.require_gpu(inr, f"{len(jobs)} Dream Short clips", dream_id)
            # One GPU, clips in turn: the ~2 min start-up + model load is paid once for the whole Short. When the AI Producer runs this
            # Short by itself, the GPU stays warm a little after each batch so its redo skips a start-up.
            return ClipRequest(storage_id(dream_id), self.wan_app, self.wan_class, self.public_url, jobs, 1, gpu_sec, _hu(inr, 2), "wan", None,
                               None, self.hold_seconds(dream_id), None, 0, self.chained(dream_id), self.chain_group(dream_id), self.locked(dream_id))

    def pod_clip_seconds(self, dream_id: uuid.UUID, jobs: list[ClipJob], is_svi: bool) -> float:
        """Pod time for these clips. An SVI beat renders like one normal 81-frame clip; the first scene job also fetches the SVI files."""
        return self.gpu.clip_seconds(dream_id, sum(j.num_frames for j in jobs)) + (svi.SETUP_SECONDS if is_svi else 0)

    def chained(self, dream_id: uuid.UUID) -> bool:
        return self.dream(dream_id).get("chain_shots") is True

    def bridged(self, dream_id: uuid.UUID) -> bool:
        """First-last frame chaining: clip N runs from picture N to picture N+1. Off by default; never with chained."""
        return self.dream(dream_id).get("chain_bridge") is True

    def locked(self, dream_id: uuid.UUID) -> bool:
        """Locked scene: one chain through the whole Short with one camera path, one world and one seed (needs chaining, no groups)."""
        d = self.dream(dream_id)
        return d.get("chain_shots") is True and int(d["chain_group"]) == 0 and d.get("chain_locked") is True

    def chain_group(self, dream_id: uuid.UUID) -> int:
        return int(self.dream(dream_id)["chain_group"])

    def hold_seconds(self, dream_id: uuid.UUID) -> int:
        """Warm-redo hold for this Short's clip batches: only when the AI Producer decides on it by itself (autopilot)."""
        rows = self.db.rows("SELECT producer_decides FROM dream_short WHERE id = %(d)s", {"d": dream_id})
        return self.pricing.wan_redo_hold_seconds() if rows and rows[0]["producer_decides"] else 0

    def seconds_of(self, lk: Look) -> float:
        """A look's own shot length, else the configured default."""
        return lk.shot_seconds if lk.shot_seconds > 0 else self.shot_seconds

    def clip_jobs(self, dream_id: uuid.UUID, only: list[str] | None) -> list[ClipJob]:
        d = self.dream(dream_id)
        width, height = looks.clip_size(d.get("quality"))
        n_frames = looks.frames(self.seconds_of(looks.look(d.get("look"))))
        wanted = None if only is None else set(only)
        jobs: list[ClipJob] = []
        # The Producer's stop-rule fixes for this Short's clips (a cause that came back twice): firm prompt + negative.
        # (Unfiltered: nothing is ever added to the owner's words.)
        fixes: list[redo_stop_rule.Cause] = []
        if not looks.unfiltered(d):
            keys = [r["stop_cause"] for r in self.db.rows(
                "SELECT DISTINCT stop_cause FROM producer_decision WHERE dream_short_id = %(d)s AND stage = 'DREAM_CLIPS' "
                "AND stop_rule = 'prompt_fix'", {"d": dream_id})]
            fixes = [c for c in (redo_stop_rule.cause_by_key(k) for k in keys) if c is not None]
        fix_prompt = "".join(" " + c.prompt_fix for c in fixes)
        negative = None if not fixes else ", ".join(c.negative for c in fixes)
        if svi.on(d):
            return self._svi_clip_jobs(dream_id, wanted, fix_prompt, negative)
        chain = d.get("chain_shots") is True
        group = int(d["chain_group"]) if chain else 0
        locked = chain and group == 0 and d.get("chain_locked") is True
        seed = scene_seed(dream_id, d)
        # A continued episode: shot 1 starts from the last frame of the previous episode's last clip (no picture of its own).
        parent_clip = None if d.get("start_clip_asset_id") is None else self.assets.uri_of(d["start_clip_asset_id"])
        before: str | None = None  # the clip of the shot before this one, when it already exists (chained shots)
        bridge = d.get("chain_bridge") is True
        all_shots = self.shots(dream_id)
        for i, s in enumerate(all_shots):
            # First-last frame: this clip ends on the next shot's picture (the last clip, or a missing picture: ends freely).
            end_image = all_shots[i + 1].get("picture_uri") if bridge and i + 1 < len(all_shots) else None
            shot_id = str(s["id"])
            clip_before = before
            before = s.get("clip_uri")
            ord_ = int(s["ord"])
            cont = continues(chain, group, ord_)
            from_parent = ord_ == 0 and parent_clip is not None
            if from_parent:
                clip_before = parent_clip
            if s.get("picture_uri") is None and not cont and not from_parent:
                continue  # (a chained shot after the first has no picture of its own: it starts from the clip before)
            if (s.get("clip_asset_id") is not None) if wanted is None else (shot_id not in wanted):
                continue
            take = int(s["clip_takes"]) + 1
            jobs.append(ClipJob(shot_id, ord_ + 1, take, "dream", "fast", s.get("picture_uri"),
                                s["motion_prompt"] if not fix_prompt else s["motion_prompt"] + fix_prompt, width, height, n_frames,
                                (seed if locked else abs(string_hash(shot_id))) + take, negative,
                                clip_before if cont or from_parent else None, end_image))
        return jobs

    def _svi_clip_jobs(self, dream_id: uuid.UUID, wanted: set[str] | None, fix_prompt: str, negative: str | None) -> list[ClipJob]:
        """SVI beats to render, one scene after another. A scene is touched when one of its beats is asked for (a redo) or, with nothing
        asked, when one has no clip (first run, Retry). It re-renders from that beat to the scene's end: the later beats grew from it, so
        they would be stale. Earlier beats are kept, and the first beat rendered continues from the clip (and saved latent) of the beat
        before it. Every beat is 81 frames at 480x832 (the proven SVI recipe) and starts from the scene's one picture (the anchor); a
        scene without its picture waits."""
        jobs: list[ClipJob] = []
        scenes: dict[int, list[dict[str, Any]]] = {}
        for s in self.shots(dream_id):
            scenes.setdefault(svi.scene_of(s), []).append(s)
        for scene_no, beats in scenes.items():
            picture = beats[0].get("picture_uri")
            if picture is None:
                continue
            start = -1
            for i, s in enumerate(beats):
                if start >= 0:
                    break
                if wanted is None:
                    hit = s.get("clip_asset_id") is None
                else:
                    hit = str(s["id"]) in wanted or (s.get("clip_asset_id") is None and any(str(b["id"]) in wanted for b in beats))
                if hit:
                    start = i
            if start < 0:
                continue
            for i in range(start, len(beats)):
                s = beats[i]
                shot_id = str(s["id"])
                take = int(s["clip_takes"]) + 1
                prompt = s["motion_prompt"] if not fix_prompt else s["motion_prompt"] + fix_prompt
                jobs.append(ClipJob(shot_id, int(s["ord"]) + 1, take, "dream", "fast", picture, prompt, 480, 832, svi.BEAT_FRAMES,
                                    abs(string_hash(shot_id)) + take, negative, beats[i - 1].get("clip_uri") if i > 0 else None, None,
                                    scene_no, svi.beat_of(s)))
        return jobs

    def gpu_seconds(self, jobs: list[ClipJob], quality: str | None) -> float:
        """Wan fast render time (scaled by pixels and frames from the measured 480p draft) + one container start-up."""
        if not jobs:
            return 0.0
        # Measured timings (recalibrated after every batch) win over the config's numbers. 720p is its own number: render time grows
        # faster than the pixel count (19 Nov 2026: ~2x the old pixel-scaled guess).
        kind = clip_kind(quality)
        self.evidence.recalibrate(kind)  # cheap; keeps the estimate on the latest real timings
        per81 = self.evidence.calibrated(kind + ".per81")
        if per81 is None:
            per81 = self.pricing.wan_seconds_per_81_frames("draft720" if quality == "720p" else "draft")
        first_extra = self.evidence.calibrated(kind + ".first_extra")
        if first_extra is None:
            first_extra = self.pricing.wan_first_clip_extra_seconds()
        return sum(per81 * j.num_frames / looks.MAX_FRAMES for j in jobs) + first_extra + self.pricing.gpu_container_start_seconds()

    def save_clips(self, dream_id: uuid.UUID, req: ClipRequest, r: ClipResult) -> str:
        with self.db.transaction():
            return self._save_clips(dream_id, req, r)

    def _save_clips(self, dream_id: uuid.UUID, req: ClipRequest, r: ClipResult) -> str:
        jobs = {f"{j.shot_id}:{j.take}": j for j in req.jobs}
        ok = stopped = 0
        gpu = 0.0
        spent = Decimal(0)
        kind = clip_kind(self.dream(dream_id).get("quality"))
        first = r.start_ups() > 0
        on_pod = req.provider == "pod"
        errors: list[str] = []
        for o in r.clips:
            job = jobs.get(f"{o.shot_id}:{o.take}")
            if job is None:
                continue
            gpu += o.gpu_seconds
            if not on_pod and o.gpu_seconds > 0:
                self.evidence.gpu_timing(dream_id, kind, o.frames if o.frames > 0 else job.num_frames, o.gpu_seconds, first)
                first = False
                spent += self.pricing.modal_gpu_inr("H100", o.gpu_seconds)
                self.cost(dream_id, "modal_h100", self.pricing.modal_gpu_inr("H100", o.gpu_seconds), o.gpu_seconds, "gpu_seconds", True,
                          f"Dream Short clip, shot {job.shot_number} take {job.take} (Wan fast)")
            shot_id = uuid.UUID(o.shot_id)
            if o.uri is None:
                if o.error == "stopped":
                    stopped += 1
                else:
                    errors.append(f"shot {job.shot_number}: {o.error}")
                    self.db.execute("UPDATE dream_shot SET error = %(e)s, updated_at = now() WHERE id = %(s)s", {"e": o.error, "s": shot_id})
                continue
            fps = o.fps if o.fps > 0 else looks.FPS
            model = (f"wan2.2-i2v-a14b-fp8+svi2.0-pro scene {job.scene} beat {job.beat} " if req.svi
                     else "wan2.2-i2v-a14b-fp8 " if on_pod else "wan2.2-i2v-a14b ") + str(o.mode)
            asset = self.assets.create(NewAsset(None, "video", o.uri, "video/mp4", job.width, job.height,
                                                float(o.frames) / fps if o.frames > 0 else None, POD if on_pod else WAN, model, job.prompt,
                                                job.seed, lic.LICENCE_WAN, True))
            self.db.execute("UPDATE asset SET dream_short_id = %(d)s, fps = %(f)s WHERE id = %(a)s", {"d": dream_id, "f": fps, "a": asset})
            self.db.execute("UPDATE dream_shot SET clip_asset_id = %(a)s, clip_takes = clip_takes + 1, error = NULL, updated_at = now() "
                            "WHERE id = %(s)s", {"a": asset, "s": shot_id})
            ok += 1
        if not on_pod and r.start_ups() > 0:  # observed cold starts: a redo on a warm GPU pays none
            start = r.start_ups() * self.pricing.gpu_container_start_seconds()
            self.cost(dream_id, "modal_h100", self.pricing.modal_gpu_inr("H100", start), start, "gpu_seconds", True,
                      "Dream Short: Wan GPU start-up + model load (estimated)")
            spent += self.pricing.modal_gpu_inr("H100", start)
        if req.estimate_inr is not None and gpu > 0:
            self.evidence.cost_check(dream_id, "CLIPS", float(req.estimate_inr), float(spent))
        if not on_pod:
            for c in self.evidence.recalibrate(kind):
                log.info("Clip estimate recalibrated: %s", c)
        self.router.report_batch(POD if on_pod else WAN, ok, len(errors), None if not errors else errors[0])
        if ok == 0 and stopped == 0:
            raise RuntimeError("No clips were made: " + " | ".join(errors))
        self._stale_final(dream_id)
        how = (", SVI scenes" if req.svi else ", chained" if req.chain else
               ", first-last frame" if any(j.end_image_uri is not None for j in req.jobs) else "")
        tail_err = ""
        if errors:
            tail_err = f"; {len(errors)} failed: {errors[0]}" + (
                " (the shots after a failed one wait for it: Retry or redo it, they continue from its last frame)" if req.chain
                else " (Retry re-renders that scene from the failed beat on; earlier beats are kept)" if req.svi else "")
        return (f"{ok}/{len(req.jobs)} clips made (Wan {'fp8 on a rented pod' if on_pod else 'fast'}, {self.dream(dream_id).get('quality')}{how})"
                + (f" in {fixed(gpu, 0)} s of GPU time" if gpu > 0 else "") + (f"; stopped before {stopped} finished" if stopped > 0 else "")
                + tail_err + ".")

    # ------------------------------------------------------------------------------------------- MUSIC (see DreamMusic)

    def short_seconds(self, shots: int, lk: Look) -> float:
        """Length the finished Short will have: the shots minus the dissolves between them, plus a second of tail."""
        clip = looks.frames(self.seconds_of(lk)) / looks.FPS / lk.speed
        return shots * clip - max(0, shots - 1) * min(lk.dissolve_sec, clip / 3) + 1

    def music_request(self, dream_id: uuid.UUID, notes: str | None) -> MusicRequest:
        with self.db.transaction():
            return self.music.music_request(dream_id, notes)

    def save_music(self, dream_id: uuid.UUID, r: MusicResult) -> str:
        with self.db.transaction():
            return self.music.save_music(dream_id, r)

    def music_source(self, dream_id: uuid.UUID) -> str:
        return self.music.music_source(dream_id)

    def music_bed_request(self, dream_id: uuid.UUID) -> DreamBedRequest:
        return self.music.music_bed_request(dream_id)

    def save_music_bed(self, dream_id: uuid.UUID, r: DreamBedResult) -> str:
        with self.db.transaction():
            return self.music.save_music_bed(dream_id, r)

    # ------------------------------------------------------------------------------------------- FINISH

    def finish_request(self, dream_id: uuid.UUID) -> DreamFinishRequest:
        clips: list[DreamFinishClip] = []
        for s in self.shots(dream_id):
            if s.get("clip_uri") is None:
                raise RuntimeError(f"Shot {int(s['ord']) + 1} has no clip yet")
            clips.append(DreamFinishClip(s["clip_uri"], float(looks.FPS if s.get("clip_fps") is None else s["clip_fps"]), s.get("scene")))
        if not clips:
            raise RuntimeError("No shots to finish")
        d = self.dream(dream_id)
        music_uri = None if d.get("music_asset_id") is None else self.assets.uri_of(d["music_asset_id"])
        music_name = None
        if d.get("music_asset_id") is not None:
            rows = self.db.rows("SELECT CASE WHEN p.provider = %(bed)s THEN 'your track (' || p.model || ')' ELSE 'Lyria score' END AS n "
                                "FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.id = %(a)s",
                                {"bed": BED, "a": d["music_asset_id"]})
            music_name = rows[0]["n"] if rows else None
        lk = looks.look(d.get("look"))
        is_svi = svi.on(d)
        # SVI: beats are cross-faded into one shot per scene first; between scenes a hard cut (default) or a 0.5 s dissolve
        svi_scenes = svi.beats_per_scene(self.shots(dream_id)) if is_svi else None
        svi_frames = None if not is_svi else [0 if looks.unfiltered(d) else min(svi.frames_of(n), svi.seconds(d) * svi.FPS + 1)
                                              for n in svi_scenes]
        scouted = self.scouted(d)
        return DreamFinishRequest(
            storage_id(dream_id), d.get("look"), clips, 1080, 1920, 24,
            (0.5 if d.get("svi_between") == "dissolve" else 0.0) if is_svi else lk.dissolve_sec,
            None if not self.music_dir or not self.music_dir.strip() else self.music_dir, self.public_url, music_uri, music_name,
            lk.speed, lk.handheld, d.get("edit_music") is not False, d.get("edit_ambience") is not False, d.get("edit_pacing") is not False,
            # a picked Scout angle is one story told in order: pacing must not reshuffle it (19 Nov: the ending played third), and so is a
            # path-choice Short (the end card asks about where the LAST shot leaves you) and so are SVI scenes: their order is the story
            (scouted is not None and scouted.chosen() is not None) or path_choice(d) or is_svi,
            _blank_to_none(d.get("hook_text")) if d.get("hook_title") is True else None, d.get("hook_color"),
            _blank_to_none(d.get("path_a")) if path_choice(d) else None, _blank_to_none(d.get("path_b")) if path_choice(d) else None,
            svi_scenes, svi_frames)

    def continuation(self, d: dict[str, Any]) -> str:
        """The writer's continuation context for an episode after the first (empty for every other Short)."""
        if d.get("parent_id") is None:
            return ""
        branch = self.dream(d["parent_id"]).get("branch_json")
        memory = {} if branch is None else (branch if isinstance(branch, dict) else json.loads(branch))
        return branches.for_writer(memory, d.get("chosen_path"), int(d["episode"]))

    def set_paths(self, dream_id: uuid.UUID, a: str | None, b: str | None) -> None:
        """The owner edits the two path options; they apply at the next finish and a rewrite by the writer never replaces them."""
        ca, cb = branches.clean_path(a), branches.clean_path(b)
        if ca is None or cb is None:
            raise ValueError("Write both paths (2-4 words each)")
        self.db.execute("UPDATE dream_short SET path_a = %(a)s, path_b = %(b)s, path_locked = true, updated_at = now() WHERE id = %(id)s",
                        {"a": ca, "b": cb, "id": dream_id})

    def save_branch(self, dream_id: uuid.UUID) -> None:
        """Branch memory, saved when a path-choice Short finishes: what the next episode needs to continue the same world from this one's
        last frame."""
        d = self.dream(dream_id)
        if d.get("path_choice") is not True:
            return
        all_shots = self.shots(dream_id)
        last = all_shots[-1] if all_shots else {}
        raw = d.get("world_json")
        w = None if raw is None else DreamWorld.model_validate(raw if isinstance(raw, dict) else json.loads(raw))
        b: dict[str, Any] = {"title": d.get("title")}
        if w is not None:
            b.update({"world": w.world, "palette": w.palette, "light": w.light, "hero": w.hero, "cameraPath": w.cameraPath})
        b["lastScene"] = last.get("scene")
        b["lastClipAssetId"] = None if last.get("clip_asset_id") is None else str(last["clip_asset_id"])
        b["seed"] = scene_seed(dream_id, d)
        b["look"] = d.get("look")
        for k, col in (("chainShots", "chain_shots"), ("chainGroup", "chain_group"), ("chainLocked", "chain_locked"),
                       ("chainBridge", "chain_bridge"), ("pathA", "path_a"), ("pathB", "path_b"), ("episode", "episode")):
            b[k] = d.get(col)
        self.db.execute("UPDATE dream_short SET branch_json = CAST(%(b)s AS jsonb) WHERE id = %(id)s", {"b": json.dumps(b), "id": dream_id})

    def set_hook(self, dream_id: uuid.UUID, text: str | None, color: str | None) -> None:
        """The owner edits the hook title; it applies at the next finish and a rewrite never replaces it. None colour keeps the colour;
        blank text clears it."""
        self.db.execute("UPDATE dream_short SET hook_text = %(t)s, hook_color = COALESCE(%(c)s, hook_color), hook_locked = true, "
                        "updated_at = now() WHERE id = %(id)s", {"t": _blank_to_none(text), "c": _blank_to_none(color), "id": dream_id})

    def save_finish(self, dream_id: uuid.UUID, r: DreamFinishResult) -> str:
        if r.uri is None:
            raise RuntimeError(f"Finish failed: {r.error}")
        with self.db.transaction():
            asset = self.assets.create(NewAsset(None, "video", r.uri, "video/mp4", r.width, r.height, r.duration_sec, "local_laptop",
                                                f"dream finish ({r.smoothing}, FFmpeg look)", None, None, lic.LICENCE_WAN, True))
            self.db.execute("UPDATE asset SET dream_short_id = %(d)s, fps = %(f)s WHERE id = %(a)s", {"d": dream_id, "f": r.fps, "a": asset})
            self.db.execute("UPDATE dream_short SET final_asset_id = %(a)s, updated_at = now() WHERE id = %(d)s", {"a": asset, "d": dream_id})
            self.save_branch(dream_id)
            self.evidence.save_metrics(dream_id, asset, self.dream(dream_id).get("look"), r.metrics)
            decision = self.router.record_external(
                None, "DREAM_FINISH", "interp.rife", "local_laptop",
                f"laptop first: smoothing, look and mix are free on this laptop ({r.smoothing})", [{"provider": "local_laptop"}])
            self.tag("routing_decision", decision, dream_id)
        return (f"Finished: {fixed(r.duration_sec, 1)} s, {r.width}x{r.height} at {r.fps} fps "
                f"({'smoothed with RIFE on the laptop' if r.smoothing == 'rife-laptop' else 'smoothed with FFmpeg'})"
                + (", no music" if r.music is None else f", music: {r.music}") + ".")

    # ------------------------------------------------------------------------------------------- QUALITY CHECK

    def check_request(self, dream_id: uuid.UUID) -> DreamCheckRequest | None:
        d = self.dream(dream_id)
        if not self.quality_check or looks.unfiltered(d) or d.get("final_asset_id") is None:  # the checks judge "no people, feel-good"
            return None
        try:
            self.budget.require(CHECK_ESTIMATE_INR, "Dream Short quality check")
        except BudgetExceededError:
            return None  # over the Gemini cap: the Short is finished anyway
        return DreamCheckRequest(storage_id(dream_id), self.assets.uri_of(d["final_asset_id"]), self._check_brief(dream_id, d),
                                 self.listen_model, None)

    def picture_check_request(self, dream_id: uuid.UUID) -> DreamCheckRequest | None:
        """The same check on the pictures (a few paise), so most redos happen before any GPU clip is paid for."""
        d = self.dream(dream_id)
        pictures = [s["picture_uri"] for s in self.shots(dream_id) if s.get("picture_uri") is not None]
        if not self.quality_check or looks.unfiltered(d) or not pictures:
            return None
        try:
            self.budget.require(CHECK_ESTIMATE_INR, "Dream Short picture check")
        except BudgetExceededError:
            return None
        return DreamCheckRequest(storage_id(dream_id), None, self._check_brief(dream_id, d), self.listen_model, pictures)

    def _check_brief(self, dream_id: uuid.UUID, d: dict[str, Any]) -> str:
        brief = "Look: " + looks.look(d.get("look")).brief
        brief += self.lessons("WRITER") + self.lessons("PICTURE")
        brief += f"\nTitle: {d.get('title') or ''}\nIdea: {d.get('idea')}"
        s = self.scouted(d)
        if s is not None and s.chosen() is not None:
            brief += f"\nThe owner's chosen angle (one story, shots in this order): {s.chosen().title} - {s.chosen().idea}"
        for n, shot in enumerate(self.shots(dream_id), 1):
            brief += f"\nShot {n}: {shot.get('scene')}"
        return brief

    def save_check(self, dream_id: uuid.UUID, r: DreamCheckResult, stage: str) -> str:
        pictures = stage == "PICTURES"
        with self.db.transaction():
            if r.usage is not None:
                inr = self.pricing.text_inr(r.usage)
                self.cost(dream_id, GEMINI, inr, r.usage.total_tokens, "token", False,
                          f"Dream Short {'picture' if pictures else 'quality'} check ({r.model})")
                self.evidence.cost_check(dream_id, "PICTURE_CHECK" if pictures else "CHECK", float(CHECK_ESTIMATE_INR), float(inr))
                decision = self.router.record_external(None, "DREAM_CHECK", "video.watch", GEMINI,
                                                       "quality check: Gemini watches the finished Short (only model that takes video)",
                                                       [{"provider": GEMINI}])
                self.tag("routing_decision", decision, dream_id)
            if r.error is not None or r.overall <= 0:
                self.evidence.event(dream_id, stage, "check", None, f"check unavailable: {r.error}", None)
                return ("Picture" if pictures else "Quality") + " check unavailable this time."
            col = "picture_check_json" if pictures else "check_json"
            self.db.execute(f"UPDATE dream_short SET {col} = CAST(%(j)s AS jsonb), updated_at = now() WHERE id = %(d)s",
                            {"j": json.dumps(to_json(r)), "d": dream_id})
            n_issues = len(r.issues or [])
            self.evidence.event(dream_id, stage, "check", None, r.summary, {"overall": r.overall, "issues": n_issues})
        tail = ", no issues" if n_issues == 0 else f", {n_issues} issue{'s' if n_issues > 1 else ''} - {r.issues[0].problem}"
        return ("Picture" if pictures else "Quality") + f" check: {r.overall}/10{tail}."

    # ------------------------------------------------------------------------------------------- shared helpers

    def route(self, dream_id: uuid.UUID, stage: str, capability: str, provider: str, inr: Decimal) -> None:
        """The router's choice, logged and linked to the Short. Raises (deferral) when nothing can run it."""
        choice = self.router.choose(None, stage, capability, [Candidate(provider, inr)], provider, True, scope_id=dream_id)
        self.tag("routing_decision", choice.decision_id, dream_id)

    def tag(self, table: str, row_id: uuid.UUID | None, dream_id: uuid.UUID) -> None:
        if row_id is not None:
            self.db.execute(f"UPDATE {table} SET dream_short_id = %(d)s WHERE id = %(id)s", {"d": dream_id, "id": row_id})

    def cost(self, dream_id: uuid.UUID, provider: str, inr: Decimal, units: float, unit_type: str, estimated: bool, note: str) -> None:
        self.db.execute("INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note) "
                        "VALUES (%(id)s, NULL, %(d)s, %(p)s, %(amt)s, %(u)s, %(ut)s, %(est)s, %(note)s)",
                        {"id": uuid.uuid4(), "d": dream_id, "p": provider, "amt": inr, "u": Decimal(repr(float(units))), "ut": unit_type,
                         "est": estimated, "note": note})

    def _stale_final(self, dream_id: uuid.UUID) -> None:
        self.db.execute("UPDATE dream_short SET final_asset_id = NULL, updated_at = now() WHERE id = %(d)s", {"d": dream_id})

    def dream(self, dream_id: uuid.UUID) -> dict[str, Any]:
        rows = self.db.rows("SELECT * FROM dream_short WHERE id = %(id)s", {"id": dream_id})
        if not rows:
            raise LookupError(f"No Dream Short {dream_id}")
        return rows[0]

    def shots(self, dream_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows("SELECT s.*, p.uri_hot AS picture_uri, c.uri_hot AS clip_uri, c.fps AS clip_fps FROM dream_shot s "
                            "LEFT JOIN asset p ON p.id = s.picture_asset_id LEFT JOIN asset c ON c.id = s.clip_asset_id "
                            "WHERE s.dream_short_id = %(d)s ORDER BY s.ord", {"d": dream_id})

    def storage_id(self, dream_id: uuid.UUID) -> str:
        return storage_id(dream_id)

    def status(self, dream_id: uuid.UUID, step: str, status: str, message: str | None) -> None:
        self.db.execute("UPDATE dream_short SET step = %(step)s, status = %(st)s, message = COALESCE(%(m)s, message), updated_at = now() "
                        "WHERE id = %(id)s", {"step": step, "st": status, "m": message, "id": dream_id})
        # Every status change is kept: the dashboard's step timeline and typical step times come from these.
        self.evidence.event(dream_id, step, "status", None, message, {"status": status})
        if status == "FAILED_WAITING":
            self.evidence.event(dream_id, step, "failure", None, message, None)
        self._release_gpu_if_done(dream_id, step, status)
        self.release_gpu_if_nobody_waits()

    def start_clip_batch(self, dream_id: uuid.UUID) -> None:
        self.clip_progress.start_batch(dream_id)

