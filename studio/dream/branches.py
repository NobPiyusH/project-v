"""'Choose the path': pure helpers (prompt text, option clean-up, branch memory). Port of DreamBranches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_WORDS = 4
OWNER = "owner"
COMMENTS = "comments"

PATH_FIELD = (", \"paths\": exactly TWO short path options (2 to 4 words each, lower case, concrete "
              "places or things the camera could go next from the LAST shot, e.g. [\"the door\", \"the stairs\"]); they must "
              "both be visible or clearly suggested in the last shot, be clearly different, and make a viewer want to pick one")


@dataclass
class PathPick:
    """Who picked which path. path: "A" or "B"; source: owner (manual) or comments (the YouTube vote)."""

    path: str
    source: str = OWNER
    votes: dict[str, int] | None = None

    def __post_init__(self) -> None:
        self.path = (self.path or "").strip().upper()
        if self.path not in ("A", "B"):
            raise ValueError("Pick path A or B")
        self.source = OWNER if not self.source or not self.source.strip() else self.source.strip()

    @staticmethod
    def owner(path: str) -> PathPick:
        return PathPick(path, OWNER, None)


_STRIP = re.compile("[\"*{}\\\\→←↑↓<>]")


def clean_path(p: str | None) -> str | None:
    """One path option cleaned for the end card: quotes and arrows gone, at most 4 words; None when empty."""
    if p is None:
        return None
    s = re.sub(r"\s+", " ", _STRIP.sub(" ", p)).strip()
    if not s:
        return None
    words = s.split(" ")
    return s if len(words) <= MAX_WORDS else " ".join(words[:MAX_WORDS])


def clean_paths(paths: list[str] | None) -> list[str] | None:
    """The writer's two options, cleaned; None unless there are two different ones."""
    if paths is None:
        return None
    out: list[str] = []
    for p in paths:
        c = clean_path(p)
        if c is not None and not any(c.lower() == o.lower() for o in out):
            out.append(c)
    return out[:2] if len(out) >= 2 else None


def path_of(d: dict[str, Any], ab: str) -> str | None:
    return d.get("path_a" if ab == "A" else "path_b")


def _append(parts: list[str], label: str, v: Any) -> None:
    if v is not None and str(v).strip():
        parts.append(f"\n{label}: {str(v).strip()}")


def for_writer(branch: dict[str, Any], chosen_path: str | None, episode: int) -> str:
    """What the writer reads for a continuation: the world the parent built, where it ended, and the path chosen."""
    parts = [f"\n\nThis Short is EPISODE {episode} of a series: it continues the previous episode in the SAME world (same materials, "
             "palette, light, scale and style; keep the recurring hero object exactly as described)."]
    _append(parts, "Previous title", branch.get("title"))
    _append(parts, "World", branch.get("world"))
    _append(parts, "Palette", branch.get("palette"))
    _append(parts, "Light", branch.get("light"))
    _append(parts, "Hero", branch.get("hero"))
    _append(parts, "The previous episode ended on", branch.get("lastScene"))
    parts.append(f"\nThe audience chose the path: {(chosen_path or '').strip().upper()}. Shot 1 begins EXACTLY on the previous episode's "
                 "last frame (described above) and the camera moves into that path; the rest of the Short explores where that path "
                 "leads. Never recap the previous episode; never jump to an unrelated place.")
    return "".join(parts)


def child_idea(parent_title: Any, chosen_path: str, episode: int) -> str:
    title = "the dream" if parent_title is None else str(parent_title).strip()
    return f"Episode {episode}: {title} - the path chosen: {chosen_path}"
