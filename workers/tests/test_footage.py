"""NASA footage: search filtering, and cutting a vertical clip from a 16:9 source with the upscale reported."""

import shutil
from pathlib import Path

from workers.activities import footage as F
from workers.common import run_ffmpeg


def _item(nasa_id, title, **extra):
    return {"data": [{"nasa_id": nasa_id, "title": title, "center": "GSFC", "secondary_creator": "Joy Ng",
                      "photographer": "None", "date_created": "2016-06-24T00:00:00Z", **extra}]}


def test_search_skips_talking_heads_and_keeps_credits(monkeypatch):
    monkeypatch.setattr(F, "_get_json", lambda url: {"collection": {"items": [
        _item("A_Narrated", "The Dynamic Solar Magnetic Field - Narrated"),
        _item("B_Flare", "Solar flare erupts", description_508="An X-class flare"),
        _item("C_Interview", "Scientist interview on the Sun"),
        _item("D Space", "Prominence eruption"),
    ]}})
    got = F.search("solar flare")
    assert [c["nasaId"] for c in got] == ["B_Flare", "D Space"]
    assert got[0]["credit"] == "GSFC, Joy Ng", "the 'None' photographer is dropped"
    assert got[0]["description"] == "An X-class flare"
    assert got[1]["previewUrl"].endswith("/D%20Space/D%20Space~small.mp4")


def test_vertical_clip_is_cut_from_a_wide_source_and_reports_the_enlargement(monkeypatch, tmp_path):
    src = tmp_path / "nasa~orig.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=1280x720:d=8:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
    monkeypatch.setattr(F, "_best_source", lambda nasa_id: str(src))
    saved = {}
    monkeypatch.setattr(F, "put_file", lambda key, path, mime: saved.setdefault(key, shutil.copy(path, tmp_path / "out.mp4")) and f"s3://projectv/{key}")
    out = F.fetch_footage({"videoId": "v1", "shotId": "s1", "shotNumber": 3, "nasaId": "GSFC/X 1", "startSec": None,
                           "durationSec": 2.5, "aspectRatio": "9:16"})
    assert out["uri"] == "s3://projectv/videos/v1/footage/shot003-GSFC_X_1.mp4"
    assert (out["width"], out["height"]) == (1080, 1920)
    assert abs(out["durationSec"] - 2.5) < 0.2
    assert out["startSec"] == 2.0, "skips the opening title card"
    assert out["upscale"] == 2.67, "a 405x720 crop of a 720p source fills 1080x1920 only by enlarging it"
    probe = F._probe(str(Path(tmp_path / "out.mp4")))
    assert (probe["width"], probe["height"]) == (1080, 1920)
