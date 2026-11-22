"""Stock footage lane: queries, ranking, clean skips, fallback signal, provenance and CC BY credit text. No network, no keys."""

import shutil

import pytest

from workers.activities import stock as S
from workers.common import run_ffmpeg


def _cand(id="1", source="pexels", w=1920, h=1080, dur=10.0, text="living room television", **kw):
    return {"source": source, "id": id, "pageUrl": f"https://x/{id}", "downloadUrl": f"file://{id}", "width": w, "height": h,
            "duration": dur, "author": "Ann", "authorUrl": "", "licence": "Pexels License", "licenceUrl": "", "text": text, **kw}


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for k in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_keyword_fallback_when_no_model():
    qs = S.build_queries("A repair bench with hands unscrewing a back panel of an old television", model=None)
    assert 1 <= len(qs) <= 3 and all(len(q.split()) <= 3 for q in qs)
    assert "repair" in qs[0] and "a" not in qs[0].split()


def test_model_queries_used_and_failure_falls_back(monkeypatch):
    monkeypatch.setattr(S, "_llm_queries", lambda d, m, n: ["old tv repair", "screwdriver panel", "workshop hands"])
    assert S.build_queries("x y z", model="m") == ["old tv repair", "screwdriver panel", "workshop hands"]

    def boom(*a):
        raise RuntimeError("quota")
    monkeypatch.setattr(S, "_llm_queries", boom)
    assert S.build_queries("rooftop antenna at sunset", model="m")[0].startswith("rooftop antenna")


def test_ranking_prefers_hd_long_enough_relevant_and_drops_wrong_orientation():
    good = _cand("good")
    sd = _cand("sd", w=1280, h=720)
    short = _cand("short", dur=2.0)  # far shorter than the 8 s shot: dropped
    vertical = _cand("vert", w=1080, h=1920)
    offtopic = _cand("off", text="forest river")
    ranked = S.rank([offtopic, sd, short, vertical, good], ["living room television"], "16:9", 8.0)
    assert [c["id"] for c in ranked] == ["good", "sd", "off"]
    assert [c["id"] for c in S.rank([vertical, good], ["tv"], "9:16", 4.0)] == ["vert"]


def test_pick_file_smallest_that_reaches_1080_else_largest():
    files = [{"url": "a", "width": 3840, "height": 2160}, {"url": "b", "width": 1920, "height": 1080},
             {"url": "c", "width": 1280, "height": 720}]
    assert S.pick_file(files)["url"] == "b"
    assert S.pick_file(files[2:])["url"] == "c"
    assert S.pick_file([]) is None


def test_missing_key_skips_source_cleanly_and_no_source_means_fallback(monkeypatch):
    usable, skipped = S.available_sources(["pexels", "pixabay", "commons"])
    assert usable == ["commons"] and len(skipped) == 2 and all("API_KEY" in s for s in skipped)
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    assert S.available_sources(["pexels"])[0] == ["pexels"]


def test_no_key_at_all_returns_not_found_without_error():
    out = S.stock_fetch({"videoId": "v", "shotId": "s", "description": "tv", "sources": ["pexels"]})
    assert out["found"] is False and "PEXELS_API_KEY" in out["reason"]
    assert S.stock_fetch({"videoId": "v", "shotId": "s", "description": "tv", "sources": []})["found"] is False


def test_no_match_signals_fallback_and_a_failing_source_never_raises(monkeypatch):
    monkeypatch.setitem(S.SEARCHERS, "commons", lambda q, a: [])
    out = S.stock_fetch({"videoId": "v", "shotId": "s", "description": "family in a dim room", "sources": ["commons"]})
    assert out["found"] is False and out["reason"] == "no candidates"

    def broken(q, a):
        raise OSError("down")
    monkeypatch.setitem(S.SEARCHERS, "commons", broken)
    out = S.stock_fetch({"videoId": "v", "shotId": "s", "description": "family in a dim room", "sources": ["commons"]})
    assert out["found"] is False and any("OSError" in s for s in out["skipped"])


