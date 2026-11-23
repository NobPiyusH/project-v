"""Builds the gate's input ("manifest") for a video from Postgres + the stored files, or from a plain JSON file.

The checks never see the database: they get the manifest dict. This is the only module that talks to Postgres (through
`docker exec ... psql`, so it needs no extra Python package) and to S3 (through workers.common.download).

manifest = {videoId, title, cutPath, fps, lines:[{index, text, startSec, durationSec, audioUri}],
            shots:[{n, ord, shotId, lane, description, overlayText, lineIndex, startSec, durationSec,
                    keyframeUri, footageUri, narration}], musicUri, srtUri, totalSec}
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config

PG_CONTAINER = "projectv-postgres-1"

SHOTS_SQL = """
select coalesce(json_agg(r order by r.sord, r.ord), '[]'::json) from (
  select sc.ord as sord, sh.ord, sh.id as "shotId", sh.description, sh.overlay_text as "overlayText",
         sh.duration_target_sec as "durationSec", sh.prompt_json as prompt,
         (select uri_hot from asset a where a.id = sh.keyframe_asset_id) as "keyframeUri",
         (select uri_hot from asset a where a.shot_id = sh.id and a.clip_kind = 'footage' order by a.created_at desc limit 1) as "footageUri"
  from shot sh join scene sc on sc.id = sh.scene_id where sc.video_id = '{vid}') r
"""
SCRIPT_SQL = "select beat_sheet_json from script where video_id = '{vid}' order by version desc limit 1"
MASTER_SQL = "select (select uri_hot from asset where id = master_asset_id), (select uri_hot from asset where id = master_captions_asset_id), title from video where id = '{vid}'"


def psql(sql: str) -> str:
    pw = config.env_value("POSTGRES_PASSWORD", "")
    out = subprocess.run(["docker", "exec", "-e", f"PGPASSWORD={pw}", PG_CONTAINER, "psql", "-U", "projectv", "-d", "projectv",
                          "-At", "-F", "|", "-c", sql], capture_output=True, text=True, check=True, encoding="utf-8")
    return out.stdout.strip()


def insert_cost_entry(row: dict) -> None:
    """Database sink for the Ledger: the same cost_entry row the control plane writes."""
    vid = f"'{row['videoId']}'" if row.get("videoId") else "null"
    note = row["note"].replace("'", "''")
    psql(f"insert into cost_entry (id, video_id, provider_id, amount_inr, amount_usd, fx_rate, units, unit_type, estimated, note) "
         f"values ('{row['id']}', {vid}, '{row['providerId']}', {row['amountInr']}, {row['amountUsd']}, {row['fxRate']}, "
         f"{row['units']}, '{row['unitType']}', {str(row['estimated']).lower()}, '{note}')")


def load_manifest(video_id: str, cut_path: str | None = None) -> dict:
    beat = json.loads(psql(SCRIPT_SQL.format(vid=video_id)))
    starts = {int(k): float(v) for k, v in (beat.get("lineStarts") or {}).items()}
    lines = [{"index": ln["index"], "text": ln["text"], "startSec": starts.get(ln["index"], 0.0),
              "durationSec": float(ln.get("durationSec") or 0), "audioUri": ln.get("audioUri")} for ln in beat["lines"]]
    by_index = {ln["index"]: ln for ln in lines}
    shots = []
    for i, r in enumerate(json.loads(psql(SHOTS_SQL.format(vid=video_id)))):
        p = r.pop("prompt") or {}
        li = p.get("lineIndex")
        shots.append({"n": i + 1, "ord": r["ord"], "shotId": r["shotId"], "lane": p.get("lane") or "picture",
                      "description": r["description"] or "", "overlayText": r["overlayText"] or "", "lineIndex": li,
                      "startSec": float(p["startSec"]) if p.get("startSec") is not None else None,
                      "durationSec": float(r["durationSec"] or 0), "keyframeUri": r["keyframeUri"], "footageUri": r["footageUri"],
                      "keyframePrompt": p.get("keyframePrompt") or "", "diagramSpec": p.get("diagramSpec"),
                      "narration": by_index.get(li, {}).get("text", "") if li is not None else ""})
    master, srt, title = (psql(MASTER_SQL.format(vid=video_id)).split("|") + ["", "", ""])[:3]
    return {"videoId": video_id, "title": title, "cutPath": cut_path, "masterUri": master or None, "srtUri": srt or None,
            "lines": lines, "shots": shots, "musicUri": None, "totalSec": None}


def fetch(uri: str, dest: Path) -> Path:
    from workers.common import download
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        download(uri, dest)
    return dest
