"""Two-scene test for the ant film (25 Nov 2026): visible sound effects (ground ripple ring, shake, dust) composited over the draft clips,
a synthesised sound bed (no samples, no licence), narration ducked under the bed, loudness to about -14 LUFS.
Run: uv run --group studio python -m tools.film4k.ant_fx <clips_dir> <fx_dir>   (clips clip00..17.mp4, line3.wav, line6.wav)"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import ndimage

W, H, FPS, SR = 720, 1280, 16, 48000

# One look for the whole film: cool blue moonlight, slightly lifted blue shadows (never crushed black), gentle contrast and a touch of
# colour, a soft vignette to pull the eye to the centre, and fine moving grain so the soft AI frames feel photographic.
GRADE = ("eq=contrast=1.08:saturation=1.12:gamma=0.97,"
         "curves=r='0/0 0.25/0.22 0.6/0.57 1/0.97':g='0/0 0.5/0.5 1/1':b='0/0.035 0.25/0.29 0.6/0.64 1/1',"
         "vignette=angle=PI/5.5,noise=alls=5:allf=t")


def read_frames(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def write_video(frames, path: Path):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()


def warp(img, dx, dy):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    out = np.empty_like(img)
    for c in range(3):
        out[..., c] = ndimage.map_coordinates(img[..., c], [yy + dy, xx + dx], order=1, mode="reflect")
    return out


def lift(img, gain=1.0, gamma=1.0):
    x = (img.astype(np.float32) / 255.0) ** gamma * gain
    return np.clip(x * 255, 0, 255).astype(np.uint8)


def sharpen(img, amount=0.6):
    f = img.astype(np.float32)
    blur = ndimage.gaussian_filter(f, sigma=(1.6, 1.6, 0))
    return np.clip(f + amount * (f - blur), 0, 255).astype(np.uint8)


def ripple_frames(frames, cx, cy, t0, speed=210.0, wavelength=55.0, amp=9.0, decay=1.6, squash=0.45, rings=2, gap=0.55):
    """Expanding ground ripple: rings travel outward from (cx, cy), squashed vertically as a floor seen from ground level."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + ((yy - cy) / squash) ** 2) + 1e-3
    ux, uy = (xx - cx) / dist, ((yy - cy) / squash) / dist * squash
    out = []
    for i, fr in enumerate(frames):
        t = i / FPS - t0
        if t <= 0:
            out.append(fr)
            continue
        disp = np.zeros_like(dist)
        for k in range(rings):
            tt = t - k * gap
            if tt <= 0:
                continue
            r = speed * tt
            env = np.exp(-((dist - r) / (wavelength * 1.3)) ** 2) * np.exp(-tt / decay)
            disp += amp * env * np.sin(2 * np.pi * (dist - r) / wavelength)
        # a faint bright edge on the ring so it reads as water or pressure, not just a wobble
        out.append(warp(fr, ux * disp, uy * disp))
    return out


def shake_dust(frames, t0, amp=26.0, decay=0.9, seed=3):
    rng = np.random.default_rng(seed)
    out, specks = [], rng.random((140, 4))
    for i, fr in enumerate(frames):
        t = i / FPS - t0
        if t > 0:
            e = np.exp(-t / decay)
            dx, dy = rng.normal(0, amp * e), rng.normal(0, amp * e * 1.3)
            fr = ndimage.shift(fr, (dy, dx, 0), order=1, mode="reflect")
            fr = lift(fr, gain=1.0 - 0.25 * e)  # a brief dip in exposure as the ground lurches
        # dust motes drifting up and back in the moonlight, strongest right after the step
        glow = np.zeros((H, W), np.float32)
        for sx, sy, ss, sp in specks:
            if t < 0:
                continue
            x = (sx * W + 40 * np.sin(sp * 9 + i * 0.05)) % W
            y = (sy * H * 0.7 + 360 - t * 40 * (0.3 + ss) + H * 0.2) % H
            glow[int(y) % H, int(x) % W] = 1.0
        glow = ndimage.gaussian_filter(glow, 1.4) * 12.0 * min(1.0, np.exp(-max(t, 0) / 2.5) + 0.1) if t >= 0 else glow
        fr = np.clip(fr.astype(np.float32) + glow[..., None] * np.array([120, 150, 190], np.float32), 0, 255).astype(np.uint8)
        out.append(fr)
    return out


# ---------------------------------------------------------------- sound
def noise(n, rng):
    return rng.normal(0, 1, n).astype(np.float32)


def lowpass(x, hz):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, hz, "low", fs=SR, output="sos"), x).astype(np.float32)


def bandpass(x, lo, hi):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, [lo, hi], "band", fs=SR, output="sos"), x).astype(np.float32)


def env_exp(n, tau):
    return np.exp(-np.arange(n) / (tau * SR)).astype(np.float32)


def place(track, snd, t):
    i = int(t * SR)
    track[i:i + len(snd)] += snd[: len(track) - i]


def boom(dur, f0, f1, tau, rng, rumble=0.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / (dur * 0.18))
    ph = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(ph) * env_exp(n, tau)
    rb = lowpass(noise(n, rng), 140) * env_exp(n, tau * 1.4) * 6 * rumble
    click = lowpass(noise(n, rng), 600) * env_exp(n, 0.02) * 1.5
    return (tone + rb + click).astype(np.float32)


