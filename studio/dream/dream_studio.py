"""The Dream Shorts screen's backend: start a Short, read it, and pass the owner's decisions to its workflow. Port of DreamStudio.

The workflow is reached through a gateway (start / signal / query), so tests use a fake and S7 wires the Temporal client."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from studio.db import Db
from studio.dream import idea_packs, looks, svi, timeline
from studio.dream.branches import PathPick, child_idea, path_of
from studio.dream.producer import DreamProducer
from studio.dream.service import DreamService
from studio.dream.settings import clean_picture_model
from studio.dream.wire_types import DREAM_STEPS
from studio.workflow.stage import StageMode

WORKFLOW = "DreamShortWorkflowPy"


class DreamGateway(Protocol):
    def start(self, workflow: str, workflow_id: str, arg: Any) -> None: ...

    def signal(self, workflow_id: str, name: str, *args: Any) -> None: ...

    def query(self, workflow_id: str, name: str) -> Any: ...


class WorkflowNotFoundError(LookupError):
    """The workflow has already ended and left the server (a signal to it is answered NOT_FOUND)."""


@dataclass
class NewDream:
    """The new-Short form. Every option off by default; None means 'not asked'. hook_title: burn a two-colour hook title into the first ~2 s;
    chain_shots: each clip starts from the last frame of the one before; chain_group: chains of N shots; chain_locked: a locked scene;
    idea_pack + idea_id: start from a curated concept seed instead of researching; path_choice: the Short ends on a two-way choice;
    gpu_hold: auto|manual GPU power; chain_bridge: first-last frame chaining; svi_scenes: 1-3 SVI scenes of svi_scene_seconds (10/15/20)."""

    idea: str | None = None
    look: str | None = None
    shots: int | None = None
    quality: str | None = None
    default_mode: str | None = None
    modes: dict[str, str] | None = None
    review_minutes: int | None = None
    music: str | None = None
    scout: bool | None = None
    music_edit: bool | None = None
    ambience: bool | None = None
    pacing: bool | None = None
    producer_decides: bool | None = None
    hook_title: bool | None = None
    hook_text: str | None = None
    hook_color: str | None = None
    chain_shots: bool | None = None
    idea_pack: str | None = None
    idea_id: str | None = None
    chain_group: int | None = None
    chain_locked: bool | None = None
    path_choice: bool | None = None
    chain_bridge: bool | None = None
    gpu_hold: str | None = None
    gpu_idle_minutes: int | None = None
    svi_scenes: int | None = None
    svi_scene_seconds: int | None = None
    svi_between: str | None = None
    picture_model: str | None = None


@dataclass
class Continuation:
    """A continued episode: the parent, the path picked and the parent's last clip (shot 1 starts from its last frame)."""

    parent_id: uuid.UUID
    episode: int
    chosen_path: str
    pick_source: str
    start_clip_asset_id: uuid.UUID
    seed: int


def _as_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else json.loads(v)


def _text(v: Any) -> Any:
    """jsonb columns come back as Python objects; the dashboard gets them as JSON text, as the Java API sent them."""
    return None if v is None else (v if isinstance(v, str) else json.dumps(v, separators=(",", ":")))


