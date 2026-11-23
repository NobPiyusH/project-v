"""Production stages in run order (HANDOVER §9.1, plus the R1 PLAN stage). Port of Stage and StageMode.

The Planner decides which of these a given video needs; skipped stages are recorded as SKIPPED.
"""

from __future__ import annotations

from enum import Enum


class Stage(Enum):
    PLAN = ("Plan", "Turn the prompt into a video plan: format, length, style, what's needed")
    RESEARCH = ("Research", "Gather facts with sources")
    CONCEPT = ("Concept", "Big idea, structure, the 'clock'")
    SCRIPT = ("Script", "Narration text and beat sheet (or a visual beat sheet when there is no narration)")
    MUSIC = ("Music", "Choose or generate the score and map its beats")
    NARRATION = ("Voice", "Narration voiced line by line; every line is transcribed and re-voiced if it is wrong")
    SHOT_PLAN = ("Shot plan", "Scenes and shots timed to the beats, with prompts per model")
    KEYFRAMES = ("Keyframes", "One still per shot, before any video spend")
    ANIMATIC = ("Animatic", "Stills + scratch voice + music cut together to lock story and pacing")
    LIBRARY_MATCH = ("Library match", "Reuse good clips from earlier videos")
    DRAFT_GEN = ("Draft generation", "Cheap low-res drafts of the remaining shots")
    FINAL_GEN = ("Final generation", "Full-quality renders of the chosen drafts")
    CRITIC = ("Critic", "Automatic quality checks on every take")
    TAKE_SELECTION = ("Take selection", "Pick the best take per shot")
    UPSCALE = ("Upscale", "Upscale to 4K, interpolate, colour-match")
    AUDIO = ("Audio", "Final narration, sound effects, mix and loudness")
    ASSEMBLY = ("Assembly", "Timeline, Premiere XML and the master render")
    ROUGH_CUT_REVIEW = ("Rough cut review", "Review the cut and turn notes into changes")
    PACKAGING = ("Packaging", "Captions, dubs, titles, thumbnails, Shorts")
    FINAL_APPROVAL = ("Final approval", "Last check before publishing")
    PUBLISH = ("Publish", "Upload as private to YouTube / Instagram")

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def description(self) -> str:
        return self.value[1]


class StageMode(Enum):
    """Every stage runs in one of these modes, switchable per video and mid-run."""

    AUTO = "AUTO"  # the system does the stage and continues
    PRODUCER = "PRODUCER"  # an AI producer reviews the output and recommends approve / redo / ask (advise-only in P1)
    REVIEW = "REVIEW"  # semi-auto: waits a review window (default 10 min), then continues by itself
    MANUAL = "MANUAL"  # prepares the output, then waits for the owner

    @staticmethod
    def parse(value: str | None) -> StageMode:
        return StageMode.AUTO if value is None else StageMode(value.strip().upper())
