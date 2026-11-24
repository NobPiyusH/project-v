"""Write replay: fire the Python write routes (POST/PUT/DELETE) at a THROWAWAY COPY of the database, then compare every row they wrote with rows the
Java app wrote earlier in the same table, by form: which columns are null, the JSON key sets, number-vs-text, digit/uuid patterns of short strings.

    docker exec projectv-postgres-1 psql -U projectv -d postgres -c 'CREATE DATABASE projectv_replay'
    docker exec projectv-postgres-1 sh -c 'pg_dump -U projectv projectv | psql -q -U projectv -d projectv_replay'
    POSTGRES_DB=projectv_replay python -m studio.contract.writes

It refuses to run on a database called projectv (the live one). Nothing is spent: fake LLM, fake Temporal gateway, no pods. Delete the copy afterwards (it holds
owner data): docker exec projectv-postgres-1 psql -U projectv -d postgres -c 'DROP DATABASE projectv_replay'."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from types import SimpleNamespace

TABLES = ["dream_short", "dream_lesson", "dream_review", "autopilot_setting", "autopilot_idea", "video", "script", "shot", "producer_setting", "producer_rule",
          "stage_run", "cost_entry", "budget_modal"]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def form(v):
    """The structure of a value: what a reader of the row could tell apart (not the owner's words)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, dict):
        return {k: form(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [form(v[0])] if v else []
    s = str(v)
    if UUID.fullmatch(s):
        return "uuid"
    if re.fullmatch(r"\d{4}-\d\d-\d\d[T ][\d:.+Z-]+", s):
        return "ts"
    if len(s) > 24 or " " in s:
        return "text"
    return "tok:" + re.sub(r"\d", "9", s)


def merge(a, b):
    """A form that fits both (null fits anything, int/float interchangeable)."""
    if a == "null":
        return b
    if b == "null":
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge(a.get(k, "null"), b.get(k, "null")) for k in sorted(set(a) | set(b))}
    return a


def drift(java, py, where="") -> list[str]:
    if java == "null" or py == "null":
        return []
    if where.endswith(".stages"):
        return []  # a map keyed by whatever stages the owner set, not a record
    if isinstance(java, dict) and isinstance(py, dict):
        out = [f"{where}.{k}: missing here" for k in java if k not in py] + [f"{where}.{k}: extra here" for k in py if k not in java]
        for k in java.keys() & py.keys():
            out += drift(java[k], py[k], f"{where}.{k}")
        return out
    if isinstance(java, list) and isinstance(py, list):
        return drift(java[0], py[0], where + "[0]") if java and py else []
    if java == py:
        return []
    if isinstance(java, str) and isinstance(py, str) and java.startswith("tok:") and py.startswith("tok:"):
        return []  # short words differ (an enum value): not a form difference
    return [f"{where}: Java {java!r}, here {py!r}"]


def snapshot(db) -> dict[str, dict[str, dict]]:
    snap = {}
    for t in TABLES:
        try:
            rows = db.rows(f"SELECT row_to_json(x) AS j FROM {t} x")
        except Exception:
            snap[t] = {}
            continue
        d = {}
        for i, r in enumerate(rows):
            j = r["j"] if isinstance(r["j"], dict) else json.loads(r["j"])
            d[str(j.get("id", j.get("video_id", i)))] = j
        snap[t] = d
    return snap


def calls(ids) -> list[tuple[str, str, object]]:
    """The writes: (method, path, json body). ids: existing Dream and video ids in the copy."""
    d, v = ids["dream"], ids["video"]
    return [
        ("POST", "/api/dream", {"idea": "replay: a lighthouse in fog", "look": "cinematic", "shots": 2, "quality": "SHORT", "defaultMode": "REVIEW",
                                "modes": {"WRITE": "MANUAL"}, "reviewMinutes": 10, "hookTitle": True, "hookText": "WAIT", "hookColor": "#ffcc00"}),
        ("POST", f"/api/dream/{d}/hook", {"text": "LOOK", "color": "#ffffff"}),
        ("POST", f"/api/dream/{d}/paths", {"a": "Go left", "b": "Go right"}),
        ("POST", f"/api/dream/{d}/reviews", {"rating": 4, "note": "replay note"}),
        ("POST", "/api/autopilot/ideas", {"idea": "replay idea", "look": "dreamcore"}),
        ("PUT", "/api/autopilot/settings", {"enabled": False, "startTime": "09:30", "startMode": "AUTO", "reviewMinutes": 10, "dailyCapInr": 100,
                                            "stepModes": {}, "look": "dreamcore", "shots": 3, "useScout": False, "privacy": "private"}),
        ("POST", "/api/videos", {"prompt": "replay: a calm sea", "defaultMode": "REVIEW", "modes": {"SCRIPT": "MANUAL"}, "reviewMinutes": 15}),
        ("PUT", f"/api/videos/{v}/review-minutes", {"minutes": 7}),
        ("PUT", "/api/producer/settings", {"trust": "advise"}),
        ("POST", "/api/producer/rules", {"stage": "SCRIPT", "text": "replay rule"}),
        ("POST", "/api/internal/modal-billing", {"creditsUsedUsd": 1.5, "meteredUsd": 0.5}),
    ]


def main(argv=None) -> int:
    from fastapi.testclient import TestClient

    from studio.api.app import create_app
    from studio.api.video_wiring import build_video_context
    from studio.api.wiring import build_dream_context
    from studio.config import Settings
    from studio.db import PgDb
    from studio.tests.dworld import FakeGateway, FakeLlm

    s = Settings.from_env()
    if s.postgres_db == "projectv":
        print("Refusing to run on the live database 'projectv': set POSTGRES_DB to a throwaway copy.")
        return 2
    db = PgDb(s.dsn)
    s = replace(s, dream_routes=True, video_routes=True)
    stub = SimpleNamespace(deciding=lambda: False, mark_acted=lambda *a: None)
    gw = FakeGateway()
    dctx = build_dream_context(db, s, llm=FakeLlm(), gateway=gw, producer_service=stub)
    vctx = build_video_context(db, s, llm=FakeLlm(), gateway=gw, media_urls=None, dream_ctx=dctx, researcher=object())
    c = TestClient(create_app(db=db, settings=s, dream_ctx=dctx, video_ctx=vctx), raise_server_exceptions=False)
    dream = db.rows("SELECT id FROM dream_short ORDER BY created_at DESC LIMIT 1")[0]["id"]
    video = db.rows("SELECT id FROM video ORDER BY created_at DESC LIMIT 1")[0]["id"]
    before = snapshot(db)
    problems, ran = [], 0
    for method, path, body in calls({"dream": dream, "video": video}):
        r = c.request(method, path, json=body)
        ran += 1
        print(f"{method} {path.replace(str(dream), '{dream}').replace(str(video), '{video}')} -> {r.status_code}")
        if r.status_code >= 400:
            problems.append(f"{method} {path}: {r.status_code} {r.text[:200]}")
    after = snapshot(db)
    for t in TABLES:
        java_rows = list(before[t].values())
        if not java_rows:
            continue
        java_form = {}
        for row in java_rows[:200]:
            f = form(row)
            java_form = f if not java_form else merge(java_form, f)
        for key, row in after[t].items():
            if key in before[t] and before[t][key] == row:
                continue
            if key in before[t]:
                # an update: compare against the row's own earlier form (an update must not change what a column looks like)
                d = drift(form(before[t][key]), form(row), t)
            else:
                d = drift(java_form, form(row), t)
            for x in d:
                problems.append(f"{t} {'updated' if key in before[t] else 'new'} row: {x}")
    print(f"\n{ran} writes replayed on {s.postgres_db}; {len(problems)} differences")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