def plink(rng, f=1900):
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * (f + 600 * np.exp(-t / 0.03)) * t) * env_exp(n, 0.07)
    tick = bandpass(noise(n, rng), 2500, 7000) * env_exp(n, 0.012)
    return (0.5 * tone + 0.5 * tick).astype(np.float32)


def bed(dur, rng, level=0.035, swell=0.35):
    n = int(dur * SR)
    x = lowpass(noise(n, rng), 900) * 0.6 + bandpass(noise(n, rng), 1800, 5200) * 0.12
    t = np.arange(n) / SR
    x *= level * (1 + swell * np.sin(2 * np.pi * t / 7.0) + 0.5 * swell * np.sin(2 * np.pi * t / 3.1))
    return x.astype(np.float32)


def rain_scene(rng, dur):
    tr = bed(dur, rng)
    place(tr, plink(rng), 1.35)                       # the drop lands (beat 1, about 1.3 s)
    for j, tt in enumerate([0.7, 2.6, 3.3, 4.1]):    # falling drops in the air
        place(tr, plink(rng, 2600 + 300 * j) * 0.18, tt)
    place(tr, boom(3.2, 95, 34, 0.9, rng, 0.35) * 0.9, 1.4)   # soft ground boom under the splash
    place(tr, boom(3.5, 70, 30, 1.1, rng, 0.5) * 0.7, 5.05)   # shockwave (beat 2, start)
    for tt in np.arange(6.0, 9.6, 0.22):              # dew beads jumping and settling
        place(tr, plink(rng, 3200 + 600 * rng.random()) * 0.08 * np.exp(-(tt - 6.0) / 1.6), float(tt))
    return tr


def giant_scene(rng, dur):
    tr = bed(dur, rng, level=0.03)
    place(tr, boom(5.0, 60, 24, 1.6, rng, 1.0) * 1.1, 0.5)     # the giant step
    place(tr, boom(4.0, 48, 22, 1.4, rng, 0.8) * 0.6, 1.4)     # a second, smaller shudder
    for tt in np.arange(1.0, 6.5, 0.16):                         # debris and dew falling back
        place(tr, bandpass(noise(int(0.04 * SR), rng), 1200, 6500) * 0.1 * np.exp(-(tt - 1.0) / 2.2), float(tt))
    tr = tr * np.linspace(1.0, 0.8, len(tr))                     # settles into quiet by the end of the beat
    return tr


def mix(scene_wav: np.ndarray, voice: np.ndarray, path: Path):
    """Duck the bed under the voice (simple envelope follower), add the voice at 0.4 s, write stereo-less mono 48 kHz."""
    n = len(scene_wav)
    v = np.zeros(n, np.float32)
    off = int(0.4 * SR)
    v[off:off + len(voice)] = voice[: n - off]
    env = ndimage.uniform_filter1d(np.abs(v), int(0.25 * SR))
    duck = 1.0 - 0.55 * np.clip(env / (env.max() * 0.35 + 1e-9), 0, 1)
    out = scene_wav * duck + v * 0.9
    sf.write(str(path), out, SR, subtype="PCM_16")


def build(clips: Path, fx: Path):
    fx.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    jobs = [
        ("rain", (6, 7), "line3.wav", rain_scene),
        ("giant", (12, 13), "line6.wav", giant_scene),
    ]
    for name, (a, b), voice_file, scene_fn in jobs:
        frames = list(read_frames(clips / f"clip{a:02d}.mp4")) + list(read_frames(clips / f"clip{b:02d}.mp4"))
        nbeat = len(frames) // 2
        if name == "rain":
            # beat 1 lifted a little; beat 2 is dark and soft, so lift and sharpen it, then send a ripple ring across the soil from the landing spot
            frames = [sharpen(lift(f, 1.5 if i >= nbeat else 1.15, 0.9), 0.5) for i, f in enumerate(frames)]
            frames = ripple_frames(frames, cx=140, cy=980, t0=nbeat / FPS * 0.9 + 0.0, speed=230, amp=10, rings=3)
        else:
            frames = [sharpen(lift(f, 1.25, 0.9), 0.4) for f in frames]
            frames = shake_dust(frames, t0=0.5)
        vid = fx / f"{name}_silent.mp4"
        write_video(frames, vid)
        dur = len(frames) / FPS
        voice, sr = sf.read(str(clips.parent / "fx" / voice_file) if (clips.parent / "fx" / voice_file).exists() else str(clips / voice_file))
        if voice.ndim > 1:
            voice = voice.mean(1)
        voice = ndimage.zoom(voice, SR / sr, order=1).astype(np.float32)
        wav = fx / f"{name}.wav"
        mix(scene_fn(rng, dur), voice, wav)
        out = fx / f"{name}_scene.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vid), "-i", str(wav), "-af", "loudnorm=I=-14:TP=-1.5:LRA=7", "-vf", GRADE,
                        "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-b:a", "192k", "-ar", "48000", "-shortest", str(out)], check=True)
        print("wrote", out)


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
