"""Procedural assets for the science zoom (run inside Blender: needs bpy + numpy).

  blender -b -P tools/blender/science_zoom_assets.py -- --out E:/project-data/blender/library/science-zoom/textures

Writes galaxy_disc_4096.exr (RGB = emission colour, A = coverage): a face-on spiral galaxy with a bar and bulge,
two main arms plus feathers, dust lanes, HII knots and a few hundred thousand individual stars.
Deterministic (fixed seed), no downloads. The Earth/NASA textures are copied separately by science_zoom.py.
"""
import sys, os
import numpy as np


def _noise(n, cells, rng):
    """Smooth value noise on an n x n grid with `cells` lattice cells (tileable not needed)."""
    g = rng.random((cells + 2, cells + 2)).astype(np.float32)
    xs = np.linspace(0, cells, n, endpoint=False, dtype=np.float32)
    i = xs.astype(np.int32)
    f = xs - i
    f = f * f * (3 - 2 * f)
    a = g[i][:, i]
    b = g[i][:, i + 1]
    c = g[i + 1][:, i]
    d = g[i + 1][:, i + 1]
    fx = f[None, :]
    fy = f[:, None]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def fbm(n, base, octaves, rng, gain=0.55):
    out = np.zeros((n, n), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        out += amp * _noise(n, base * (2 ** o), rng)
        tot += amp
        amp *= gain
    return out / tot


def make_galaxy(n=4096, seed=7):
    rng = np.random.default_rng(seed)
    ax = np.linspace(-1, 1, n, dtype=np.float32)
    x, y = np.meshgrid(ax, ax)
    r = np.sqrt(x * x + y * y) + 1e-5
    th = np.arctan2(y, x)

    warp = fbm(n, 6, 5, rng) - 0.5
    pitch = np.deg2rad(22.0)
    a = np.log(r / 0.07 + 1e-3) / np.tan(pitch)
    s = 2.0 * (th - a) + warp * 3.2
    arm = np.clip(0.5 + 0.5 * np.cos(s), 0, 1) ** 2.4
    feather = np.clip(0.5 + 0.5 * np.cos(4.0 * (th - a * 1.07) + warp * 6.0 + 1.3), 0, 1) ** 3.0
    arms = np.clip(arm + 0.2 * feather, 0, 1)
    radial_arm = np.clip((r - 0.10) / 0.18, 0, 1) * np.clip((0.96 - r) / 0.25, 0, 1)

    disk = np.exp(-r / 0.34)
    bulge = np.exp(-(r / 0.085) ** 1.0)
    bx = x * np.cos(0.55) + y * np.sin(0.55)
    by = -x * np.sin(0.55) + y * np.cos(0.55)
    bar = np.exp(-((bx / 0.20) ** 2 + (by / 0.055) ** 2)) * 0.9
    edge = np.clip((1.0 - r) / 0.12, 0, 1)
    edge = edge * edge * (3 - 2 * edge)

    clump = fbm(n, 40, 5, rng)
    clump = np.clip((clump - 0.35) * 2.6, 0, 1)

    # dust: dark lanes on the inner (leading) edge of each arm
    dust_phase = np.clip(0.5 + 0.5 * np.cos(s - 0.9), 0, 1) ** 4.0
    dust = np.clip(dust_phase * (0.5 + clump) * np.clip((r - 0.06) / 0.2, 0, 1), 0, 1) * 0.85

    lum_arm = arms * radial_arm * disk * (0.35 + 1.3 * clump) * 2.0
    lum_disk = disk * 0.30
    lum_core = bulge * 2.6 + bar * 1.2 * np.exp(-r / 0.30)
    old = (lum_disk + lum_core) * (1 - 0.55 * dust)
    young = lum_arm * (1 - 0.8 * dust)

    hii_n = fbm(n, 180, 3, rng)
    hii = np.clip((hii_n - 0.70) * 5.0, 0, 1) ** 1.5 * arms * radial_arm * 0.7

    warm = np.array([1.00, 0.80, 0.52], np.float32)
    blue = np.array([0.58, 0.74, 1.00], np.float32)
    pink = np.array([1.00, 0.42, 0.55], np.float32)
    img = np.zeros((n, n, 3), np.float32)
    for c in range(3):
        img[..., c] = old * warm[c] + young * blue[c] + hii * 1.4 * pink[c]

    # individual stars, sampled from the luminosity
    dens = (old + young)
    dens = dens / dens.max()
    stars = np.zeros((n, n), np.float32)
    count = 520000
    cand = rng.random((count * 3, 2))
    px = (cand[:, 0] * (n - 1)).astype(np.int32)
    py = (cand[:, 1] * (n - 1)).astype(np.int32)
    keep = rng.random(count * 3) < dens[py, px] ** 0.8
    px, py = px[keep][:count], py[keep][:count]
    mag = rng.random(len(px)) ** 6.0
    np.add.at(stars, (py, px), 0.15 + 2.6 * mag)
    # tiny blur so stars are ~2px, the brightest get a cross
    blur = stars.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        blur += 0.35 * np.roll(np.roll(stars, dy, 0), dx, 1)
    starcol = np.array([0.95, 0.93, 1.0], np.float32)
    for c in range(3):
        img[..., c] += blur * starcol[c] * 0.9 * edge

    img *= edge[..., None]
    lum = img.max(axis=2)
    alpha = np.clip(lum * 1.6, 0, 1).astype(np.float32)
    out = np.dstack([img, alpha]).astype(np.float32)
    # Blender wants bottom-to-top rows
    return out[::-1].copy()


def save_exr(arr, path, name):
    import bpy
    h, w, _ = arr.shape
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
    img.colorspace_settings.name = 'Linear Rec.709'
    img.alpha_mode = 'CHANNEL_PACKED'
    img.pixels.foreach_set(arr.ravel())
    img.filepath_raw = path
    img.file_format = 'OPEN_EXR'
    scn = bpy.context.scene
    scn.render.image_settings.file_format = 'OPEN_EXR'
    scn.render.image_settings.color_depth = '16'
    scn.render.image_settings.color_mode = 'RGBA'
    img.save_render(path, scene=scn)


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    out = argv[argv.index('--out') + 1] if '--out' in argv else '.'
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, 'galaxy_disc_4096.exr')
    if os.path.exists(path) and os.path.exists(os.path.join(out, 'galaxy_blur_1024.exr')) and '--force' not in argv:
        print('galaxy exists:', path)
        return
    print('generating galaxy disc ...')
    arr = make_galaxy()
    save_exr(arr, path, 'galaxy_disc')
    # soft 1024px version: blends in while the disc is hugely magnified (no pixel-sized star specks)
    a = arr[::-1]
    k = 4
    small = a.reshape(1024, k, 1024, k, 4).mean(axis=(1, 3))
    for _ in range(3):
        small = (small + np.roll(small, 1, 0) + np.roll(small, -1, 0)) / 3
        small = (small + np.roll(small, 1, 1) + np.roll(small, -1, 1)) / 3
    small[..., 3] = np.clip(small[..., 3] * 1.1, 0, 1)
    save_exr(small[::-1].copy(), os.path.join(out, 'galaxy_blur_1024.exr'), 'galaxy_blur')
    print('wrote', path)


if __name__ == '__main__':
    main()
