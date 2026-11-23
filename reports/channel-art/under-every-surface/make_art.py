"""Under Every Surface: profile picture, banner and three thumbnails for the television video.
Run: uv run python reports/channel-art/under-every-surface/make_art.py
Palette matches the diagram lane: navy #0B1020, cyan #4CC9F0, orange #F9A03F, yellow #FFD166."""
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
SRC = HERE / "src"
NAVY, CYAN, ORANGE, YELLOW, WHITE = (11, 16, 32), (76, 201, 240), (249, 160, 63), (255, 209, 102), (255, 255, 255)
BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
REG = r"C:\Windows\Fonts\segoeui.ttf"


def font(size, bold=True):
    return ImageFont.truetype(BOLD if bold else REG, size)


def glow(img, draw_fn, color, blur=18, strength=1.6):
    """Draws on a layer, blurs it and adds it back: a neon glow."""
    layer = Image.new("RGB", img.size, (0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), color)
    halo = layer.filter(ImageFilter.GaussianBlur(blur))
    out = np.asarray(img).astype(np.float32) + np.asarray(halo).astype(np.float32) * strength + np.asarray(layer).astype(np.float32)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def crack_points(w, h, seed, x0, y0, x1, y1, steps=14, jitter=0.07):
    rnd = random.Random(seed)
    pts = []
    for i in range(steps + 1):
        t = i / steps
        pts.append((x0 + (x1 - x0) * t + rnd.uniform(-jitter, jitter) * w * (0 if i in (0, steps) else 1),
                    y0 + (y1 - y0) * t + rnd.uniform(-jitter, jitter) * h * (0 if i in (0, steps) else 1)))
    return pts


def background(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((x - w * 0.5) / w) ** 2 + ((y - h * 0.5) / h) ** 2)
    base = np.array(NAVY, np.float32)[None, None, :] * (1.25 - d[..., None] * 0.9)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def emblem(size, text=False):
    """A dark glass disc, a glowing crack, a lit layer beneath."""
    img = background(size, size)
    cx = cy = size // 2
    r = int(size * 0.42)
    dr = ImageDraw.Draw(img)
    dr.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(40, 60, 90), width=max(2, size // 90))
    under = Image.new("RGB", (size, size), (0, 0, 0))
    ud = ImageDraw.Draw(under)
    # the layer beneath: warm light seen through the crack, clipped to the disc
    for k in range(7):
        ud.line([(cx - r, cy - r * 0.55 + k * r * 0.18), (cx + r, cy - r * 0.9 + k * r * 0.26)], fill=(180 - k * 15, 100, 40 + k * 8), width=size // 40)
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    pts = crack_points(size, size, 7, cx - r * 0.95, cy - r * 0.85, cx + r * 0.95, cy + r * 0.85, steps=10, jitter=0.045)
    poly = pts + [(cx + r * 1.2, cy + r * 1.2), (cx + r * 1.2, cy - r * 1.2)]
    md.polygon(poly, fill=255)
    disc = Image.new("L", (size, size), 0)
    ImageDraw.Draw(disc).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    mask = Image.fromarray(np.minimum(np.asarray(mask), np.asarray(disc)))
    img = Image.composite(under.filter(ImageFilter.GaussianBlur(size // 120)), img, mask)
    img = glow(img, lambda d, c: d.line(pts, fill=c, width=max(3, size // 70), joint="curve"), CYAN, blur=size // 45, strength=1.4)
    if text:
        t = "UES"
    return img


def profile():
    img = emblem(800)
    # the wide ring keeps it readable at 98 px
    img.save(HERE / "profile-picture.png")


def banner():
    w, h = 2560, 1440
    img = background(w, h)
    # faint layer lines flowing right, the "signal" from the diagrams
    for k in range(9):
        y = 330 + k * 85
        img = glow(img, lambda d, c, y=y, k=k: d.line([(0, y + math.sin(k) * 40), (w * 0.38, y - 30), (w * 0.62, y + 20), (w, y - 40)], fill=c, width=3), (50 + k * 8, 70 + k * 10, 110 + k * 12), blur=10, strength=1.0)
    # a crack of light across the middle
    pts = crack_points(w, h, 3, 120, h * 0.58, w - 120, h * 0.42, steps=22, jitter=0.012)
    img = glow(img, lambda d, c: d.line(pts, fill=c, width=6, joint="curve"), CYAN, blur=22, strength=1.7)
    img = glow(img, lambda d, c: d.line(pts, fill=c, width=2, joint="curve"), WHITE, blur=6, strength=0.8)
    dr = ImageDraw.Draw(img)
    # the title sits in YouTube's safe area (1546 x 423, centred)
    title, tag = "STRANGE IF TRUE", "Wild ideas. Honest physics."
    f1, f2 = font(100), font(40, bold=False)
    tw = dr.textlength(title, font=f1)
    dr.text(((w - tw) / 2, 600), title, font=f1, fill=WHITE, stroke_width=8, stroke_fill=NAVY)
    tw2 = dr.textlength(tag, font=f2)
    dr.text(((w - tw2) / 2, 735), tag, font=f2, fill=YELLOW, stroke_width=5, stroke_fill=NAVY)
    img.save(HERE / "banner-2560x1440.png")
    # a preview showing only what a TV / desktop shows (the safe area), for checking
    crop = img.crop((w // 2 - 773, h // 2 - 212, w // 2 + 773, h // 2 + 212))
    crop.save(HERE / "banner-safe-area-preview.png")


def cover(path, size):
    im = Image.open(path).convert("RGB")
    w, h = size
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    return im.crop(((im.width - w) // 2, (im.height - h) // 2, (im.width - w) // 2 + w, (im.height - h) // 2 + h))


def thumb(name, src, headline, sub, focus=(0.5, 0.5), accent=CYAN):
    w, h = 1280, 720
    img = cover(SRC / src, (w, h))
    a = np.asarray(img).astype(np.float32)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    shade = 0.45 + 0.55 * np.clip((x / w) * 1.6 - 0.1, 0, 1)  # darker on the left, where the words go
    a *= shade[..., None]
    img = Image.fromarray(np.clip(a * 1.1, 0, 255).astype(np.uint8))
    img = glow(img, lambda d, c: d.line([(0, h - 70), (w * 0.4, h - 95), (w * 0.7, h - 60), (w, h - 85)], fill=c, width=7), accent, blur=14, strength=1.5)
    dr = ImageDraw.Draw(img)
    f, f2 = font(118), font(46, bold=False)
    lines = headline.split("\n")
    ty = 60
    for ln in lines:
        for ox, oy in ((-4, -4), (4, 4), (-4, 4), (4, -4)):
            dr.text((54 + ox, ty + oy), ln, font=f, fill=(0, 0, 0))
        dr.text((54, ty), ln, font=f, fill=WHITE if ln != lines[-1] else YELLOW)
        ty += 125
    if sub:
        dr.rounded_rectangle((54, h - 150, 54 + dr.textlength(sub, font=f2) + 44, h - 84), radius=14, fill=(0, 0, 0))
        dr.text((76, h - 142), sub, font=f2, fill=accent)
    img.save(HERE / f"thumbnail-{name}.png")


if __name__ == "__main__":
    profile()
    banner()
    thumb("1-into-the-wall", "kf4.png", "IT GOES\nINTO THE WALL", "", accent=ORANGE)
    thumb("2-one-dot", "kf9.png", "ONE DOT.\nTHAT'S ALL.", "", accent=(80, 255, 140))
    thumb("3-your-brain", "kf31.png", "YOUR BRAIN\nIS LYING", "", accent=CYAN)
    print("done")
