"""Asset cache keyed by content (25 Nov 2026): an identical request is never paid for twice.

A generated asset (keyframe, clip, narration line, music) that passed its checks is stored with a content key: the sha256 of the canonical
JSON of everything that decides the output (provider, model, revision, prompt, negative prompt, seed, size, frames, fps, camera preset,
hashes of the input assets). When a stage is about to request the same thing again, `AssetCache.reuse_if_cached` hands back a NEW asset row
(own Provenance row, `reused_from_asset_id` pointing at the original, same stored file) and books a Rs 0 cost entry saying what it saved,
so the cost view shows the hit instead of a silent gap.

Rules:
- Only an asset that passed its checks gets a key (`remember` with passed=False stores nothing); `invalidate` takes a key away again.
- Per video opt-out: `video.no_cache` (the owner's `noCache` switch). An explicit Redo with a new seed passes bypass=True: always a fresh render.
- Bump `REVISION` to retire every stored key at once (a model or worker change that alters outputs).
This module only reads and writes the database. Stages call it at their request and save points (see the hook lines in shot_stage.py and
stages_m2a.py).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from studio.assets.asset_store import AssetStore, NewAsset
from studio.db import Db

REVISION = "1"
CACHE_PROVIDER_NOTE = "cache hit"


def content_key(*, kind: str, provider: str, model: str | None, prompt: str | None, negative: str | None = None, seed: int | None = None,
                width: int | None = None, height: int | None = None, frames: int | None = None, fps: float | None = None,
                camera: str | None = None, inputs: list[str] | tuple[str, ...] = (), revision: str | None = None,
                extra: dict[str, Any] | None = None) -> str:
    """sha256 over canonical JSON (sorted keys, no spaces). `inputs` are the hashes of the input assets in the order the model reads them
    (first frame, then last frame...). `extra` holds anything else that changes the output (voice, style, language)."""
    doc = {"kind": kind, "provider": provider, "model": model, "revision": revision if revision is not None else REVISION, "prompt": prompt,
           "negative": negative, "seed": seed, "width": width, "height": height, "frames": frames, "fps": fps, "camera": camera,
           "inputs": list(inputs), "extra": extra or {}}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def input_hash(uri_or_hash: str | None) -> str:
    """An input asset's identity for the key: the stored sha256 when there is one, else a hash of its address."""
    s = uri_or_hash or ""
    return s if len(s) == 64 and all(c in "0123456789abcdef" for c in s) else hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheHit:
    asset_id: uuid.UUID
    kind: str
    uri: str
    mime_type: str | None
    width: int | None
    height: int | None
    duration_sec: float | None
    provider: str
    model: str | None
    prompt: str | None
    seed: int | None
    licence_id: uuid.UUID | None
    ai_generated: bool
    shot_id: uuid.UUID | None
    clip_kind: str | None
    fps: float | None


