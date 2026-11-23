import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(r"C:\dev\project-v\.env"); sys.path.insert(0, r"C:\dev\project-v")
from google.genai import types
from workers.common import gemini
OUT = Path(__file__).parent / "ai"
P = ("A photorealistic cinematic YouTube channel profile picture, square, perfectly centred circular composition, no text, no letters. {s} "
     "Deep black background, dramatic lighting, razor sharp, premium documentary look, strong focal point readable at 98 pixels.")
S = {"icon-sit-d": "A realistic black hole seen close, a perfect black sphere with a blazing thin amber and white accretion ring and gravitationally lensed light, filling most of the frame.",
     "icon-sit-e": "A close-up human eye whose iris is a spiral galaxy with a tiny black hole as the pupil, glowing amber and cyan, filling the frame.",
     "icon-sit-f": "A glowing round window of light torn open in pure darkness, through it a small impossible alien sky with two moons and a ringed planet, cyan and amber glow."}
c = gemini()
for n, s in S.items():
    for m in ["gemini-3.1-flash-image", "gemini-nano-banana-2.1"]:
        try:
            r = c.models.generate_content(model=m, contents=[P.format(s=s)], config=types.GenerateContentConfig(
                response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="1:1", image_size="1K")))
            p = next(x for x in r.candidates[0].content.parts if x.inline_data and (x.inline_data.mime_type or "").startswith("image"))
            (OUT / f"{n}.png").write_bytes(p.inline_data.data); print("ok", n); break
        except Exception as e:
            print("fail", n, m, str(e)[:100])
from PIL import Image
ims = [Image.open(OUT / f"{n}.png").convert("RGB").resize((500, 500)) for n in S if (OUT / f"{n}.png").exists()]
o = Image.new("RGB", (1500, 500)); [o.paste(im, (500 * i, 0)) for i, im in enumerate(ims)]
o.save(Path(__file__).parent / "icon-v3-overview.jpg", quality=90)
