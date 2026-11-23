"""Procedural face-on spiral galaxy texture (RGBA uint8). No downloaded images."""
import numpy as np
from scipy.ndimage import gaussian_filter


def _noise(rng, n, sigmas, weights):
    out = np.zeros((n, n))
    for s, w in zip(sigmas, weights):
        f = gaussian_filter(rng.standard_normal((n, n)), s, mode="wrap")
        out += w * f / (f.std() + 1e-9)
    return out


def make_galaxy(n=2048, seed=11):
    """Disc radius = 1.0 maps to n/2 * 0.96 pixels. Returns an (n, n, 4) uint8 array."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    x = (x - n / 2) / (n / 2 * 0.96)
    y = (y - n / 2) / (n / 2 * 0.96)
    r = np.hypot(x, y) + 1e-6
    th = np.arctan2(y, x)

    # two main arms plus two weak ones, logarithmic spirals
    pitch = np.tan(np.radians(14.0))
    arm_phase = np.log(r / 0.06) / pitch
    main = np.exp(2.6 * (np.cos(2 * (th - arm_phase)) - 1))
    weak = np.exp(2.0 * (np.cos(2 * (th - arm_phase) + np.pi / 2) - 1)) * 0.35
    arms = main + weak
    clump = np.clip(1 + 0.55 * _noise(rng, n, (3, 9, 28), (0.5, 0.8, 0.9)), 0.15, None)

    disc = np.exp(-r / 0.26) * (r < 1.0)
    edge = np.clip((1.0 - r) / 0.12, 0, 1) ** 1.5
    bulge = np.exp(-(r / 0.07) ** 2) * 1.8 + np.exp(-r / 0.04) * 0.5
    bar = np.exp(-((x * np.cos(0.5) + y * np.sin(0.5)) / 0.20) ** 2 - ((-x * np.sin(0.5) + y * np.cos(0.5)) / 0.065) ** 2) * 0.8

    dust = np.clip(_noise(rng, n, (2, 6), (0.7, 0.7)), 0, None)
    arm_glow = (0.03 + 1.5 * arms**1.3) * clump * disc * edge
    dust_lane = np.clip(1 - 0.55 * dust * np.exp(2.2 * (np.cos(2 * (th - arm_phase) - 0.35) - 1)), 0.25, 1)
    light = (arm_glow * dust_lane + bulge + bar) * edge

    # colour: blue-white arms, warm old-star core, deep contrast
    core_w = np.clip(bulge / (bulge + 0.6 * arm_glow + 1e-6), 0, 1)[..., None]
    arm_col = np.array([0.62, 0.74, 1.0])
    core_col = np.array([1.0, 0.82, 0.55])
    rgb = arm_col * (1 - core_w) + core_col * core_w
    # sprinkle bright star knots along the arms
    knots = (rng.random((n, n)) < 0.0007 + 0.003 * arms * disc * edge) * rng.random((n, n))
    knots = gaussian_filter(knots, 1.7) * 60
    light = light + knots * 0.35 * edge

    a = 1 - np.exp(-1.15 * light)           # soft film curve keeps the core from clipping flat
    a = np.clip(a, 0, 1) ** 1.05
    rgb = np.clip(rgb * (0.82 + 0.35 * core_w), 0, 1)
    rgba = np.dstack([rgb, a])
    return (rgba * 255).astype(np.uint8)


if __name__ == "__main__":
    from PIL import Image
    a = make_galaxy(1024).astype(float)
    rgb = a[..., :3] * a[..., 3:] / 255
    Image.fromarray(rgb.astype("uint8")).save("galaxy_preview.png")
