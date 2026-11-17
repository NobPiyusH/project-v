"""Sound effects: consecutive shots share one ambience; effects are mixed under the voice; no key = no effects."""

from pathlib import Path

from workers.activities import inspect as I
from workers.activities import sfx
from workers.activities.mixing import mix_audio
from workers.common import run_ffmpeg


def test_consecutive_shots_with_the_same_sound_become_one_ambience():
    shots = [{"startSec": 0, "durationSec": 2, "sfxQuery": "Underwater ambience"},
             {"startSec": 2, "durationSec": 3, "sfxQuery": "underwater ambience"},
             {"startSec": 5, "durationSec": 2, "sfxQuery": ""},
             {"startSec": 7, "durationSec": 2, "sfxQuery": "train interior"}]
    assert sfx.segments(shots) == [{"query": "underwater ambience", "startSec": 0.0, "endSec": 5.0},
                                   {"query": "train interior", "startSec": 7.0, "endSec": 9.0}]


def test_without_a_key_there_are_no_sound_effects(monkeypatch, tmp_path):
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    assert sfx.fetch([{"query": "rain", "startSec": 0, "endSec": 3}], tmp_path) == ([], [])


def test_a_short_sound_loops_to_fill_its_segment_under_the_voice(monkeypatch, tmp_path):
    monkeypatch.setattr("workers.activities.mixing.download", lambda uri, dest: Path(uri))
    voice = tmp_path / "v.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=220:d=2", str(voice)])
    fx = tmp_path / "fx.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "anoisesrc=d=1:a=0.3", str(fx)])  # 1 s of noise, must loop to 4 s
    out = mix_audio([{"uri": str(voice), "startSec": 0, "tempo": 1.0}], None, 6.0, tmp_path, tmp_path / "m.wav",
                    sfx=[{"path": fx, "startSec": 1.0, "durationSec": 4.0}])
    assert out is not None and I.loudness(out)["lufs"] > -40