class DreamStudio:
    def __init__(self, db: Db, gateway: DreamGateway, dreams: DreamService, clip_progress: Any, producer: DreamProducer):
        self.db, self.gateway, self.dreams, self.clip_progress, self.producer = db, gateway, dreams, clip_progress, producer

    def create(self, req: NewDream, cont: Continuation | None = None) -> uuid.UUID:
        # Unfiltered (owner, 20 Nov 2026): the prompt is used exactly as written, so nothing that rewrites or judges it is on: no idea
        # pack, Scout, hook title, path-choice end card, editing rules (music cut, ambience, pacing) or Producer.
        raw = req.look == looks.UNFILTERED
        seeded = not raw and bool(req.idea_pack and req.idea_pack.strip()) and bool(req.idea_id and req.idea_id.strip())
        seed = idea_packs.idea(req.idea_pack, req.idea_id) if seeded else None
        if seeded and seed is None:
            raise ValueError(f"Unknown idea: {req.idea_pack}/{req.idea_id}")
        if not seeded and (req.idea is None or not req.idea.strip()):
            raise ValueError("Type an idea first")
        idea_text = seed.concept if req.idea is None or not req.idea.strip() else req.idea.strip()
        look = req.look if req.look is not None and req.look in looks.LOOKS else looks.LOOK_ORDER[0]
        if seeded:  # the pack decides the look; the idea comes from the pack, so there is nothing for the Scout to research
            p = idea_packs.pack(req.idea_pack)
            look = p.look if p is not None and p.look in looks.LOOKS else look
        hook_title = not raw and req.hook_title is True
        hook_text = (None if not hook_title else req.hook_text.strip() if req.hook_text and req.hook_text.strip()
                     else seed.hook if seed is not None else None)
        svi_scenes = 0 if req.svi_scenes is None else max(0, min(svi.MAX_SCENES, req.svi_scenes))
        svi_seconds = svi.clean_seconds(req.svi_scene_seconds)
        # Unfiltered: one shot per non-empty line of the prompt (the writing step sets the real count).
        if svi_scenes > 0:
            shots = max(1, len(svi.unfiltered(idea_text))) if raw else svi_scenes * svi.beats_for(svi_seconds)
        elif raw:
            shots = int(max(1, min(8, sum(1 for ln in idea_text.splitlines() if ln.strip()))))
        else:
            shots = 5 if req.shots is None else max(2, min(8, req.shots))
        picture_model = clean_picture_model(req.picture_model, self.dreams.default_picture_model())
        gpu_hold = "manual" if (req.gpu_hold or "").lower() == "manual" else "auto"
        gpu_idle = 10 if req.gpu_idle_minutes is None else max(5, min(30, req.gpu_idle_minutes))
        quality = "720p" if req.quality == "720p" else "480p"
        review = 10 if req.review_minutes is None else max(1, min(240, req.review_minutes))
        # One chain style per Short: first-last frame wins over last-frame chaining (the two need different pictures).
        bridge = svi_scenes == 0 and req.chain_bridge is True
        last_frame = svi_scenes == 0 and not bridge and req.chain_shots is True
        default_mode = StageMode.parse(req.default_mode or "REVIEW")
        modes = {k.upper(): StageMode.parse(v) for k, v in (req.modes or {}).items()}
        for k in modes:
            if k not in DREAM_STEPS:
                raise ValueError(f"No Dream step {k}")
        did = uuid.uuid4()
        workflow_id = f"dream-{did}"
        mode_names = {s: modes.get(s, default_mode).name for s in DREAM_STEPS}
        self.db.execute(
            "INSERT INTO dream_short (id, idea, look, shot_count, quality, modes_json, review_minutes, status, step, workflow_id, "
            "music_source, scout, edit_music, edit_ambience, edit_pacing, producer_decides, hook_title, hook_text, hook_color, hook_locked, "
            "chain_shots, idea_ref, chain_group, chain_locked, path_choice, parent_id, episode, chosen_path, pick_source, start_clip_asset_id, "
            "scene_seed, chain_bridge, gpu_hold, gpu_idle_minutes, svi_scenes, svi_scene_seconds, svi_between, picture_model) "
            "VALUES (%(id)s, %(idea)s, %(look)s, %(n)s, %(q)s, CAST(%(modes)s AS jsonb), %(r)s, 'RUNNING', 'SCOUT', %(wf)s, %(music)s, %(scout)s, "
            "%(em)s, %(ea)s, %(ep)s, %(pd)s, %(ht)s, %(htext)s, %(hcol)s, %(hlock)s, %(chain)s, %(iref)s, %(cgroup)s, %(clocked)s, %(pc)s, "
            "%(parent)s, %(episode)s, %(cpath)s, %(psrc)s, %(sclip)s, %(seed)s, %(bridge)s, %(ghold)s, %(gidle)s, %(svi)s, %(svisec)s, "
            "%(svibetween)s, %(pmodel)s)",
            {"id": did, "idea": idea_text, "look": look, "n": shots, "q": quality, "modes": json.dumps(mode_names), "r": review,
             "wf": workflow_id, "music": "lyria" if (req.music or "").lower() == "lyria" else "bed",
             "scout": not seeded and not raw and (req.scout is None or req.scout),
             "em": not raw and (req.music_edit is None or req.music_edit), "ea": not raw and (req.ambience is None or req.ambience),
             "ep": not raw and (req.pacing is None or req.pacing), "pd": not raw and req.producer_decides is True, "ht": hook_title,
             "htext": hook_text, "hcol": None if not req.hook_color or not req.hook_color.strip() else req.hook_color.strip(),
             "hlock": hook_text is not None, "chain": last_frame, "iref": idea_packs.ref(req.idea_pack, req.idea_id) if seeded else None,
             "cgroup": 0 if not last_frame or req.chain_group is None else max(0, min(8, req.chain_group)),
             "clocked": last_frame and req.chain_locked is True and (req.chain_group is None or req.chain_group == 0),
             "pc": not raw and req.path_choice is True, "parent": None if cont is None else cont.parent_id,
             "episode": 1 if cont is None else cont.episode, "cpath": None if cont is None else cont.chosen_path,
             "psrc": None if cont is None else cont.pick_source, "sclip": None if cont is None else cont.start_clip_asset_id,
             "seed": None if cont is None else cont.seed, "bridge": bridge, "ghold": gpu_hold, "gidle": gpu_idle, "svi": svi_scenes,
             "svisec": svi_seconds, "svibetween": svi.clean_between(req.svi_between), "pmodel": picture_model})
        self.gateway.start(WORKFLOW, workflow_id, {"dreamId": str(did), "defaultMode": default_mode.name,
                                                   "modes": {k: v.name for k, v in modes.items()}, "reviewMinutes": review, "fromStep": None})
        created: dict[str, Any] = {"look": look, "shots": shots, "quality": quality, "music": "lyria" if (req.music or "").lower() == "lyria" else "bed",
                                   "mode": default_mode.name, "pictureModel": picture_model}
        if hook_title:
            created["hookTitle"] = True
        if last_frame:
            created["chainShots"] = True
        if bridge:
            created["chainBridge"] = True
        if svi_scenes > 0:
            created.update({"sviScenes": svi_scenes, "sviSceneSeconds": svi_seconds, "sviBetween": svi.clean_between(req.svi_between)})
        if seeded:
            created["idea"] = idea_packs.ref(req.idea_pack, req.idea_id)
        if req.path_choice is True:
            created["pathChoice"] = True
        if cont is not None:
            created.update({"parent": str(cont.parent_id), "episode": cont.episode, "path": cont.chosen_path, "pickedBy": cont.pick_source})
        self.dreams.evidence.event(did, None, "create", None, idea_text, created)
        return did

    def rerun(self, dream_id: uuid.UUID, from_step: str) -> None:
        """Redo a finished (or cancelled) Short from a step: that step and the ones after it run again; earlier results stay. Modes come
        from the Short's saved settings."""
        d = self.dreams.dream(dream_id)
        if d["status"] not in ("COMPLETED", "CANCELLED"):
            raise RuntimeError("This Short is still running: use approve / redo on its current step")
        saved = _as_dict(d["modes_json"])
        modes = {}
        for k, v in saved.items():
            if k in DREAM_STEPS:  # a step that no longer exists is ignored
                modes[k] = StageMode.parse(v).name
        self.db.execute("UPDATE dream_short SET status = 'RUNNING', step = %(s)s, message = NULL, updated_at = now() WHERE id = %(id)s",
                        {"s": from_step, "id": dream_id})
        self.gateway.start(WORKFLOW, f"dream-{dream_id}", {"dreamId": str(dream_id), "defaultMode": "AUTO", "modes": modes,
                                                          "reviewMinutes": int(d["review_minutes"]), "fromStep": from_step})
        self.dreams.evidence.event(dream_id, from_step, "rerun", None, None, None)

    def list(self) -> list[dict[str, Any]]:
        return self.db.rows(
            "SELECT d.id, d.idea, d.title, d.look, d.status, d.step, d.message, d.final_asset_id, d.created_at, d.updated_at, d.episode, d.parent_id, "
            "(SELECT p.picture_asset_id FROM dream_shot p WHERE p.dream_short_id = d.id ORDER BY p.ord LIMIT 1) AS thumb_asset_id, "
            "COALESCE((SELECT sum(amount_inr) FROM cost_entry c WHERE c.dream_short_id = d.id), 0) AS spent_inr "
            "FROM dream_short d ORDER BY d.created_at DESC LIMIT 100")

    def detail(self, dream_id: uuid.UUID) -> dict[str, Any]:
        out = dict(self.dreams.dream(dream_id))
        out.pop("world_json", None)
        out["branch_json"] = _text(out.get("branch_json"))
        out["chain"] = self.chain(dream_id)
        out["children"] = self.db.rows("SELECT id, title, chosen_path, status FROM dream_short WHERE parent_id = %(d)s ORDER BY created_at",
                                       {"d": dream_id})
        for k in ("scout_json", "check_json", "picture_check_json"):
            out[k] = _text(out.get(k))
        out["shots"] = self.db.rows("SELECT id, ord, scene, picture_prompt, motion_prompt, picture_asset_id, clip_asset_id, picture_takes, "
                                    "clip_takes, error FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord", {"d": dream_id})
        out["costs"] = self.db.rows("SELECT provider_id, amount_inr, units, unit_type, estimated, note, ts FROM cost_entry "
                                    "WHERE dream_short_id = %(d)s ORDER BY ts", {"d": dream_id})
        out["decisions"] = self.db.rows("SELECT stage, capability, chosen_provider_id, reason_text, candidates_json::text AS candidates, created_at "
                                        "FROM routing_decision WHERE dream_short_id = %(d)s ORDER BY created_at", {"d": dream_id})
        events = self.dreams.evidence.events(dream_id)
        out["events"] = events
        parsed = [self._event(e) for e in events]
        costs = [timeline.Cost(float(c["amount_inr"]), c["ts"]) for c in out["costs"]]
        out["timeline"] = timeline.steps(parsed, costs, datetime.now(UTC), self._typical_step_times(dream_id))
        out["log"] = timeline.log(parsed)
        out["producer"] = self.producer.decisions(dream_id)
        out["metrics"] = self.dreams.evidence.metrics(dream_id)
        progress = self.clip_progress.get(dream_id)
        out["clipProgress"] = None if progress is None else progress.clips
        try:
            out["view"] = self.gateway.query(f"dream-{dream_id}", "view")
        except Exception:  # finished workflows can still be queried; a missing one just has no live view
            out["view"] = None
        return out

    def approve(self, dream_id: uuid.UUID, step: str) -> None:
        self.gateway.signal(f"dream-{dream_id}", "approve", step)
        self.producer.record_owner_decision(dream_id, step, "approve", None)
        self.dreams.evidence.event(dream_id, step, "approve", None, None, None)

    def redo(self, dream_id: uuid.UUID, step: str, shot_ids: list[str] | None, notes: str | None) -> None:
        self.gateway.signal(f"dream-{dream_id}", "redo", step, shot_ids, notes)
        self.producer.record_owner_decision(dream_id, step, "reject", notes)
        self.dreams.evidence.event(dream_id, step, "redo", shot_ids, notes, None)

    def set_mode(self, dream_id: uuid.UUID, step: str, mode: StageMode) -> None:
        self.gateway.signal(f"dream-{dream_id}", "set_mode", step, mode.name)
        self.dreams.evidence.event(dream_id, step, "mode", None, None, {"mode": mode.name})
        self.db.execute("UPDATE dream_short SET modes_json = jsonb_set(modes_json, CAST(%(path)s AS text[]), to_jsonb(CAST(%(m)s AS text))) "
                        "WHERE id = %(id)s", {"path": "{" + step + "}", "m": mode.name, "id": dream_id})

    def retry(self, dream_id: uuid.UUID) -> None:
        self.gateway.signal(f"dream-{dream_id}", "retry")
        self.dreams.evidence.event(dream_id, None, "retry", None, None, None)

    def cancel(self, dream_id: uuid.UUID) -> None:
        """The owner's Cancel. A Short whose workflow has already ended (a second click, or it finished meanwhile) is not an error."""
        d = self.dreams.dream(dream_id)
        clip_running = d.get("step") == "CLIPS" and d.get("status") == "RUNNING"
        self.clip_progress.request_stop(dream_id)
        try:
            self.gateway.signal(f"dream-{dream_id}", "cancel")
        except WorkflowNotFoundError:
            self.db.execute("UPDATE dream_short SET status = 'CANCELLED', updated_at = now() "
                            "WHERE id = %(id)s AND status NOT IN ('COMPLETED', 'CANCELLED')", {"id": dream_id})
            self.dreams.evidence.event(dream_id, d.get("step"), "cancel", None, None, {"alreadyEnded": True})
            return
        self.dreams.evidence.event(dream_id, d.get("step"), "cancel", None, None, {"afterClip": True} if clip_running else None)

    def stop_clips(self, dream_id: uuid.UUID) -> None:
        """The owner's Stop: clips in flight stop after the one being rendered (rule 8)."""
        self.clip_progress.request_stop(dream_id)
        self.dreams.evidence.event(dream_id, "CLIPS", "stop", None, None, None)

    def set_paths(self, dream_id: uuid.UUID, a: str, b: str) -> None:
        self.dreams.set_paths(dream_id, a, b)
        self.dreams.evidence.event(dream_id, "FINISH", "paths", None, f"{a} / {b}", None)

    def continue_dream(self, parent_id: uuid.UUID, pick: PathPick) -> uuid.UUID:
        """"Continue this dream": a new Short, the next episode, down the picked path in the same world. Shot 1 starts from the parent's
        last frame (chained-shots machinery); the writer reads the parent's branch memory; settings and the world's seed are inherited."""
        p = self.dreams.dream(parent_id)
        if p.get("path_choice") is not True:
            raise RuntimeError("This Short has no path choice: turn on Choose the path for a new Short")
        if p.get("status") != "COMPLETED" or p.get("final_asset_id") is None or p.get("branch_json") is None:
            raise RuntimeError("Finish this Short first: its last frame starts the next episode")
        path = path_of(p, pick.path)
        if path is None or not path.strip():
            raise RuntimeError(f"Path {pick.path} has no text yet")
        branch = _as_dict(p["branch_json"])
        last_clip = branch.get("lastClipAssetId")
        if last_clip is None:
            raise RuntimeError("The last shot has no clip: redo it first")
        modes = _as_dict(p["modes_json"])
        episode = int(p["episode"]) + 1
        # Same settings as the parent: same look, length, quality, editing, hook and chain options, path choice stays on.
        req = NewDream(child_idea(p.get("title"), path, episode), p.get("look"), int(p["shot_count"]), p.get("quality"), "REVIEW", modes,
                       int(p["review_minutes"]), p.get("music_source"), False, p.get("edit_music"), p.get("edit_ambience"), p.get("edit_pacing"),
                       p.get("producer_decides"), p.get("hook_title"), None, p.get("hook_color"), p.get("chain_shots"), None, None,
                       int(p["chain_group"]), p.get("chain_locked"), True, p.get("chain_bridge"), None, None, None, None, None,
                       p.get("picture_model"))
        child = self.create(req, Continuation(parent_id, episode, path, pick.source, uuid.UUID(str(last_clip)), int(branch["seed"])))
        data: dict[str, Any] = {"child": str(child), "path": pick.path, "source": pick.source}
        if pick.votes is not None:
            data["votes"] = pick.votes
        self.dreams.evidence.event(parent_id, None, "continue", None, path, data)
        return child

    def chain(self, dream_id: uuid.UUID) -> list[dict[str, Any]]:
        """The episode chain this Short belongs to, oldest first (episode 1, 2, 3...), following parent links."""
        return self.db.rows(
            "WITH RECURSIVE up AS (SELECT id, parent_id, 0 AS depth FROM dream_short WHERE id = %(id)s "
            "UNION ALL SELECT d.id, d.parent_id, up.depth + 1 FROM dream_short d JOIN up ON d.id = up.parent_id WHERE up.depth < 100) "
            "SELECT d.id, d.episode, d.title, d.chosen_path, d.pick_source, d.status, d.final_asset_id "
            "FROM up JOIN dream_short d ON d.id = up.id ORDER BY d.episode", {"id": dream_id})

    def set_hook(self, dream_id: uuid.UUID, text: str | None, color: str | None) -> None:
        self.dreams.set_hook(dream_id, text, color)
        self.dreams.evidence.event(dream_id, "FINISH", "hook", None, text, None)

    def pick_angle(self, dream_id: uuid.UUID, angle: int) -> None:
        """The owner picks which of the Scout's angles the writer follows (1-based); then approves the Scout step."""
        self.db.execute("UPDATE dream_short SET scout_angle = %(a)s, updated_at = now() WHERE id = %(id)s", {"a": max(1, angle), "id": dream_id})
        self.dreams.evidence.event(dream_id, "SCOUT", "angle", None, None, {"angle": angle})

    def editing(self, dream_id: uuid.UUID, music_edit: bool | None, ambience: bool | None, pacing: bool | None) -> None:
        """The editing switches (music-led cuts, J-cut ambience, pacing); they apply at the next finish or re-finish."""
        self.db.execute("UPDATE dream_short SET edit_music = COALESCE(%(em)s, edit_music), edit_ambience = COALESCE(%(ea)s, edit_ambience), "
                        "edit_pacing = COALESCE(%(ep)s, edit_pacing), updated_at = now() WHERE id = %(id)s",
                        {"em": music_edit, "ea": ambience, "ep": pacing, "id": dream_id})
        data = {k: v for k, v in (("musicEdit", music_edit), ("ambience", ambience), ("pacing", pacing)) if v is not None}
        self.dreams.evidence.event(dream_id, "FINISH", "editing", None, None, data)

    def calibration(self) -> list[dict[str, Any]]:
        return self.dreams.evidence.calibration()

    def _typical_step_times(self, except_id: uuid.UUID) -> dict[str, int]:
        """Median work time per step over the last 20 completed Shorts (from their status events)."""
        by_short: dict[Any, list[timeline.Event]] = {}
        for r in self.db.rows(
                "SELECT dream_short_id, step, kind, notes, data::text AS data, created_at FROM dream_event WHERE kind = 'status' AND dream_short_id IN "
                "(SELECT id FROM dream_short WHERE status = 'COMPLETED' AND id <> %(d)s ORDER BY updated_at DESC LIMIT 20) ORDER BY created_at",
                {"d": except_id}):
            by_short.setdefault(r["dream_short_id"], []).append(self._event(r))
        return timeline.typical([timeline.finished_work(evs) for evs in by_short.values()])

    @staticmethod
    def _event(r: dict[str, Any]) -> timeline.Event:
        data = r.get("data")
        parsed = None if data is None else (data if isinstance(data, dict) else json.loads(data))
        return timeline.Event(r.get("step"), r["kind"], r.get("notes"), parsed, r["created_at"])
