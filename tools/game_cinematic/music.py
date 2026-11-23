"""One Lyria call (about $0.08 list price) for the 20 s game-cinematic test, trimmed to the video and muxed in. Never prints the key."""
import re, subprocess, sys
from google import genai
from google.genai import types

OUT = r"E:\project-data\outputs\game-cinematic"
env = {}
for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
PROMPT = ("Instrumental cinematic game-trailer score, about 20 seconds, no vocals. Starts with a low ominous drone and distant wind over a ruined city, "
          "slow rising tension with deep taiko and cello ostinato, swells with massive brass and choir-like pads as a colossal winged creature rises, "
          "a huge impact hit near 14 seconds, then a thunderous sustained climax with sub bass as the creature passes overhead, ending on a long dark ring-out. "
          "Epic, dark, orchestral hybrid, Unreal Engine game trailer style.")
c = genai.Client(api_key=env["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta", timeout=300_000))
r = c.models.generate_content(model="lyria-3.5", contents=PROMPT)
audio = None
for p in r.candidates[0].content.parts:
    if p.inline_data and (p.inline_data.mime_type or "").startswith("audio"):
        audio = p.inline_data
if not audio:
    sys.exit("no audio returned")
raw = OUT + r"\work\lyria_raw.mp3"
open(raw, "wb").write(audio.data)
print("mime", audio.mime_type, "bytes", len(audio.data), flush=True)
dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", raw]).decode().strip())
print("lyria duration", dur, flush=True)
L = 20.25
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", OUT + r"\game_cinematic_20s_4k_silent.mp4", "-i", raw,
                "-filter_complex", f"[1:a]atrim=0:{L},afade=t=in:d=0.5,afade=t=out:st={L-3:.2f}:d=3,aresample=48000[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest",
                OUT + r"\game_cinematic_20s_4k_music.mp4"], check=True)
print("done")
