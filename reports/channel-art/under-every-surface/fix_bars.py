import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont
H = Path(__file__).parent
BOLD, REG = r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf"
for n in "abc":
    im = Image.open(H / "ai" / f"sit-{n}.png").convert("RGB")
    a = np.asarray(im).astype(float).mean(axis=(1, 2))
    rows = np.where(a > 4)[0]
    top, bot = rows[0], rows[-1] + 1
    print(n, im.size, "content rows", top, bot)
    im = im.crop((0, top, im.width, bot))
    s = max(2560 / im.width, 1440 / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    l, t = (im.width - 2560) // 2, (im.height - 1440) // 2
    im = im.crop((l, t, l + 2560, t + 1440))
    shade = Image.new("L", im.size, 0)
    ImageDraw.Draw(shade).rectangle((380, 520, 2180, 920), fill=150)
    shade = shade.filter(ImageFilter.GaussianBlur(120))
    im = Image.composite(Image.new("RGB", im.size, (6, 9, 20)), im, shade)
    d = ImageDraw.Draw(im)
    f1, f2 = ImageFont.truetype(BOLD, 120), ImageFont.truetype(REG, 46)
    t1, t2 = "STRANGE IF TRUE", "Wild ideas. Honest physics."
    d.text(((2560 - d.textlength(t1, font=f1)) / 2, 640), t1, font=f1, fill=(255, 255, 255), stroke_width=6, stroke_fill=(6, 9, 20))
    d.text(((2560 - d.textlength(t2, font=f2)) / 2, 790), t2, font=f2, fill=(255, 209, 102), stroke_width=4, stroke_fill=(6, 9, 20))
    im.save(H / f"banner-sit-{n}-2560x1440.png")
ims = [Image.open(H / f"banner-sit-{k}-2560x1440.png").resize((1280, 720)) for k in "abc"]
o = Image.new("RGB", (1280, 2160))
[o.paste(im, (0, 720 * i)) for i, im in enumerate(ims)]
o.save(H / "banner-v2-overview.jpg", quality=88)
