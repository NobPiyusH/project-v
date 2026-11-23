"""What the dashboard shows for a video's production. Port of ProductionReadModel."""

from __future__ import annotations

import json
import uuid
from typing import Any

from studio.media.wire import to_json
from studio.production.support import StageSupport


class ProductionReadModel(StageSupport):
    def production(self, video_id: uuid.UUID) -> dict[str, Any]:
        db = self.db
        out: dict[str, Any] = {}
        # Postgres text[] is converted to JSON in SQL so the driver's array type never reaches the response.
        rows = db.rows("SELECT facts_json::text AS facts, to_json(COALESCE(queries, '{}'))::text AS queries, version FROM research "
                       "WHERE video_id = %(v)s ORDER BY version DESC LIMIT 1", {"v": video_id})
        out["research"] = None if not rows else {**rows[0], "queries": json.loads(rows[0]["queries"])}
        concept = self.latest_concept(video_id)
        out["concept"] = None if concept is None else concept.model_dump()
        script = self.latest_script(video_id)
        out["script"] = None if script is None else to_json(script)
        music = db.rows("SELECT asset_id, bpm FROM music_cue WHERE video_id = %(v)s", {"v": video_id})
        out["music"] = music[0] if music else None
        shots = []
        for s in self.shots(video_id):
            row = {k: v for k, v in s.items() if k != "keyframe_uri"}
            row["prompt_json"] = json.loads(s["prompt_json"])
            shots.append(row)
        out["shots"] = shots
        animatic = db.rows("SELECT animatic_asset_id FROM video WHERE id = %(v)s", {"v": video_id})
        out["animaticAssetId"] = animatic[0]["animatic_asset_id"] if animatic else None
        # True when any active keyframe is newer than the animatic: the owner redid shots and should rebuild it.
        out["animaticOutdated"] = db.rows(
            "SELECT EXISTS (SELECT 1 FROM video v JOIN asset an ON an.id = v.animatic_asset_id JOIN scene sc ON sc.video_id = v.id "
            "JOIN shot s ON s.scene_id = sc.id JOIN asset k ON k.id = s.keyframe_asset_id WHERE v.id = %(v)s AND k.created_at > an.created_at) AS x",
            {"v": video_id})[0]["x"]
        # Voice takes newer than the animatic also make it outdated; edited-but-not-revoiced lines are counted.
        lines = [] if script is None else script.lines
        active_voice = [ln.audio_asset_id for ln in lines if ln.audio_asset_id is not None]
        if active_voice and not out["animaticOutdated"]:
            out["animaticOutdated"] = db.rows(
                "SELECT EXISTS (SELECT 1 FROM video v JOIN asset an ON an.id = v.animatic_asset_id, asset a WHERE v.id = %(v)s AND a.id = ANY(%(ids)s) "
                "AND a.created_at > an.created_at) AS x", {"v": video_id, "ids": active_voice})[0]["x"]
        out["linesNeedingVoice"] = sum(1 for ln in lines if ln.needs_voice is True)
        out["redoingLines"] = sum(1 for ln in lines if ln.voice_status == "REDOING")
        out["redoingShots"] = db.rows("SELECT count(*) AS n FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE sc.video_id = %(v)s AND s.status = 'REDOING'",
                                      {"v": video_id})[0]["n"]
        return out
