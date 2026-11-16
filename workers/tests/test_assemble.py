"""Assembly end to end with generated media: shot fitting, -14 LUFS master, captions, Premiere package."""

import shutil
import zipfile
from pathlib import Path

import pytest

from workers.activities import assemble as A
from workers.activities import inspect as I
from workers.common import run_ffmpeg


@pytest.fixture()
def local(monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(A, "download", lambda uri, dest: Path(shutil.copy(uri, dest)))
    monkeypatch.setattr("workers.activities.mixing.download", lambda uri, dest: Path(shutil.copy(uri, dest)))
    monkeypatch.setattr(A, "put_file", lambda key, path, mime: str(Path(shutil.copy(path, store / Path(key).name))))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    return tmp_path


def _clip(path: Path, seconds: float, size="480x832"):
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=s={size}:d={seconds}:r=16", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    return str(path)


def test_fit_plan_trims_long_clips_and_slows_short_ones():
    assert A.fit_plan(5.0, 3.0) == (1.0, "trimmed")
    f, note = A.fit_plan(2.0, 4.0)
    assert f == 2.0 and "slow-motion" in note


def test_captions_use_script_words_with_whisper_timing_and_fall_back_evenly():
    heard = [{"word": "Light", "start": 0.1, "end": 0.4}, {"word": "travels", "start": 0.5, "end": 0.9}]
    assert A.line_words("Light travels", heard, 2.0)[1] == {"word": "travels", "start": 0.5, "end": 0.9}
    even = A.line_words("13.8 billion years", None, 3.0)
    assert even[0]["word"] == "13.8" and even[-1]["end"] == pytest.approx(3.0)
    chunks = A.caption_chunks([{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate("one two three four. five".split())], 3)
    assert [c["text"] for c in chunks] == ["one two three", "four.", "five"]


def test_a_short_is_assembled_mastered_captioned_and_packaged(local):
    t = local
    clips = [_clip(t / "a.mp4", 3.0), _clip(t / "b.mp4", 1.5), _clip(t / "c.mp4", 4.0, size="1920x1080")]
    voice = t / "voice.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=220:d=4", "-af", "volume=-12dB", str(voice)])
    music = t / "music.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=440:d=12", "-af", "volume=-20dB", str(music)])
    out = A.assemble_video({
        "videoId": "v1", "title": "Night sky", "aspectRatio": "9:16", "language": "en", "captions": True,
        "shots": [{"n": 1, "uri": clips[0], "kind": "draft", "startSec": 0.0, "durationSec": 2.5, "overlayText": "13.8 billion years"},
                  {"n": 2, "uri": clips[1], "kind": "draft", "startSec": 2.5, "durationSec": 3.0},
                  {"n": 3, "uri": clips[2], "kind": "footage", "startSec": 5.5, "durationSec": 3.5}],
        "narration": [{"uri": str(voice), "startSec": 0.4, "tempo": 1.0, "durationSec": 4.0,
                       "text": "Why is the night sky dark when the universe is full of stars?"}],
        "musicUri": str(music), "totalSec": 9.0})
    assert (out["width"], out["height"], out["fps"]) == (1080, 1920, 30)
    assert out["durationSec"] == pytest.approx(9.0, abs=0.15)
    assert [f["fit"] for f in out["fits"]] == ["trimmed", "slowed 2.00x (looks slow-motion)", "trimmed"]
    assert out["captionChunks"] >= 4 and out["srtUri"].endswith("captions.srt")
    master = Path(out["masterUri"])
    probe = I._probe(master)
    assert (probe["width"], probe["height"]) == (1080, 1920) and probe["audio"]
    assert I.loudness(master)["lufs"] == pytest.approx(-14.0, abs=1.0)
    with zipfile.ZipFile(out["packageUri"]) as z:
        names = z.namelist()
        assert {"project.xml", "captions.srt", "master.mp4", "README.txt"} <= set(names)
        assert sum(n.startswith("media/shot") for n in names) == 3
        xml = z.read("project.xml").decode()
        assert "<xmeml" in xml and "media/shot002.mp4" in xml


def test_captions_never_overlap(tmp_path):
    chunks = [{"text": "one two three", "start": 0.0, "end": 1.0}, {"text": "four five", "start": 1.0, "end": 1.6}]
    ass = tmp_path / "c.ass"
    A.write_ass(chunks, ass, 1080, 1920, True)
    lines = [x for x in ass.read_text(encoding="utf-8").splitlines() if x.startswith("Dialogue")]
    assert lines[0].split(",")[2] == "0:00:01.00", "the first caption ends when the second begins"