def test_provenance_and_cc_by_attribution():
    c = _cand("p1", source="commons", licence="CC BY 4.0", licenceUrl="https://creativecommons.org/licenses/by/4.0",
              title="Old TV.webm", id_title=None)
    c["id"], c["author"], c["pageUrl"] = "File:Old TV.webm", "Jane Roe", "https://commons.wikimedia.org/wiki/File:Old_TV.webm"
    p = S.provenance(c, "2026-11-22T10:00:00+00:00")
    assert p["creditRequired"] is True and p["source"] == "commons" and p["sourceId"] == "File:Old TV.webm"
    for part in ("Old TV.webm", "Jane Roe", "CC BY 4.0", "creativecommons.org", "commons.wikimedia.org", "trimmed"):
        assert part in p["attribution"]
    assert p["fetchedAt"] == "2026-11-22T10:00:00+00:00"
    pd = S.provenance({**c, "licence": "Public domain"}, "t")
    assert pd["creditRequired"] is False
    assert S.provenance(_cand("9"), "t")["creditRequired"] is True  # Pexels: its API guideline asks for a Pexels link
    assert S.provenance(_cand("9", source="pixabay", licence="Pixabay Content License"), "t")["creditRequired"] is False


def test_commons_search_filters_licences(monkeypatch):
    def page(i, lic, name):
        return {"index": i, "title": f"File:{name}.webm", "imageinfo": [{"url": f"https://u/{name}", "width": 1920, "height": 1080,
                "descriptionurl": f"https://c/{name}", "metadata": [{"name": "length", "value": 12.5}],
                "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a>Bob</a>"}}}]}
    monkeypatch.setattr(S, "_get_json", lambda url, headers=None: {"query": {"pages": {
        "1": page(1, "CC BY 4.0", "a"), "2": page(2, "CC BY-SA 4.0", "b"), "3": page(3, "CC BY-NC 2.0", "c"),
        "4": page(4, "Public domain", "d")}}})
    got = S.search_commons("tv", "16:9")
    assert [c["title"] for c in got] == ["a.webm", "d.webm"]
    assert got[0]["author"] == "Bob" and got[0]["duration"] == 12.5


def test_end_to_end_picks_trims_and_returns_provenance(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=640x360:d=6:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
    monkeypatch.setenv("PEXELS_API_KEY", "fake")
    monkeypatch.setitem(S.SEARCHERS, "pexels", lambda q, a: [_cand("77", dur=6.0, w=1920, h=1080)])
    monkeypatch.setattr(S, "_download", lambda url, dest, mb: shutil.copy(src, dest))
    saved = {}
    monkeypatch.setattr(S, "put_file", lambda key, path, mime: saved.setdefault(key, shutil.copy(path, tmp_path / "o.mp4")) and f"s3://b/{key}")
    out = S.stock_fetch({"videoId": "v1", "shotId": "s1", "shotNumber": 2, "description": "living room with a lit television",
                         "aspectRatio": "16:9", "durationSec": 3.0, "sources": ["pexels"]})
    assert out["found"] is True and out["uri"] == "s3://b/videos/v1/stock/shot002-pexels-77.mp4"
    assert abs(out["durationSec"] - 3.0) < 0.2
    assert out["provenance"]["source"] == "pexels" and out["provenance"]["sourceId"] == "77"
    assert out["provenance"]["fetchedAt"] and "Pexels" in out["provenance"]["attribution"]


def test_oversized_download_is_skipped_to_fallback(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "fake")
    monkeypatch.setitem(S.SEARCHERS, "pexels", lambda q, a: [_cand("big")])

    def too_big(url, dest, mb):
        raise ValueError("over cap")
    monkeypatch.setattr(S, "_download", too_big)
    out = S.stock_fetch({"videoId": "v", "shotId": "s", "description": "television", "sources": ["pexels"]})
    assert out["found"] is False and any("ValueError" in s for s in out["skipped"])
