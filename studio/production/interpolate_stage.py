"""Optional local frame-interpolation stage (INTERPOLATE, after MOON_LAYER): RIFE doubles the master's frame rate (24 to 48 fps) on the laptop, audio untouched.
Behind INTERPOLATE_FPS (`local` = on, anything else = off, default off: docs/runbooks/first-live-short.md section 5c).

- The control plane only builds the request and saves the result (database work); the interpolation itself is the media worker's `interpolate_master`.
- The master before the step (the ASSEMBLY master, or the Moon-layer composite) is kept as an asset; the 2x video is a NEW asset (clip_kind 'interpolated',
  derived_from = that master, its own Provenance row: tool, model, fps in and out) and becomes `video.master_asset_id`. A retry or redo starts again from
  the original, never from an earlier interpolation.
- Flag off: the request says `skip` and the stage is recorded as skipped. A missing RIFE program is also a skip (decided by the worker, which owns the path).
  Real failures raise, so the workflow waits for the owner (Approve = continue without it, Reject = retry), never killing the video (rule 2b).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.production.support import StageSupport
from studio.workflow.types import StageResult

KIND = "interpolated"
FACTOR = 2


def _compact(o: Any) -> str:
    return json.dumps(o, separators=(",", ":"))


class InterpolateStage(StageSupport):
    def __init__(self, d: Any, mode: str = "off"):
        super().__init__(d)
        self.mode = (mode or "off").strip().lower()  # INTERPOLATE_FPS: "local" = on

    def request(self, video_id: uuid.UUID) -> dict[str, Any]:
        """The media request, or {"skip": reason}. Raises (the stage waits for the owner) when the flag is on but there is no master yet."""
        if self.mode != "local":
            return {"skip": "Interpolation is off (INTERPOLATE_FPS is not 'local')."}
        rows = self.db.rows("SELECT a.id, a.uri_hot, a.clip_kind, a.derived_from FROM video v JOIN asset a ON a.id = v.master_asset_id WHERE v.id = %(v)s", {"v": video_id})
        if not rows:
            raise RuntimeError("There is no master video yet: run Assembly first, then Interpolation.")
        m = rows[0]
        src_id, src_uri = m["id"], m["uri_hot"]
        if m["clip_kind"] == KIND and m["derived_from"] is not None:  # redo: interpolate the original again, never an earlier interpolation
            src_id, src_uri = m["derived_from"], self.d.assets.uri_of(m["derived_from"])
        return {"videoId": str(video_id), "sourceAssetId": str(src_id), "sourceUri": src_uri, "factor": FACTOR}

    def save(self, video_id: uuid.UUID, req: dict[str, Any], r: dict[str, Any]) -> StageResult:
        prompt = _compact({"tool": r["tool"], "model": r["model"], "fpsIn": r["fpsIn"], "fpsOut": r["fpsOut"], "framesIn": r["framesIn"], "framesOut": r["framesOut"]})
        source = uuid.UUID(req["sourceAssetId"])
        with self.db.transaction():
            asset = self.d.assets.create(NewAsset(video_id, "video", r["videoUri"], "video/mp4", r["width"], r["height"], r["durationSec"], "projectv",
                                                  f"{r['tool']} {r['model']} (local frame interpolation)", prompt, None, lic.LICENCE_OWN_RENDER, False))
            self.db.execute("UPDATE asset SET clip_kind = %(k)s, derived_from = %(p)s WHERE id = %(a)s", {"k": KIND, "p": source, "a": asset})
            self.db.execute("UPDATE video SET master_asset_id = %(a)s, updated_at = now() WHERE id = %(v)s", {"a": asset, "v": video_id})
        summary = (f"Interpolated the master with {r['tool']} {r['model']}: {r['fpsIn']:g} to {r['fpsOut']:g} fps, {r['width']}x{r['height']}, {r['durationSec']:.1f}s "
                   f"in {r['seconds']:.0f}s on this laptop; audio untouched. The earlier master is kept; the master is now the {r['fpsOut']:g} fps video. Judge the motion by eye.")
        return StageResult.of(summary, _compact({"masterAssetId": str(asset), "sourceAssetId": str(source)}))
