"""Strange If True banners: 3 AI backgrounds (Gemini image model), title added in Pillow inside YouTube's safe area."""
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(r"C:\dev\project-v\.env"); sys.path.insert(0, r"C:\dev\project-v")
from google.genai import types
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from workers.common import gemini

HERE = Path(__file__).parent
OUT = HERE / "ai"
MODELS = ["gemini-3.1-flash-image", "gemini-nano-banana-2.1"]
BASE = ("Ultra-wide cinematic YouTube channel banner artwork, no text, no letters, no logos, no people. {scene} "
        "Premium science documentary look, deep navy and black with cyan and amber light, sharp, high contrast. "
        "Keep the centre of the frame dark and calm, the main subject placed far to the left or right, so a title can sit in the middle.")
SCENES = {
    "sit-a": "A tiny glowing black hole with a thin amber accretion ring floating in an empty dark lab, bending the light of a distant star field around it, small orbiting dust.",
    "sit-b": "A vast Dyson sphere of glowing amber panels half-enclosing a distant star, seen from deep space, with a faint cyan nebula; a tiny spacecraft for scale.",
    "sit-c": "A dark star field where faint luminous four-dimensional geometric shapes, like a glowing tesseract made of cyan lines, drift through a mist, with an amber galaxy at the edge.",
}
client = gemini()
for name, scene in SCENES.items():
    for m in MODELS:
        try:
            r = client.models.generate_content(model=m, contents=[BASE.format(scene=scene)], config=types.GenerateContentConfig(
                response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K")))
            part = next(p for p in r.candidates[0].content.parts if p.inline_data and (p.inline_data.mime_type or "").startswith("image"))
            (OUT / f"{name}.png").write_bytes(part.inline_data.data)
            print("ok", name, m); break
        except Exception as e:  # noqa: BLE001
            print("fail", name, m, str(e)[:140])

BOLD, REG = r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf"
for name in SCENES:
    p = OUT / f"{name}.png"
    if not p.exists():
        continue
    im = Image.open(p).convert("RGB").resize((2560, 1440), Image.LANCZOS)
    # darken the middle band so the title reads
    shade = Image.new("L", im.size, 0)
    ImageDraw.Draw(shade).rectangle((380, 520, 2180, 920), fill=150)
    shade = shade.filter(ImageFilter.GaussianBlur(120))
    im = Image.composite(Image.new("RGB", im.size, (6, 9, 20)), im, shade)
    d = ImageDraw.Draw(im)
    f1, f2 = ImageFont.truetype(BOLD, 120), ImageFont.truetype(REG, 46)
    t1, t2 = "STRANGE IF TRUE", "Wild ideas. Honest physics."
    w1, w2 = d.textlength(t1, font=f1), d.textlength(t2, font=f2)
    d.text(((2560 - w1) / 2, 640), t1, font=f1, fill=(255, 255, 255), stroke_width=6, stroke_fill=(6, 9, 20))
    d.text(((2560 - w2) / 2, 790), t2, font=f2, fill=(255, 209, 102), stroke_width=4, stroke_fill=(6, 9, 20))
    im.save(HERE / f"banner-{name}-2560x1440.png")
    print("banner", name)