class AssetCache:
    def __init__(self, db: Db, assets: AssetStore, ledger: Any | None = None):
        self._db, self._assets, self._ledger = db, assets, ledger

    # ---------------------------------------------------------------------------------------------- writing keys

    def remember(self, asset_id: uuid.UUID, key: str, passed: bool = True) -> bool:
        """Stores the key on a finished asset. A failed asset is never stored (returns False)."""
        if not passed:
            return False
        self._db.execute("UPDATE asset SET content_key = %(k)s WHERE id = %(a)s", {"k": key, "a": asset_id})
        return True

    def invalidate(self, asset_id: uuid.UUID) -> None:
        """The asset turned out bad after all (a later check, the owner): it is never reused again."""
        self._db.execute("UPDATE asset SET content_key = NULL WHERE id = %(a)s", {"a": asset_id})

    # ---------------------------------------------------------------------------------------------- reading keys

    def set_enabled(self, video_id: uuid.UUID, on: bool) -> None:
        """The per-video switch (video.no_cache): off = this video always pays for fresh renders."""
        self._db.execute("UPDATE video SET no_cache = %(n)s WHERE id = %(v)s", {"n": not on, "v": video_id})

    def enabled(self, video_id: uuid.UUID) -> bool:
        rows = self._db.rows("SELECT no_cache FROM video WHERE id = %(v)s", {"v": video_id})
        return bool(rows) and not rows[0]["no_cache"]

    def lookup(self, key: str, kind: str) -> CacheHit | None:
        rows = self._db.rows(
            "SELECT a.id, a.kind, a.uri_hot, a.mime_type, a.width, a.height, a.duration_sec, a.shot_id, a.clip_kind, a.fps, p.provider, p.model, "
            "p.prompt, p.seed, p.licence_id, p.ai_generated FROM asset a JOIN provenance p ON p.id = a.provenance_id "
            "WHERE a.content_key = %(k)s AND a.kind = %(kind)s AND a.uri_hot IS NOT NULL ORDER BY a.created_at DESC LIMIT 1",
            {"k": key, "kind": kind})
        if not rows:
            return None
        r = rows[0]
        return CacheHit(r["id"], r["kind"], r["uri_hot"], r["mime_type"], r["width"], r["height"],
                        None if r["duration_sec"] is None else float(r["duration_sec"]), r["provider"], r["model"], r["prompt"], r["seed"],
                        r["licence_id"], bool(r["ai_generated"]), r["shot_id"], r["clip_kind"], None if r["fps"] is None else float(r["fps"]))

    def reuse_if_cached(self, video_id: uuid.UUID, key: str, kind: str, *, bypass: bool = False, saved_inr: Decimal | None = None,
                        what: str = "") -> uuid.UUID | None:
        """The new asset id when an identical, checked asset exists (and caching is on for this video and not bypassed), else None."""
        if bypass or not self.enabled(video_id):
            return None
        hit = self.lookup(key, kind)
        if hit is None:
            return None
        return self.reuse(video_id, hit, key, saved_inr=saved_inr, what=what)

    def reuse(self, video_id: uuid.UUID, hit: CacheHit, key: str, *, saved_inr: Decimal | None = None, what: str = "") -> uuid.UUID:
        """A new asset row pointing at the same stored file, with its own provenance ('reused from asset X') and a Rs 0 cost entry."""
        with self._db.transaction():
            new_id = self._assets.create(NewAsset(video_id, hit.kind, hit.uri, hit.mime_type, hit.width, hit.height, hit.duration_sec, hit.provider,
                                                  hit.model, hit.prompt, hit.seed, hit.licence_id, hit.ai_generated))
            self._db.execute("UPDATE asset SET content_key = %(k)s, clip_kind = %(ck)s, fps = %(f)s WHERE id = %(a)s",
                             {"k": key, "ck": hit.clip_kind, "f": hit.fps, "a": new_id})
            self._db.execute("UPDATE provenance SET reused_from_asset_id = %(src)s WHERE asset_id = %(a)s", {"src": hit.asset_id, "a": new_id})
            if self._ledger is not None:
                saved = "" if saved_inr is None else f", saved about Rs {saved_inr:.2f}"
                self._ledger.record(video_id, hit.provider, Decimal(0), Decimal(1), "cache_hit", False,
                                    f"{CACHE_PROVIDER_NOTE}: {what or hit.kind} reused from asset {hit.asset_id}{saved}")
        return new_id


# ---- key builders: the same function on the request side and the save side, so a stored key is found again

def keyframe_key(job: Any, model: str) -> str:
    """job: studio.media.types.KeyframeJob. `model` is the configured image model list (the request side and the save side both know it)."""
    return content_key(kind="image", provider=job.provider, model=f"{model}|{job.pod_model or ''}", prompt=job.prompt, seed=job.seed,
                       width=job.width, height=job.height, extra={"aspect": job.aspect_ratio, "size": job.image_size, "reference": job.reference_query})


def clip_key(job: Any, provider: str, model: str | None, fps: float | None) -> str:
    """job: studio.media.types.ClipJob; inputs are the start picture, the chain-from clip and the end picture, in that order."""
    return content_key(kind="video", provider=provider, model=model, prompt=job.prompt, negative=job.negative, seed=job.seed, width=job.width,
                       height=job.height, frames=job.num_frames, fps=fps, camera=job.camera,
                       inputs=[input_hash(u) for u in (job.image_uri, job.chain_from_uri, job.end_image_uri) if u],
                       extra={"mode": job.mode, "kind": job.kind})


def narration_key(model: str, voice: str | None, style: str, language: str | None, text: str, delivery: str | None) -> str:
    return content_key(kind="audio", provider="narration", model=model, prompt=text, extra={"voice": voice, "style": style, "language": language,
                                                                                           "delivery": delivery or ""})


def music_key(model: str, prompt: str, target_sec: float, max_segments: int) -> str:
    return content_key(kind="audio", provider="music", model=model, prompt=prompt, extra={"targetSec": target_sec, "maxSegments": max_segments})
