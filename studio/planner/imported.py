"""Start a video from a finished plan file (docs/shorts/*.json, e.g. moon-10x-closer-plan.json) instead of letting the paid planner, writer and
director invent one from the prompt (25 Nov 2026).

The plan document is stored in `video.plan_json -> 'imported'` when the video is created (POST /api/videos with `planFile` or `plan`). The
stages stay the same (modes, approvals, redo); only the three LLM calls are replaced, for that video, by the plan's own content, free:

- PLAN: the file's `video` object is the VideoPlan (no LLM).
- CONCEPT / SCRIPT: built from the plan (title, summary, tone; the `script` lines word for word).
- SHOT_PLAN: one shot per beat, with the beat's camera preset, bank ids, continuity and claims. Shot timing follows the plan's per-beat
  startSec / durationSec (ShotStage reads `ImportedPlan.beat_times`), not the narration length: a line may be shorter than its beat.

A video without an `imported` key goes to the real planner, writer and director unchanged (the wrappers delegate everything else).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from studio.agents.types import Act, Concept, PlannedShot, Script, ScriptLine, ShotPlan
from studio.config import REPO_ROOT
from studio.planner.bank import Bank, BankedPlan, ScenePlan, validate
from studio.planner.plan import VideoPlan

IMPORT_KEY = "imported"
PLAN_DIR = REPO_ROOT / "docs" / "shorts"  # a planFile must be a .json inside this folder


class ImportedBeat(BaseModel):
    prompt: str
    camera: str | None = None
    startSec: float = 0.0  # noqa: N815  absolute, from the start of the video
    durationSec: float = 5.0  # noqa: N815
    characterIds: list[str] | None = None  # noqa: N815
    locationIds: list[str] | None = None  # noqa: N815
    continuity: str | None = None
    claims: list[str] | None = None


class ImportedLine(BaseModel):
    text: str
    startSec: float | None = None  # noqa: N815  absolute
    beat: int | None = None  # 1-based beat this line is spoken over


class ImportedPlan(BaseModel):
    video: VideoPlan
    bank: Bank | None = None
    beats: list[ImportedBeat]
    lines: list[ImportedLine]
    allowUntested: bool = False  # noqa: N815
    width: int = 720
    height: int = 1280

    # ---- what the stages ask of it

    def total_sec(self) -> float:
        return round(sum(b.durationSec for b in self.beats), 3)

    def beat_times(self) -> list[tuple[float, float]]:
        return [(b.startSec, b.durationSec) for b in self.beats]

    def line_starts(self) -> dict[int, float]:
        """Script line index -> the plan's start (a line without one starts with its beat)."""
        out: dict[int, float] = {}
        for i, ln in enumerate(self.lines):
            if ln.startSec is not None:
                out[i] = ln.startSec
            elif ln.beat is not None and 1 <= ln.beat <= len(self.beats):
                out[i] = self.beats[ln.beat - 1].startSec
        return out

    def concept(self) -> Concept:
        v = self.video
        return Concept(logline=v.summary or v.title, bigIdea=v.title, clockLabel="",
                       acts=[Act(title="Imported plan", purpose=v.rationale or "", approxSec=int(self.total_sec()))], emotionalArc=v.tone)

    def script(self) -> Script:
        return Script(lines=[ScriptLine(text=ln.text, act="Imported plan", purpose="plan line", spoken=True) for ln in self.lines],
                      voiceName=None, voiceStyle=self.video.voice, musicBrief=self.video.tone)

    def shot_plan(self) -> ShotPlan:
        by_beat = {ln.beat: i for i, ln in enumerate(self.lines) if ln.beat is not None}
        shots = [PlannedShot(sceneTitle=self.video.title or "Scene", lineIndex=by_beat.get(i + 1), durationSec=b.durationSec, shotType="wide",
                             cameraMove=b.camera, description=f"Beat {i + 1}: " + b.prompt.split(".")[0][:140],
                             keyframePrompt=b.prompt, motionPrompt=b.prompt, priority="hero" if i == 0 else "standard",
                             characterIds=b.characterIds, locationIds=b.locationIds, continuity=b.continuity, claims=b.claims)
                 for i, b in enumerate(self.beats)]
        return ShotPlan(shots=shots, bank=self.bank)


