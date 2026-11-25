"""Which automatic redos cost money. The Inspector sends a failed stage back once by itself; when that redo would rent a pod or call a paid
service the stage waits for the owner instead, unless the video has `auto_redo_paid` on (CLAUDE.md rule 2: nothing that spends money without a yes).

Pure data, no I/O: the workflow asks `control.redo_check`, which uses this table."""

from __future__ import annotations

# Stage -> what the redo would use. Only inspected stages can be redone by the Inspector. ANIMATIC and ASSEMBLY are local ffmpeg: free.
PAID_REDO = {
    "RESEARCH": "call a paid research model",
    "SCRIPT": "call a paid writing model",
    "SHOT_PLAN": "call a paid writing model",
    "MUSIC": "call the paid music service",
    "NARRATION": "call the paid voice service",
    "KEYFRAMES": "rent a GPU pod or call the paid image model",
    "DRAFT_GEN": "rent a GPU pod or call the paid video model",
    "FINAL_GEN": "rent a GPU pod or call the paid video model",
}


def redo_is_paid(stage: str) -> bool:
    """Unknown stages count as paid: a redo nobody classified must not start on its own."""
    return stage not in ("ANIMATIC", "ASSEMBLY", "LIBRARY_MATCH", "MOON_LAYER")


def redo_what(stage: str) -> str:
    return PAID_REDO.get(stage, "call a paid service")

