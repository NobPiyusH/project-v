"""Optional Moon-layer stage (MOON_LAYER, after ASSEMBLY): a plan file with a `moon` block gets its Moon picture composited over the finished master
(scale curve, glow, caption: tools/film4k/moon_layer.py). Replaces the by-hand ffmpeg step of docs/runbooks/first-live-short.md section 5b.

- The control plane only builds the request and saves the result (database work); the composite itself is the media worker's `moon_layer_composite`.
- The original plate (the ASSEMBLY master) is kept as an asset; the finished video is a NEW asset (clip_kind 'moon_layer', derived_from = the plate,
  its own Provenance row) and becomes `video.master_asset_id`. A retry or redo always starts again from the plate, never from an earlier composite.
- The Moon picture is a LOCAL file named by the plan (`moon.layer.imagePath`); nothing is downloaded or generated here.
- No `moon` block (or the MOON_LAYER flag off): the request says `skip` and the stage is recorded as skipped. Failures (missing picture, ffmpeg) raise,
  so the workflow waits for the owner (Approve = continue without the Moon, Reject = retry), never killing the video (rule 2b).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.planner import imported
from studio.production.support import StageSupport
from studio.workflow.types import StageResult

KIND = "moon_layer"
# keys of the plan's moon.layer block that tools/film4k/moon_layer.composite_cmd understands (the worker maps them to its snake_case arguments)
LAYER_KEYS = ("diameterFrac", "scaleFrom", "scaleTo", "ease", "cx", "cyFrom", "cyTo", "glow", "glowScale")


def _compact(o: Any) -> str:
    return json.dumps(o, separators=(",", ":"))


class MoonLayerStage(StageSupport):
    def __init__(self, d: Any, enabled: bool = True):
        super().__init__(d)
        self.enabled = enabled  # MOON_LAYER flag: on by default; the stage still only runs for a plan that has a moon block

    def request(self, video_id: uuid.UUID) -> dict[str, Any]:
        """The media request, or {"skip": reason}. Raises (the stage waits for the owner) when the plan wants the Moon but there is no master yet."""
        imp = imported.load(self.db, video_id)
        if imp is None or imp.moon is None:
            return {"skip": "No Moon layer: this plan has no moon block."}
        if not self.enabled:
            return {"skip": "Moon layer is switched off (MOON_LAYER=false)."}
        layer = imp.moon["layer"]
        rows = self.db.rows("SELECT a.id, a.uri_hot, a.clip_kind, a.derived_from FROM video v JOIN asset a ON a.id = v.master_asset_id WHERE v.id = %(v)s",
                            {"v": video_id})
        if not rows:
            raise RuntimeError("There is no master video yet: run Assembly first, then the Moon layer.")
        m = rows[0]
        plate_id, plate_uri = m["id"], m["uri_hot"]
        if m["clip_kind"] == KIND and m["derived_from"] is not None:  # redo: composite over the original plate, not over the last composite
            plate_id, plate_uri = m["derived_from"], self.d.assets.uri_of(m["derived_from"])
        cap = imp.moonCaption or {}
        caption = None if not cap.get("text") else {"text": str(cap["text"]), "startSec": float(cap.get("startSec", 0.0)),
                                                    "endSec": float(cap.get("endSec", 4.0))}
        return {"videoId": str(video_id), "plateAssetId": str(plate_id), "plateUri": plate_uri, "imagePath": layer.get("imagePath"),
                "keyBlack": bool(layer.get("keyBlack", True)), "layer": {k: layer[k] for k in LAYER_KEYS if k in layer}, "caption": caption}

    def save(self, video_id: uuid.UUID, req: dict[str, Any], r: dict[str, Any]) -> StageResult:
        prompt = _compact({"layer": req["layer"], "caption": req["caption"], "imagePath": req["imagePath"], "imageSha256": r.get("imageSha256")})
        plate = uuid.UUID(req["plateAssetId"])
        with self.db.transaction():
            asset = self.d.assets.create(NewAsset(video_id, "video", r["videoUri"], "video/mp4", r["width"], r["height"], r["durationSec"], "projectv",
                                                  "ffmpeg Moon layer composite (tools/film4k/moon_layer.py)", prompt, None, lic.LICENCE_OWN_RENDER, False))
            self.db.execute("UPDATE asset SET clip_kind = %(k)s, derived_from = %(p)s WHERE id = %(a)s", {"k": KIND, "p": plate, "a": asset})
            self.db.execute("UPDATE video SET master_asset_id = %(a)s, updated_at = now() WHERE id = %(v)s", {"a": asset, "v": video_id})
        summary = (f"Moon layer composited over the master ({r['width']}x{r['height']}, {r['durationSec']:.1f}s, caption "
                   f"{'on' if req['caption'] else 'off'}). The original plate is kept; the master is now the finished video. Judge the Moon by eye.")
        return StageResult.of(summary, _compact({"masterAssetId": str(asset), "plateAssetId": str(plate)}))