def parse(doc: dict[str, Any]) -> ImportedPlan:
    """Plan document -> ImportedPlan. Raises ValueError with every problem found (bad bank, unknown ids, timings that leave a gap, an
    unknown or untested camera preset, a script line on a missing beat)."""
    from studio.production import camera

    for key in ("video", "scenes", "script"):
        if key not in doc:
            raise ValueError(f"plan has no '{key}'")
    gen = doc.get("generation") or {}
    allow = bool(gen.get("allowUntested"))
    banked = BankedPlan(bank=Bank(**doc["bank"]) if doc.get("bank") else None, scenes=[ScenePlan(**s) for s in doc["scenes"]])
    problems = validate(banked)
    beats: list[ImportedBeat] = []
    cams = gen.get("cameraPresets")
    offset = 0.0
    for sc in banked.scenes or []:
        scene_beats = sc.beats or []
        for b in scene_beats:
            beats.append(ImportedBeat(prompt=b.prompt or "", startSec=round(offset + (b.startSec or 0.0), 3),
                                      durationSec=5.0 if b.durationSec is None else b.durationSec, characterIds=b.characterIds,
                                      locationIds=b.locationIds, continuity=b.continuity, claims=b.claims))
        offset += sc.lengthSec if sc.lengthSec is not None else sum((b.durationSec or 5.0) for b in scene_beats)
    if not beats:
        problems.append("plan has no beats")
    if cams is not None:
        if not isinstance(cams, list) or len(cams) != len(beats):
            problems.append(f"generation.cameraPresets must list one preset per beat ({len(beats)})")
        else:
            for b, c in zip(beats, cams, strict=True):
                b.camera = c
    try:
        camera.check([b.camera for b in beats if b.camera], allow)
    except (ValueError, KeyError) as e:
        problems.append(str(e.args[0] if e.args else e))
    lines = [ImportedLine(text=str(s.get("text", "")), startSec=s.get("startSec"), beat=s.get("beat")) for s in doc["script"]]
    if not lines or any(not ln.text.strip() for ln in lines):
        problems.append("plan script must have at least one line and no blank line")
    problems += [f"script line {i + 1}: beat {ln.beat} does not exist" for i, ln in enumerate(lines) if ln.beat is not None and not 1 <= ln.beat <= len(beats)]
    try:
        video = VideoPlan(**doc["video"])
    except Exception as e:  # noqa: BLE001 - pydantic's message names the field
        problems.append(f"video: {e}")
        video = VideoPlan()
    if problems:
        raise ValueError("; ".join(problems))
    return ImportedPlan(video=video, bank=banked.bank, beats=beats, lines=lines, allowUntested=allow,
                        width=int(gen.get("width", 720)), height=int(gen.get("height", 1280)))


def read_plan_file(name: str) -> dict[str, Any]:
    """A plan file by name: only a .json file inside docs/shorts (the route must not read arbitrary files)."""
    path = (PLAN_DIR / Path(name).name) if "/" not in name.replace("\\", "/") else (REPO_ROOT / name)
    path = path.resolve()
    if path.suffix != ".json" or PLAN_DIR.resolve() not in path.parents:
        raise ValueError("planFile must be a .json file inside docs/shorts")
    if not path.is_file():
        raise ValueError(f"plan file not found: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def initial_plan_json(doc: dict[str, Any]) -> str:
    """The value stored in video.plan_json at creation (validated first). The PLAN stage keeps the key when it writes the real plan."""
    parse(doc)
    return json.dumps({IMPORT_KEY: doc}, separators=(",", ":"))


def title_of(doc: dict[str, Any]) -> str:
    return str((doc.get("video") or {}).get("title") or doc.get("title") or "Imported plan")


def load(db: Any, video_id: uuid.UUID) -> ImportedPlan | None:
    rows = db.rows("SELECT (plan_json -> 'imported')::text AS i FROM video WHERE id = %(v)s", {"v": video_id})
    if not rows or rows[0]["i"] is None:
        return None
    raw = rows[0]["i"]
    return parse(json.loads(raw) if isinstance(raw, str) else raw)


class _Wrapper:
    def __init__(self, inner: Any, db: Any):
        self._inner, self._db = inner, db

    def __getattr__(self, name: str) -> Any:  # everything not replaced goes to the real object
        return getattr(self._inner, name)


class ImportedPlanner(_Wrapper):
    def plan(self, video_id, prompt: str, owner_notes: str | None):
        imp = load(self._db, video_id)
        if imp is None:
            return self._inner.plan(video_id, prompt, owner_notes)
        from studio.planner.planner import Outcome, stages_for

        plan = self._inner.normalise(imp.video, prompt)
        return Outcome(plan, stages_for(plan), "imported plan")


class ImportedWriter(_Wrapper):
    def concept(self, video_id, prompt, plan, facts, owner_notes):
        imp = load(self._db, video_id)
        return self._inner.concept(video_id, prompt, plan, facts, owner_notes) if imp is None else imp.concept()

    def script(self, video_id, prompt, plan, concept, facts, owner_notes):
        imp = load(self._db, video_id)
        return self._inner.script(video_id, prompt, plan, concept, facts, owner_notes) if imp is None else imp.script()


class ImportedDirector(_Wrapper):
    def plan(self, video_id, plan, concept, lines, bpm, total_sec, owner_notes):
        imp = load(self._db, video_id)
        return self._inner.plan(video_id, plan, concept, lines, bpm, total_sec, owner_notes) if imp is None else imp.shot_plan()
