"""Worker -> Java contract: the keys a worker activity returns must exist as components of its Java record.

22 Nov 2026: stock_fetch returned sourceWidth/sourceHeight, Java's StockResult did not have them, and the television
video's workflow wedged. Java now ignores unknown fields (LenientConverters, tested in WorkerContractTest), but a key the
record does not know is silently dropped, so a NEW key must be a decision: add it to the record, or list it in EXTRA below
with the reason. This test fails until one of the two is done.
"""
import re
import shutil
from pathlib import Path

import pytest

from workers.activities import stock as S
from workers.tests.test_stock import _cand
from workers.common import run_ffmpeg

MEDIA_TYPES = Path(__file__).resolve().parents[2] / "control-plane/src/main/java/com/projectv/media/MediaTypes.java"

# Worker keys the Java record intentionally does not carry (ignored on purpose).
EXTRA = {
    "StockResult": {"sourceWidth", "sourceHeight"},  # source clip size before trimming/scaling; informational
}


def record_components(name: str) -> set[str]:
    """Component names of `public record <name>(...)` in MediaTypes.java (generics-safe split on top-level commas)."""
    src = MEDIA_TYPES.read_text(encoding="utf-8")
    m = re.search(r"record\s+" + re.escape(name) + r"\s*\(", src)
    assert m, f"record {name} not found in MediaTypes.java"
    depth, i, start, parts = 1, m.end(), m.end(), []
    while depth:
        c = src[i]
        if c in "(<":
            depth += 1
        elif c in ")>":
            depth -= 1
        elif c == "," and depth == 1:
            parts.append(src[start:i])
            start = i + 1
        i += 1
    parts.append(src[start:i - 1])
    return {p.split()[-1] for p in parts if p.strip()}


def check(record: str, payload: dict) -> None:
    unknown = set(payload) - record_components(record) - EXTRA.get(record, set())
    assert not unknown, (f"worker returns {sorted(unknown)} which Java record {record} does not have: add the component to "
                         f"MediaTypes.java or list it in EXTRA with a reason")


def test_the_parser_reads_the_real_record():
    assert {"found", "uri", "provenance", "durationSec"} <= record_components("StockResult")


def test_stock_miss_keys_match_the_java_record():
    check("StockResult", S._miss("no candidates", ["q"], []))


def test_stock_found_keys_match_the_java_records(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=640x360:d=6:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
    monkeypatch.setenv("PEXELS_API_KEY", "fake")
    monkeypatch.setitem(S.SEARCHERS, "pexels", lambda q, a: [_cand("77", dur=6.0, w=1920, h=1080)])
    monkeypatch.setattr(S, "_download", lambda url, dest, mb: shutil.copy(src, dest))
    monkeypatch.setattr(S, "put_file", lambda key, path, mime: f"s3://b/{key}")
    out = S.stock_fetch({"videoId": "v1", "shotId": "s1", "shotNumber": 2, "description": "living room with a lit television",
                         "aspectRatio": "16:9", "durationSec": 3.0, "sources": ["pexels"]})
    assert out["found"] is True
    check("StockResult", out)
    check("StockProvenance", out["provenance"])
