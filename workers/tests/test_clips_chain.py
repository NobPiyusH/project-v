"""Chained shots: clip N+1 starts from the LAST frame of clip N (pod and Modal paths), with fakes only (no GPU)."""

import subprocess
from pathlib import Path

import pytest

from workers.activities import clips as C
from workers.common import ffmpeg_bin, run_ffmpeg
from workers.pod import client


def _two_colour_mp4(path: Path, first: str, last: str) -> bytes:
    """1 s of `first` then 1 s of `last`: the first frame and the last frame differ."""
    run_ffmpeg(["-f", "lavfi", "-i", f"color=c={first}:s=64x64:r=16:d=1", "-f", "lavfi", "-i", f"color=c={last}:s=64x64:r=16:d=1",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv444p", str(path)])
    return path.read_bytes()


def _pixel(png: bytes, tmp: Path) -> tuple[int, int, int]:
    f = tmp / "p.png"
    f.write_bytes(png)
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(f), "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return raw[0], raw[1], raw[2]


def _job(n: int, **extra) -> dict:
    return {"shotId": f"abcdefg{n}-1", "shotNumber": n, "take": 1, "kind": "dream", "mode": "fast", "imageUri": f"s3://b/pic{n}.png",
            "prompt": f"move {n}", "width": 64, "height": 64, "numFrames": 33, "seed": n, **extra}


def test_last_frame_is_the_end_of_the_clip_not_the_start(tmp_path):
    r, g, b = _pixel(C.last_frame_png(_two_colour_mp4(tmp_path / "c.mp4", "red", "blue")), tmp_path)
    assert b > 150 and r < 100 and g < 100


def test_start_image_chains_from_the_previous_clip_and_falls_back_to_the_picture(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "get_bytes", lambda uri: uri.encode())
    clip = _two_colour_mp4(tmp_path / "c.mp4", "red", "blue")
    assert C.start_image(_job(1), True, {}, set()) == b"s3://b/pic1.png"                    # first shot: its own picture
    assert C.start_image(_job(2), False, {1: clip}, set()) == b"s3://b/pic2.png"            # chaining off: unchanged
    assert C.start_image(_job(2), True, {1: clip}, set())[:4] == b"\x89PNG"                 # chained: previous last frame
    monkeypatch.setattr(C, "last_frame_png", lambda mp4: (_ for _ in ()).throw(RuntimeError("bad file")))
    with pytest.raises(C.ChainBroken, match="could not be read"):                          # extraction failed: no silent
        C.start_image(_job(2), True, {1: clip}, set())                                      # fallback to another picture
    with pytest.raises(C.ChainBroken, match="no clip yet"):
        C.start_image(_job(2, imageUri=None), True, {}, set())
    with pytest.raises(C.ChainBroken):
        C.start_image(_job(3), True, {}, {2})


def test_start_image_uses_the_clip_before_this_batch(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "get_bytes", lambda uri: seen.append(uri) or b"mp4-before")
    monkeypatch.setattr(C, "last_frame_png", lambda mp4: b"PNG:" + mp4)
    out = C.start_image(_job(3, chainFromUri="s3://b/shot002.mp4"), True, {}, set())   # shot 2 was made in an earlier batch
    assert out == b"PNG:mp4-before" and seen == ["s3://b/shot002.mp4"]


def _pod_setup(monkeypatch, fail_shot=None):
    sent = []
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"pic:" + uri.encode())
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    monkeypatch.setattr(C, "last_frame_png", lambda mp4: b"last:" + mp4)

    def run(url, kind, payload, ready, beat=None):
        import base64
        sent.append(base64.b64decode(payload["image"]))
        if fail_shot is not None and payload["prompt"] == f"move {fail_shot}":
            raise RuntimeError("pod lost")
        return f"mp4-{payload['prompt'][-1]}".encode(), {"seconds": 5, "frames": 33, "fps": 16}

    monkeypatch.setattr(client, "run", run)
    return sent


def test_pod_chain_runs_in_order_and_each_clip_starts_from_the_last_frame(monkeypatch):
    sent = _pod_setup(monkeypatch)
    clips, stopped = C._pod({"gpuUrl": "https://g.example/", "chain": True, "jobs": [_job(1), _job(2), _job(3)]}, "http://x", "v")
    assert not stopped and [c["error"] for c in clips] == [None, None, None]
    assert sent == [b"pic:s3://b/pic1.png", b"last:mp4-1", b"last:mp4-2"]


def test_pod_without_chain_uses_every_shots_own_picture(monkeypatch):
    sent = _pod_setup(monkeypatch)
    C._pod({"gpuUrl": "https://g.example/", "jobs": [_job(1), _job(2)]}, "http://x", "v")
    assert sent == [b"pic:s3://b/pic1.png", b"pic:s3://b/pic2.png"]


def test_a_failed_chained_clip_fails_the_ones_after_it_but_keeps_the_earlier(monkeypatch):
    sent = _pod_setup(monkeypatch, fail_shot=2)
    clips, _ = C._pod({"gpuUrl": "https://g.example/", "chain": True, "jobs": [_job(1), _job(2), _job(3)]}, "http://x", "v")
    assert clips[0]["uri"] and clips[1]["uri"] is None and "pod lost" in clips[1]["error"]
    assert clips[2]["uri"] is None and "shot 2 has no clip" in clips[2]["error"]
    assert len(sent) == 2                      # no GPU time was spent on shot 3


def test_modal_chain_calls_one_clip_at_a_time_in_order(monkeypatch):
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"pic:" + uri.encode())
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    monkeypatch.setattr(C, "last_frame_png", lambda mp4: b"last:" + mp4)
    images = []

    class Fn:
        def remote(self, payload):
            images.append(payload["image"])
            return {"mp4": f"mp4-{payload['id']}".encode(), "seconds": 9.0, "frames": 33, "fps": 16, "mode": "fast", "coldStart": len(images) == 1}

    class Wan:
        generate_one = Fn()

    jobs = [(_job(n), f"k{n}", {"id": f"k{n}", "image": b"x", "prompt": f"move {n}"}) for n in (1, 2)]
    clips, stopped = C._chained(Wan(), {}, "http://x", "v", jobs)
    assert not stopped and [c["gpuSeconds"] for c in clips] == [9.0, 9.0]
    assert images == [b"pic:s3://b/pic1.png", b"last:mp4-k1"]
    assert C.cold_starts(clips) == 1
