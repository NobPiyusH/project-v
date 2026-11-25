"""moon_layer: pure builders (scale curve, ffmpeg argv). One optional smoke run of ffmpeg on synthetic input (free, a second or two)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import moon_layer as M  # noqa: E402


def test_scale_curve_starts_ends_and_only_grows():
    assert M.scale_curve(0, 10, 1.0, 2.2) == 1.0 and M.scale_curve(10, 10, 1.0, 2.2) == pytest.approx(2.2)
    assert M.scale_curve(5, 10, 1.0, 2.2) == pytest.approx(1.6)  # smoothstep is symmetric: halfway at half time
    vals = [M.scale_curve(t / 10, 10, 1.0, 2.2) for t in range(0, 101)]
    assert vals == sorted(vals) and M.scale_curve(99, 10, 1.0, 2.2) == pytest.approx(2.2) and M.scale_curve(-3, 10, 1.0, 2.2) == 1.0
    assert M.scale_curve(2.5, 10, 1, 3, "linear") == pytest.approx(1.5)


def test_scale_curve_refuses_shrinking_and_bad_input():
    for args in ((1, 10, 2.0, 1.0), (1, 10, 0, 1), (1, 0, 1, 2)):
        with pytest.raises(ValueError):
            M.scale_curve(*args)
    with pytest.raises(ValueError):
        M.scale_curve(1, 10, 1, 2, "bounce")


def test_expression_matches_the_python_curve_at_a_few_times():
    expr = M.lerp_expr(1.0, 2.2, 10).replace("min(", "min(").replace("max(", "max(")
    for t in (0.0, 2.5, 5.0, 10.0, 12.0):
        got = eval(expr.replace("t/", f"{t}/").replace("min(max(", "min(max(").replace("(t", f"({t}"), {"min": min, "max": max})  # noqa: S307 - own string
        assert got == pytest.approx(M.scale_curve(t, 10, 1.0, 2.2))


def test_composite_cmd_shape():
    cmd = M.composite_cmd("plate.mp4", "moon.png", "out.mp4", caption="What if the Moon were 10x closer?")
    assert cmd[0] == "ffmpeg" and cmd[cmd.index("-i") + 1] == "plate.mp4" and cmd[-1] == "out.mp4" and "-an" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    for part in ("eval=frame", "gblur", "overlay", "drawtext", "format=yuv420p[v]", "trim=duration=10"):
        assert part in fc
    assert "What if the Moon were 10x closer?" in fc.replace("’", "'") and cmd[cmd.index("-map") + 1] == "[v]"
    assert "drawtext" not in M.composite_cmd("p.mp4", "m.png", "o.mp4")[cmd.index("-filter_complex") + 1]


def test_composite_cmd_validates_arguments():
    with pytest.raises(ValueError):
        M.composite_cmd("p", "m", "o", scale_from=2.0, scale_to=1.0)
    with pytest.raises(ValueError):
        M.composite_cmd("p", "m", "o", glow=1.5)


def test_drawtext_escapes_colons_and_quotes():
    f = M.drawtext_filter("It's 10:1 at 50%", 0, 3, 32)
    assert "It’s 10\\:1 at 50\\%" in f


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_ffmpeg_accepts_the_command_on_synthetic_input(tmp_path):
    plate, src, moon, out = (str(tmp_path / n) for n in ("plate.mp4", "src.png", "moon.png", "out.mp4"))
    run = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], capture_output=True, text=True, check=True)  # noqa: E731
    run("-f", "lavfi", "-i", "color=c=0x102030:s=180x320:d=2:r=8", "-pix_fmt", "yuv420p", plate)
    run("-f", "lavfi", "-i", "color=c=white:s=64x64", "-frames:v", "1", src)
    subprocess.run(M.moon_matte_cmd(src, moon), capture_output=True, check=True)
    cmd = M.composite_cmd(plate, moon, out, w=180, h=320, dur=2, fps=8, caption="hi", fontfile=r"C\:/Windows/Fonts/arial.ttf")
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    n = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out],
                       capture_output=True, text=True).stdout.strip()
    assert n == "16"
