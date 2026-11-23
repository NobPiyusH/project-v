import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(r"C:\dev\project-v\.env"); sys.path.insert(0, r"C:\dev\project-v")
from google.genai import types
from workers.common import gemini
OUT = Path(__file__).parent / "ai"
P = ("A bold minimal YouTube channel profile icon, perfectly centred, square, no other text. {s} Simple strong shapes that stay readable at 98 pixels, "
     "plain very dark navy background, cyan and amber glow, premium science look.")
S = {"icon-sit-a": "A single large glowing amber question mark whose dot is a tiny black hole with a thin accretion ring, thick clean shape.",
     "icon-sit-b": "The capital letter S drawn as one thick glowing cyan line that curves like an orbit around a small amber glowing star.",
     "icon-sit-c": "A glowing cyan tesseract (cube inside a cube, thick lines) with a small amber light at its centre."}
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
o.save(Path(__file__).parent / "icon-v2-overview.jpg", quality=90)
