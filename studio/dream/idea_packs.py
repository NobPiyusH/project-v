"""Curated idea packs for Dream Shorts (idea-packs.json). Port of DreamIdeaPacks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Idea:
    id: str
    title: str
    concept: str
    hook: str
    camera: str
    palette: str


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    look: str
    description: str
    ideas: list[Idea]


@lru_cache(maxsize=1)
def packs() -> list[Pack]:
    raw = json.loads((Path(__file__).parent / "idea-packs.json").read_text(encoding="utf-8"))
    out = []
    for p in raw.get("packs") or []:
        ideas = [Idea(i["id"], i.get("title"), i.get("concept"), i.get("hook"), i.get("camera"), i.get("palette"))
                 for i in p.get("ideas") or []]
        out.append(Pack(p["id"], p.get("name"), p.get("look"), p.get("description"), ideas))
    return out


def pack(pack_id: str) -> Pack | None:
    return next((p for p in packs() if p.id == pack_id), None)


def idea(pack_id: str, idea_id: str) -> Idea | None:
    p = pack(pack_id)
    return None if p is None else next((i for i in p.ideas if i.id == idea_id), None)


def ref(pack_id: str, idea_id: str) -> str:
    return f"{pack_id}/{idea_id}"


def resolve(r: str | None) -> Idea | None:
    """The idea a stored reference points to; None for null or an idea that no longer exists."""
    if r is None or "/" not in r:
        return None
    a, _, b = r.partition("/")
    return idea(a, b)


def for_writer(i: Idea) -> str:
    return ("\n\nConcept seed from the studio's idea pack (build the Short on this instead of inventing a different idea): "
            f"{i.title} - {i.concept} Camera: {i.camera} Palette: {i.palette}.")
