"""Structured outputs the agents ask the LLM for. Port of AgentTypes.

Fields double as the JSON schema shown to the model, so the names stay camelCase (the LLM contract) and are simple.
Missing fields get Java's defaults (null / 0 / false), as Jackson does for records.
"""

from __future__ import annotations

from pydantic import BaseModel


class Act(BaseModel):
    title: str | None = None
    purpose: str | None = None
    approxSec: int = 0  # noqa: N815


class Concept(BaseModel):
    logline: str | None = None
    bigIdea: str | None = None  # noqa: N815
    clockLabel: str | None = None  # noqa: N815  e.g. "Years since the cloud began to collapse"; empty when no clock
    acts: list[Act] | None = None
    emotionalArc: str | None = None  # noqa: N815


class ScriptLine(BaseModel):
    """One narration line (or, for videos without narration, one visual beat)."""

    text: str | None = None
    act: str | None = None
    purpose: str | None = None
    spoken: bool = False  # false for purely visual beats (no voice)
    delivery: str | None = None  # how to read this line, a few words ("hushed, slow"); optional


class Script(BaseModel):
    lines: list[ScriptLine] | None = None
    voiceName: str | None = None  # noqa: N815  one of the Gemini TTS prebuilt voices offered in the prompt
    voiceStyle: str | None = None  # noqa: N815  how to read it, e.g. "slow, hushed, awe-struck"
    musicBrief: str | None = None  # noqa: N815


class PlannedShot(BaseModel):
    """lineIndex: the script line this shot covers (None for pure visual/music moments); priority: hero | standard | filler;
    footageQuery: 2-5 exact search words when real footage would show this better; lane: ai_video (default) | diagram |
    stock; sfxQuery: 2-4 words for a background sound; referenceQuery: name of a real subject for real reference photos."""

    sceneTitle: str | None = None  # noqa: N815
    lineIndex: int | None = None  # noqa: N815
    durationSec: float = 0.0  # noqa: N815
    shotType: str | None = None  # noqa: N815
    cameraMove: str | None = None  # noqa: N815
    description: str | None = None
    keyframePrompt: str | None = None  # noqa: N815
    motionPrompt: str | None = None  # noqa: N815
    priority: str | None = None
    overlayText: str | None = None  # noqa: N815
    footageQuery: str | None = None  # noqa: N815
    referenceQuery: str | None = None  # noqa: N815
    sfxQuery: str | None = None  # noqa: N815
    lane: str | None = None

    def stock(self) -> bool:
        return (self.lane or "").lower() == "stock"

    def diagram(self) -> bool:
        return (self.lane or "").lower() == "diagram"


class ShotPlan(BaseModel):
    shots: list[PlannedShot] | None = None


# ---- Creative Director: story-level review at three levels (shot, paragraph/act, whole video). Scores 1-5.


class ShotCritique(BaseModel):
    """shot: 1-based shot number; literal: illustrates a single word instead of the line's meaning; writing: the picture
    shows (or its prompt asks for) generated lettering; fixPrompt: for any score of 3 or less, a replacement image prompt."""

    shot: int = 0
    meaning: int = 0
    accuracy: int = 0
    continuity: int = 0
    literal: bool = False
    writing: bool = False
    problem: str | None = None
    fixPrompt: str | None = None  # noqa: N815


class ActCritique(BaseModel):
    """shotsToFix: shot numbers that would fix this paragraph's problem (each needs a ShotCritique fixPrompt)."""

    act: str | None = None
    flow: int = 0
    keyMoment: int = 0  # noqa: N815
    continuity: int = 0
    mood: int = 0
    summary: str | None = None
    problem: str | None = None
    shotsToFix: list[int] | None = None  # noqa: N815


class VideoCritique(BaseModel):
    """arc: hook -> build -> payoff -> ending as a whole; variety: no repeated images or ideas."""

    arc: int = 0
    hook: int = 0
    ending: int = 0
    variety: int = 0
    summary: str | None = None
    problem: str | None = None


class CreativeReview(BaseModel):
    video: VideoCritique | None = None
    acts: list[ActCritique] | None = None
    shots: list[ShotCritique] | None = None
