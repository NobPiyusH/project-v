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
    monkeypatch.setenv("STOCK_VISION_JUDGE", "0")  # these tests have no network and no paid calls


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


def test_credits_file_text_lists_cc_by_and_sfx():
    from workers.activities.assemble import credits_text

    t = credits_text(['"Old TV" by Jane Roe, CC BY 4.0 (https://cc/by), via Wikimedia Commons: https://c/x; trimmed'], ["Hiss by Bo (CC BY)"])
    assert t.startswith("Credits") and "Footage:" in t and "Jane Roe" in t and "Sound effects:" in t and "Hiss by Bo" in t


def test_assembly_mixes_stock_clip_of_any_size_and_fps(monkeypatch, tmp_path):
    """A 1080p30 stock clip fills the 2560x1440 24 fps master: Lanczos scale + crop, light sharpen because it is enlarged."""
    from workers.activities import assemble as A

    assert A.scale_chain(1080, 2560, 1440)[0].endswith("flags=lanczos") and A.SHARPEN in A.scale_chain(1080, 2560, 1440)
    assert A.SHARPEN not in A.scale_chain(1440, 2560, 1440)
    src = tmp_path / "stock.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=3:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
    out = tmp_path / "shot.mp4"
    A._shot_clip({"n": 1, "kind": "footage", "durationSec": 2.0}, src, out, 2560, 1440)
    assert A.media_height(out) == 1440


def test_archive_licence_accepts_only_pd_cc0_ccby():
    ok = S.ia_licence
    assert ok({"licenseurl": "http://creativecommons.org/licenses/publicdomain/"}) == ("Public domain", "http://creativecommons.org/licenses/publicdomain/")
    assert ok({"licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"})[0] == "CC0"
    assert ok({"licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"})[0] == "Public domain"
    assert ok({"licenseurl": "https://creativecommons.org/licenses/by/4.0/"})[0] == "CC BY 4.0"
    assert ok({"rights": "This film is in the public domain"})[0] == "Public domain"
    for bad in ({"licenseurl": "https://creativecommons.org/licenses/by-sa/4.0/"}, {"licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/"},
                {"licenseurl": "https://creativecommons.org/licenses/by-nd/4.0/"}, {"rights": "not in the public domain"}, {}):
        assert ok(bad) is None, bad


def test_archive_search_reads_item_licence_and_skips_unlicensed(monkeypatch):
    def fake(url, headers=None, cache=False):
        if "advancedsearch" in url:
            return {"response": {"docs": [{"identifier": "FactoryA"}, {"identifier": "NoLicence"}]}}
        if url.endswith("/FactoryA"):
            return {"metadata": {"title": "Making Glass", "creator": "Ford Motor Co.", "licenseurl": "http://creativecommons.org/licenses/publicdomain/"},
                    "files": [{"name": "a.mp4", "format": "h.264", "width": "640", "height": "480", "length": "120.5"}, {"name": "a.ogv", "format": "Ogg Video"}]}
        return {"metadata": {"title": "x"}, "files": [{"name": "b.mp4", "format": "h.264"}]}
    monkeypatch.setattr(S, "_get_json", fake)
    got = S.search_archive("factory glass", "16:9")
    assert [c["id"] for c in got] == ["FactoryA"]
    c = got[0]
    assert c["downloadUrl"] == "https://archive.org/download/FactoryA/a.mp4" and c["duration"] == 120.5 and c["author"] == "Ford Motor Co."
    p = S.provenance(c, "t")
    assert p["creditRequired"] is False and "Internet Archive" in p["attribution"]
    assert "Internet Archive" in S.provenance({**c, "licence": "CC BY 4.0"}, "t")["attribution"]


def test_get_json_sends_user_agent_backs_off_on_429_and_caches(monkeypatch, tmp_path):
    import io
    import urllib.error

    monkeypatch.setenv("STOCK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    calls, agents = [], []

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        agents.append(req.get_header("User-agent"))
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow", {"Retry-After": "1"}, None)
        return R(b'{"hits": [1]}')
    monkeypatch.setattr(S.urllib.request, "urlopen", fake_open)
    url = "https://pixabay.com/api/videos/?key=SECRET&q=tv"
    assert S._get_json(url, cache=True) == {"hits": [1]}
    assert len(calls) == 2 and set(agents) == {"ProjectV-StockLane/1.0"}
    assert S._get_json(url, cache=True) == {"hits": [1]} and len(calls) == 2, "second call comes from the 24 h cache"
    assert "SECRET" not in S.redact(url) and "key=***" in S.redact(url)
