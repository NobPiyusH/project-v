"""AI backgrounds for the channel banner and profile picture (Gemini image model, ~6 images). Text is added afterwards."""
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(r"C:\dev\project-v\.env"); sys.path.insert(0, r"C:\dev\project-v")
from google.genai import types
from workers.common import gemini

OUT = Path(__file__).parent / "ai"
MODELS = ["gemini-3.1-flash-image", "gemini-nano-banana-2.1"]
BANNER = ("Ultra-wide cinematic channel banner artwork, no text, no letters, no logos. A dark polished black glass surface "
          "is cracked open like a fracture across the frame, revealing layered depths beneath it: first glowing circuit-board "
          "traces, then luminous neural networks, then a spiral galaxy, and far in the centre a tiny glowing black hole with a thin "
          "amber accretion ring. Deep navy and black, cyan and amber light, premium documentary look, sharp, high contrast. "
          "{extra} Keep the middle third of the frame dark and calm so a title can sit there.")
ICON = ("A bold minimal channel icon, centred, no text, no letters. A dark glass disc cracking open to reveal a glowing spiral "
        "galaxy with a tiny black hole inside. {extra} Strong contrast, simple shapes that stay readable at 98 pixels, "
        "plain very dark navy background, cyan and amber glow.")
JOBS = [("banner-a", BANNER.format(extra="The crack runs diagonally from the lower left to the upper right."), "16:9", "2K"),
        ("banner-b", BANNER.format(extra="The crack is a horizontal horizon line, the galaxy glows below it like a sunrise."), "16:9", "2K"),
        ("banner-c", BANNER.format(extra="Cool cyan dominates, with faint orbit lines and distant stars; minimal and elegant."), "16:9", "2K"),
        ("icon-a", ICON.format(extra="Flat vector style."), "1:1", "1K"),
        ("icon-b", ICON.format(extra="Glossy 3D glass look."), "1:1", "1K"),
        ("icon-c", ICON.format(extra="A thin glowing ring around the disc."), "1:1", "1K")]
client = gemini()
for name, prompt, ar, size in JOBS:
    done = False
    for m in MODELS:
        try:
            r = client.models.generate_content(model=m, contents=[prompt], config=types.GenerateContentConfig(
                response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=ar, image_size=size)))
            for part in r.candidates[0].content.parts:
                if part.inline_data and (part.inline_data.mime_type or "").startswith("image"):
                    (OUT / f"{name}.png").write_bytes(part.inline_data.data)
                    print("ok", name, m, len(part.inline_data.data) // 1000, "KB"); done = True; break
            if done: break
        except Exception as e:  # noqa: BLE001
            print("fail", name, m, str(e)[:140])
    if not done: print("NO IMAGE", name)
