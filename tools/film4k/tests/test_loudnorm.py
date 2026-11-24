"""Final loudness normalisation: pure command builders plus a small synthetic-audio run (needs only a local ffmpeg)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edit  # noqa: E402
import sound  # noqa: E402

M = {"i": -21.2, "tp": -1.4, "lra": 4.3, "thresh": -31.5, "offset": 0.2}
JSON = '[Parsed_loudnorm_0 @ 0x1] \n{\n\t"input_i" : "-21.20",\n\t"input_tp" : "-1.40",\n\t"input_lra" : "4.30",\n\t"input_thresh" : "-31.50",\n\t"output_i" : "-14.0",\n\t"target_offset" : "0.20"\n}\n'


def test_measure_filter_has_the_targets():
    f = sound.loudnorm_measure_filter()
    assert "I=-14.0" in f and "TP=-1.5" in f and "LRA=11.0" in f and "print_format=json" in f


def test_parse_loudnorm_json():
    assert sound.parse_loudnorm_json(JSON) == M
    with pytest.raises(ValueError):
        sound.parse_loudnorm_json("no json here")
    with pytest.raises(ValueError):
        sound.parse_loudnorm_json('{"input_i": "-20"}')


def test_apply_filter_is_linear_with_measurements():
    f = sound.loudnorm_apply_filter(M)
    assert "linear=true" in f and "measured_I=-21.2" in f and "measured_thresh=-31.5" in f and "offset=0.2" in f
    assert f.endswith("aresample=48000")


def test_linear_feasibility_depends_on_peak_headroom():
    assert not sound.linear_feasible(M)  # -1.4 dBTP + 7.2 dB gain would clip
    assert sound.linear_feasible({**M, "tp": -12.0})


def test_gain_limit_filter_orders_gain_then_limiters():
    f = sound.gain_limit_filter(10.0)
    assert f.startswith("volume=10.00dB,alimiter=") and f.count("alimiter") == 2 and f.endswith("aresample=48000")


def test_flag_default_on_and_switchable():
    assert sound.loudnorm_enabled("") and sound.loudnorm_enabled("1")
    assert not any(sound.loudnorm_enabled(v) for v in ("0", "false", "OFF", " no "))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_quiet_peaky_synthetic_audio_lands_near_target(tmp_path):
    src, dst = tmp_path / "quiet.wav", tmp_path / "norm.wav"
    graph = "sine=f=220:d=6:r=48000,volume=0.05[a];sine=f=60:d=0.2:r=48000,volume=0.8,adelay=2000|2000,apad=whole_dur=6[h];[a][h]amix=inputs=2:normalize=0"
    subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph, "-c:a", "pcm_s16le", str(src), "-y"], check=True)
    before = edit.measure_loudness(src)["i"]
    edit.normalise_loudness(src, dst)
    after = edit.measure_loudness(dst)
    assert before < -20 and abs(after["i"] - sound.LOUDNORM_I) < 1.5
    assert after["tp"] <= sound.LOUDNORM_TP + 0.5
