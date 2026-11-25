"""Sound for the ant film (25 Nov 2026): a continuous night bed from CC0 recordings (no silent gaps), cue sounds placed on each scene's event,
the narration on top with the bed ducked under the voice, loudness to about -14 LUFS. Every sound file is CC0 (snd/SOURCES.md).
Run: uv run python -m tools.film4k.ant_sound <outputs_dir> [video]   (needs outputs/snd/*.mp3, outputs/fx/line0..8.wav, outputs/film_v2_silent.mp4)"""

import subprocess
import sys
from pathlib import Path

SCENE = 9.625  # scene start spacing in film_v2 (two 5.0625 s clips with a 0.3 s blend each)
TOTAL = 86.06


def cues():
    """(filter chain pieces) one per cue; each ends in a labelled output [cN]. Inputs: 0 night, 1 crickets, 2 rain, 3 wind, 4 thump."""
    f, n, tc = [], 0, 0

    def add(chain: str):
        nonlocal n
        f.append(f"{chain}[c{n}]")
        n += 1

    def thump() -> str:
        nonlocal tc
        tc += 1
        return f"[t{tc - 1}]"

    def at(scene: int, sec: float) -> int:
        return int((scene * SCENE + sec) * 1000)

    # soft, far footsteps through the ground (scene 3): low, muffled thumps
    for sec, vol in ((1.3, 0.55), (3.0, 0.45), (4.9, 0.5)):
        add(f"{thump()}asetrate=48000*0.62,aresample=48000,lowpass=f=220,volume={vol},adelay={at(2, sec)}|{at(2, sec)}")
    # raindrop (scene 4): the recording's first seconds, then a deep shockwave thump as the second beat begins
    add(f"[2:a]atrim=0:4.2,asetrate=48000*1.0,volume=0.45,afade=t=in:d=0.15,afade=t=out:st=3.4:d=0.8,adelay={at(3, 0.5)}|{at(3, 0.5)}")
    add(f"{thump()}asetrate=48000*0.42,aresample=48000,lowpass=f=180,aecho=0.8:0.6:140|290:0.45|0.3,volume=1.1,adelay={at(3, 5.0)}|{at(3, 5.0)}")
    # the colony (scene 5): quick dry taps, like ants drumming
    for k in range(7):
        sec = 1.4 + k * 0.42 + (0.06 if k % 3 == 2 else 0.0)
        add(f"{thump()}asetrate=48000*2.1,aresample=48000,highpass=f=300,volume=0.22,adelay={at(4, sec)}|{at(4, sec)}")
    # the road (scene 6): a slow, deep hum that swells and fades
    f.append("sine=f=48:d=9.6,volume=0.10,afade=t=in:d=3,afade=t=out:st=6:d=3.5[hum1]")
    f.append("sine=f=96:d=9.6,volume=0.03,afade=t=in:d=3,afade=t=out:st=6:d=3.5[hum2]")
    f.append(f"[hum1][hum2]amix=inputs=2:normalize=0,adelay={at(5, 0.0)}|{at(5, 0.0)}[c{n}]")
    n += 1
    # the giant step (scene 7): a very low heavy thump, a smaller one behind it, rumble underneath
    add(f"{thump()}asetrate=48000*0.38,aresample=48000,lowpass=f=140,aecho=0.8:0.7:160|340|600:0.5|0.35|0.2,volume=1.6,adelay={at(6, 0.55)}|{at(6, 0.55)}")
    add(f"{thump()}asetrate=48000*0.5,aresample=48000,lowpass=f=160,aecho=0.8:0.6:200|420:0.4|0.25,volume=0.9,adelay={at(6, 1.7)}|{at(6, 1.7)}")
    # quiet (scene 8): wind in the grass, easing away
    add(f"[3:a]atrim=2:11.2,asetpts=PTS-STARTPTS,volume=0.7,afade=t=in:d=1.5,afade=t=out:st=6.8:d=2.4,adelay={at(7, 0.2)}|{at(7, 0.2)}")
    f.insert(0, "[4:a]asplit=%d%s" % (tc, "".join(f"[t{i}]" for i in range(tc))))
    return f, n


def build(out: Path, video: Path):
    snd, fx = out / "snd", out / "fx"
    pieces, n = cues()
    # bed: two long night recordings, one at moderate level and one far lower, faded in and out
    bed = ("[0:a]atrim=40:%.1f,asetpts=PTS-STARTPTS,volume=0.55,afade=t=in:d=2,afade=t=out:st=%.1f:d=3.5[bedA];"
           "[1:a]atrim=20:%.1f,asetpts=PTS-STARTPTS,volume=0.22,afade=t=in:d=2,afade=t=out:st=%.1f:d=3.5[bedB];"
           "[bedA][bedB]amix=inputs=2:normalize=0[bed]") % (40 + TOTAL, TOTAL - 3.5, 20 + TOTAL, TOTAL - 3.5)
    inputs = ["-i", str(snd / "night.mp3"), "-i", str(snd / "crickets.mp3"), "-i", str(snd / "raindrops.mp3"),
              "-i", str(snd / "grasswind.mp3"), "-i", str(snd / "thump.mp3")]
    for i in range(9):
        inputs += ["-i", str(fx / f"line{i}.wav")]
    voice = []
    for i in range(9):
        ms = int((i * SCENE + 0.4) * 1000)
        voice.append(f"[{5 + i}:a]adelay={ms}|{ms}[v{i}]")
    cue_labels = "".join(f"[c{i}]" for i in range(n))
    # the voice also drives the ducking of the bed and the cues: while someone speaks they drop by about half
    fc = ";".join(pieces) + ";" + bed + ";" + ";".join(voice) + ";" + \
        "".join(f"[v{i}]" for i in range(9)) + "amix=inputs=9:normalize=0,asplit=2[voice][vsc];" + \
        f"[bed]{cue_labels}amix=inputs={n + 1}:normalize=0:duration=longest[sfx];" + \
        "[sfx][vsc]sidechaincompress=threshold=0.015:ratio=6:attack=30:release=500:makeup=1[ducked];" + \
        "[ducked][voice]amix=inputs=2:normalize=0:duration=longest,apad=pad_dur=5,atrim=0:%.2f,loudnorm=I=-14:TP=-1.5:LRA=9[out]" % TOTAL
    cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-i", str(video), "-filter_complex", fc, "-map", "14:v", "-map", "[out]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", str(out / "film_v3.mp4")]
    subprocess.run(cmd, check=True)
    print("wrote", out / "film_v3.mp4")


if __name__ == "__main__":
    base = Path(sys.argv[1])
    build(base, Path(sys.argv[2]) if len(sys.argv) > 2 else base / "film_v2_silent.mp4")
