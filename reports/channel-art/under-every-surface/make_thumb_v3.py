import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(r"C:\dev\project-v\.env"); sys.path.insert(0, r"C:\dev\project-v")
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from workers.common import gemini
H = Path(__file__).parent; OUT = H / "ai"
P = ("A photorealistic YouTube thumbnail background, 16:9, no text, no letters, no logos. {s} Dramatic cinematic lighting, high contrast, vivid saturated colour, "
     "sharp focus, the main subject on the RIGHT two-thirds of the frame and the LEFT third dark and simple so a headline can sit there.")
S = {"tv-a": ("A vintage CRT television in a dark room, extreme close-up of its screen where a smiling human face is made of huge visible glowing red, green and blue dots, the dots clearly separate.", "IT'S JUST\nTHREE COLOURS\nOF DOTS", (255, 90, 60)),
     "tv-b": ("A vintage television on a wooden table in a dark room; a thick glowing cyan cable leaves its back and runs into a wall that is cracked open, bright light and a distant tall radio tower visible inside the wall.", "THE PICTURE ISN'T\nIN THE TV", (76, 201, 240)),
     "tv-c": ("A glass cutaway of a CRT television tube in darkness, a thin glowing electron beam shooting from the back of the tube and striking the phosphor screen, leaving a bright glowing line of colour, sparks.", "A BEAM\nPAINTS EVERY\nFRAME", (80, 255, 140))}
c = gemini()
BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
for n, (s, head, acc) in S.items():
    p = OUT / f"{n}.png"
    for m in ["gemini-3.1-flash-image", "gemini-nano-banana-2.1"]:
        try:
            r = c.models.generate_content(model=m, contents=[P.format(s=s)], config=types.GenerateContentConfig(
                response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K")))
            part = next(x for x in r.candidates[0].content.parts if x.inline_data and (x.inline_data.mime_type or "").startswith("image"))
            p.write_bytes(part.inline_data.data); print("ok", n); break
        except Exception as e:
            print("fail", n, m, str(e)[:100])
    if not p.exists(): continue
    im = Image.open(p).convert("RGB")
    a = np.asarray(im).astype(float).mean(axis=(1, 2)); rows = np.where(a > 4)[0]
    im = im.crop((0, rows[0], im.width, rows[-1] + 1))
    sc = max(1280 / im.width, 720 / im.height); im = im.resize((int(im.width * sc) + 1, int(im.height * sc) + 1), Image.LANCZOS)
    l, t = (im.width - 1280) // 2, (im.height - 720) // 2; im = im.crop((l, t, l + 1280, t + 720))
    arr = np.asarray(im).astype(float); x = np.linspace(0, 1, 1280)[None, :, None]
    arr *= (0.35 + 0.65 * np.clip(x * 2.2 - 0.3, 0, 1)); im = Image.fromarray(np.clip(arr * 1.08, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im); f = ImageFont.truetype(BOLD, 104); y = 70
    lines = head.split("\n")
    for i, ln in enumerate(lines):
        for ox, oy in ((-5, -5), (5, 5), (-5, 5), (5, -5), (0, 6), (0, -6)):
            d.text((50 + ox, y + oy), ln, font=f, fill=(0, 0, 0))
        d.text((50, y), ln, font=f, fill=(255, 255, 255) if i < len(lines) - 1 else acc); y += 118
    im.save(H / f"thumbnail-{n}.png"); print("thumb", n)
ims = [Image.open(H / f"thumbnail-{n}.png").resize((640, 360)) for n in S if (H / f"thumbnail-{n}.png").exists()]
o = Image.new("RGB", (1920, 360)); [o.paste(im, (640 * i, 0)) for i, im in enumerate(ims)]; o.save(H / "thumb-v3-overview.jpg", quality=90)
