"""Plan-driven 2D science zoom (Manim Community, Cairo renderer, CPU only).

One continuous camera zoom-out through nested orbital systems. Everything comes from a JSON plan
(see plans/moon_earth_milkyway.json); everything is drawn procedurally (circles, stacked-circle glow,
a generated galaxy texture). No downloads, no LaTeX.

    python zoom.py --plan plans/moon_earth_milkyway.json --out OUTDIR --scale 0.444       # movie (480x853)
    python zoom.py --plan ... --out OUTDIR --stills 0,4,8,12      # PNG stills at those seconds
    python zoom.py --plan ... --report                           # print the computed timeline only
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np

FRAME_W, FRAME_H = 4.5, 8.0  # manim units; 9:16
HALF_W = FRAME_W / 2


def clip01(x):
    return min(1.0, max(0.0, x))


def smooth(x):
    x = clip01(x)
    return x * x * (3 - 2 * x)


def ramp(x, a, b):
    """0 at x<=a, 1 at x>=b (works for a>b too)."""
    return smooth((x - a) / (b - a))


class Timeline:
    """All the maths of the zoom, separate from drawing so it can be reported and tested."""

    def __init__(self, plan: dict):
        self.plan = plan
        v = plan["video"]
        self.duration = float(v["duration"])
        self.fps = int(v["fps"])
        self.stages = plan["stages"]
        z = plan["zoom"]
        r_first = self.stages[0]["orbit_radius_m"]
        r_last = self.stages[-1]["orbit_radius_m"]
        self.W0 = r_first * HALF_W / z["first_orbit_screen_radius"]
        self.W1 = r_last * HALF_W / z["last_orbit_screen_radius"]
        self.k = math.log10(self.W1 / self.W0) / self.duration  # powers of ten per second
        n = len(self.stages)
        self.rate_log = [math.log10(s["rate_days_per_s"]) for s in self.stages]
        # integrate the sped-up clock once, finely
        steps = int(self.duration * self.fps)
        self._T = np.zeros(steps + 2)
        sub = 20
        t = 0.0
        acc = 0.0
        dt = 1.0 / (self.fps * sub)
        for i in range(steps + 1):
            self._T[i] = acc
            for _ in range(sub):
                acc += 10 ** self.log_rate(t + dt / 2) * dt
                t += dt
        self._T[steps + 1] = acc
        assert n == 3, "this renderer handles three nested systems; extend draw_frame for more"

    def W(self, t):
        return self.W0 * 10 ** (self.k * t)

    def R(self, i, t):
        """On-screen radius (units) of stage i's orbit if the camera sat on its centre."""
        return self.stages[i]["orbit_radius_m"] * HALF_W / self.W(t)

    def handover(self, i, t):
        a, b = self.stages[i]["handover"]
        return 1.0 - ramp(self.R(i, t), b, a)

    def weights(self, t):
        wS = self.handover(1, t)
        wG = self.handover(2, t)
        w = [1 - wS, max(0.0, wS - wG), wG]
        s = sum(w)
        return [x / s for x in w], wS, wG

    def log_rate(self, t):
        w, _, _ = self.weights(t)
        return sum(wi * ri for wi, ri in zip(w, self.rate_log))

    def T(self, frame):
        return float(self._T[min(frame, len(self._T) - 1)])

    def layer_alpha(self, i, t):
        s = self.stages[i]
        R = self.R(i, t)
        a = 1.0
        if s.get("fade_in_radius"):
            hi, lo = s["fade_in_radius"]
            a *= 1 - ramp(R, lo, hi)
        if s.get("fade_out_radius"):
            hi, lo = s["fade_out_radius"]
            a *= ramp(R, lo, hi)
        return a

    def report(self):
        print(f"zoom: {self.k:.4f} powers of ten per second, view half-width {self.W0:.3g} m -> {self.W1:.3g} m "
              f"(x{self.W1 / self.W0:.0f})")
        for i, s in enumerate(self.stages):
            ts = [j / 30 for j in range(int(self.duration * 30) + 1)]
            vis = [t for t in ts if self.layer_alpha(i, t) > 0.02]
            lab = [t for t in ts if self.weights(t)[0][i] > 0.5]
            print(f"  {s['id']:7s} visible {vis[0] if vis else None:.1f}-{vis[-1] if vis else None:.1f} s | "
                  f"label on {lab[0] if lab else float('nan'):.1f}-{lab[-1] if lab else float('nan'):.1f} s | "
                  f"orbit radius at t=0 {self.R(i, 0):.3g}, at end {self.R(i, self.duration):.3g} units")
        print(f"  simulated time at end: {self._T[-2] / 365.25:.3g} years")


def hexrgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])


def mix(c1, c2, f):
    return c1 * (1 - f) + c2 * f


def to_hex(c):
    c = np.clip(c, 0, 1)
    return "#%02x%02x%02x" % tuple(int(round(x * 255)) for x in c)


def build_scene_class(plan, still_t=None):
    from manim import (Circle, Dot, FadeIn, ImageMobject, Mobject, Scene, Text, VGroup, config, ORIGIN, UP, DOWN)
    from manim import config as mconfig

    from galaxy import make_galaxy

    tl = Timeline(plan)
    look = plan["look"]
    bg = look["background"]
    rng = np.random.default_rng(look["star_seed"])
    gal_src = make_galaxy(2048)
    gal_stage = plan["stages"][2]
    disc_factor = gal_stage.get("disc_radius_factor", 2.0)

    # --- starfields -----------------------------------------------------------------------------------
    n_far = look["far_stars"]
    far = np.column_stack([rng.uniform(-HALF_W, HALF_W, n_far), rng.uniform(-FRAME_H / 2, FRAME_H / 2, n_far)])
    far_b = rng.uniform(0.15, 0.75, n_far) ** 1.6
    far_r = rng.uniform(0.004, 0.012, n_far)
    n_near = look["near_stars"]
    lo, hi = math.log10(tl.stages[0]["orbit_radius_m"] * 0.5), math.log10(tl.stages[2]["orbit_radius_m"] * 6)
    near_r_m = 10 ** rng.uniform(lo, hi, n_near)
    near_ang = rng.uniform(0, 2 * math.pi, n_near)
    near_b = rng.uniform(0.25, 1.0, n_near) ** 1.4
    near_tint = rng.uniform(0, 1, n_near)
    cool, warm, white = hexrgb("#9db8ff"), hexrgb("#ffd9b0"), hexrgb("#f2f4ff")

    earth_dark, earth_lit = hexrgb("#0a1d3d"), hexrgb("#74b4ea")
    moon_dark, moon_lit = hexrgb("#1a1a1d"), hexrgb("#cfcac0")
    sun_core, sun_mid, sun_rim = hexrgb("#fff3d2"), hexrgb("#ffc977"), hexrgb("#e8761f")
    ring_col = hexrgb("#8aa0c8")

    def circle(c, r, color, op, stroke=0.0, stroke_col=None, stroke_op=1.0):
        m = Circle(radius=max(r, 1e-4))
        if op > 0:
            m.set_fill(to_hex(color), opacity=op)
        else:
            m.set_fill(opacity=0)
        if stroke > 0:
            m.set_stroke(to_hex(stroke_col if stroke_col is not None else color), width=stroke, opacity=stroke_op)
        else:
            m.set_stroke(width=0, opacity=0)
        m.move_to([c[0], c[1], 0])
        return m

    def glow(c, r_in, r_out, color, strength, n=14):
        out = []
        for i in range(n):
            f = i / (n - 1)
            rad = r_out * (r_in / r_out) ** f
            out.append(circle(c, rad, color, strength * (0.35 + 0.65 * f) / n * 3.0))
        return out

    def sphere(c, r, dark, lit, ldir, alpha, atmos=None, n=18):
        out = []
        if alpha <= 0.004:
            return out
        if atmos is not None and r > 0.012:
            out += glow(c, r * 1.02, r * 1.45, atmos, 0.22 * alpha, n=12)
        out.append(circle(c, r, dark, alpha))
        if r < 0.014:
            out.append(circle(c, r * 0.8, lit, alpha * 0.9))
            return out
        for i in range(1, n + 1):
            f = i / n
            rho = r * (1 - 0.62 * f)
            off = (r - rho) * 0.92
            out.append(circle((c[0] + ldir[0] * off, c[1] + ldir[1] * off), rho, mix(dark, lit, f ** 0.9), alpha))
        return out

    class ZoomScene(Scene):
        def construct(self):
            self.camera.background_color = bg
            self.tt = 0.0
            self.frame_no = 0
            self.holder = Mobject()
            self.add(self.holder)

            def mk(txt, size, op):
                t = Text(txt, font="Segoe UI", font_size=size, color="#cfd8ea")
                t.set_opacity(op)
                return t

            self.size_time = 22
            self.labels = [mk(s["label"], self.size_time, 1) for s in tl.stages]
            self.names = [mk(s["name"], 17, 1) for s in tl.stages]
            self.note = mk(plan["note_text"], 13, 0.55)
            self.note.move_to([0, -3.34, 0])
            for t_ in self.labels:
                t_.move_to([0, -2.88, 0])
            for t_ in self.names:
                t_.move_to([0, 3.15, 0])
            # One scene.wait per frame: Manim caches "static" mobjects across a long wait, which would
            # freeze our rebuilt-every-frame drawing, so we redraw explicitly instead of using updaters.
            if still_t is not None:
                self.tt = still_t
                self.frame_no = int(round(still_t * tl.fps))
                self.step(0.0, first=True)
                self.wait(1 / tl.fps)
            else:
                n_frames = int(round(tl.duration * tl.fps))
                if os.environ.get("ZOOM_MAX_FRAMES"):
                    n_frames = min(n_frames, int(os.environ["ZOOM_MAX_FRAMES"]))
                for f in range(n_frames):
                    self.tt = f / tl.fps
                    self.frame_no = f
                    self.step(0.0, first=True)
                    self.wait(1 / tl.fps, frozen_frame=False)

        # -------------------------------------------------------------------------------------------------
        def step(self, dt, first=False):
            if not first:
                self.tt += dt
                self.frame_no = int(round(self.tt * tl.fps))
            t = min(self.tt, tl.duration)
            self.holder.submobjects = []
            self.holder.add(*self.draw_frame(t, self.frame_no))

        def draw_frame(self, t, frame):
            st = tl.stages
            W = tl.W(t)
            u = HALF_W / W  # screen units per metre
            T = tl.T(frame)  # simulated days
            ang = [2 * math.pi * (s["phase0"] + T / s["period_days"]) for s in st]
            r1, r2, r3 = (s["orbit_radius_m"] for s in st)
            m = (r1 * math.cos(ang[0]), r1 * math.sin(ang[0]))  # Moon relative to Earth
            e = (r2 * math.cos(ang[1]), r2 * math.sin(ang[1]))  # Earth relative to Sun
            s_ = (r3 * math.cos(ang[2]), r3 * math.sin(ang[2]))  # Sun relative to galactic centre
            E, S = (0.0, 0.0), (-e[0], -e[1])
            G = (-(e[0] + s_[0]), -(e[1] + s_[1]))
            Mo = m
            _, wS, wG = tl.weights(t)
            F = ((1 - wG) * wS * S[0] + wG * G[0], (1 - wG) * wS * S[1] + wG * G[1])

            def scr(p):
                return ((p[0] - F[0]) * u, (p[1] - F[1]) * u)

            pE, pS, pG, pM = scr(E), scr(S), scr(G), scr(Mo)
            R1, R2, R3 = tl.R(0, t), tl.R(1, t), tl.R(2, t)
            a1, a2, a3 = (tl.layer_alpha(i, t) for i in range(3))
            out = []

            # 1. far stars: fixed, only a faint breathing so the backdrop is never dead
            for (x, y), b, rr in zip(far, far_b, far_r):
                out.append(Dot([x, y, 0], radius=rr).set_fill(to_hex(white * (0.55 + 0.45 * b)), opacity=0.25 + 0.6 * b)
                           .set_stroke(width=0))

            # 2. near stars: world-anchored, they stream toward the centre as we zoom out
            for rm, an, b, tint in zip(near_r_m, near_ang, near_b, near_tint):
                rs = rm * u
                x, y = rs * math.cos(an), rs * math.sin(an)
                if abs(x) > HALF_W + 0.05 or abs(y) > FRAME_H / 2 + 0.05:
                    continue
                vis = ramp(rs, 0.5, 1.5)
                if vis < 0.03:
                    continue
                col = mix(cool, warm, tint) * 0.5 + white * 0.5
                out.append(Dot([x, y, 0], radius=0.0075 + 0.012 * b).set_fill(to_hex(col), opacity=vis * (0.35 + 0.6 * b))
                           .set_stroke(width=0))

            # 3. galaxy texture (cropped to the visible window so it never gets huge)
            if a3 > 0.01:
                half = r3 * disc_factor * u / 0.96 * 1.0  # half-size of the image square in screen units
                cx, cy = pG
                x0, x1 = max(cx - half, -HALF_W), min(cx + half, HALF_W)
                y0, y1 = max(cy - half, -FRAME_H / 2), min(cy + half, FRAME_H / 2)
                if x1 > x0 and y1 > y0:
                    n = gal_src.shape[0]
                    # screen -> source pixels (y flips)
                    px0 = int(max(0, (x0 - (cx - half)) / (2 * half) * n))
                    px1 = int(min(n, math.ceil((x1 - (cx - half)) / (2 * half) * n)))
                    py0 = int(max(0, ((cy + half) - y1) / (2 * half) * n))
                    py1 = int(min(n, math.ceil(((cy + half) - y0) / (2 * half) * n)))
                    if px1 - px0 > 1 and py1 - py0 > 1:
                        crop = gal_src[py0:py1, px0:px1].copy()
                        crop[..., 3] = (crop[..., 3].astype(np.float32) * a3 * 0.95).astype(np.uint8)
                        wx = (px1 - px0) / n * 2 * half
                        wy = (py1 - py0) / n * 2 * half
                        ccx = cx - half + (px0 + px1) / 2 / n * 2 * half
                        ccy = cy + half - (py0 + py1) / 2 / n * 2 * half
                        img = ImageMobject(crop)
                        img.stretch_to_fit_width(wx).stretch_to_fit_height(wy).move_to([ccx, ccy, 0])
                        out.append(img)

            # 4. orbit rings
            if a3 > 0.01:
                out.append(circle(pG, R3, ring_col, 0, stroke=1.6, stroke_op=0.45 * a3))
            if a2 > 0.01:
                out.append(circle(pS, R2, ring_col, 0, stroke=1.4, stroke_op=0.42 * a2))
            if a1 > 0.01:
                out.append(circle(pE, R1, ring_col, 0, stroke=1.4, stroke_op=0.42 * a1))

            # 5. Sun: glow always; body scales with the layer it lives in
            sun_r = max(0.10 * R2, min(0.045, 0.02 * R3 + 0.006))
            sun_vis = 1.0
            ls = math.hypot(*scr((0, 0)))  # unused, keeps linters calm about scr
            if abs(pS[0]) < HALF_W + 3 * sun_r + 2 and abs(pS[1]) < FRAME_H / 2 + 3 * sun_r + 2:
                big = clip01(a2 * 1.0 + 0.0)
                out += glow(pS, sun_r * 1.1, sun_r * (7.0 * big + 3.5 * (1 - big)), sun_rim, 0.26 * sun_vis, n=44)
                out += glow(pS, sun_r * 0.9, sun_r * 2.6, sun_mid, 0.55, n=26)
                out.append(circle(pS, sun_r, sun_mid, 1.0))
                out.append(circle(pS, sun_r * 0.78, sun_core, 1.0))

            # 6. Earth and Moon, lit from the Sun's true direction
            def unit(a, b):
                d = math.hypot(a, b)
                return (a / d, b / d) if d > 1e-30 else (0.0, 1.0)

            sun_dir = unit(S[0] - E[0], S[1] - E[1])
            earth_r = max(0.16 * R1, min(0.028, 0.014 * R2 + 0.006))
            earth_a = 1 - ramp(R2, 0.12, 0.03)
            out += sphere(pE, earth_r, earth_dark, earth_lit, sun_dir, earth_a, atmos=hexrgb("#6ab6ff"))
            moon_r = max(0.050 * R1, 0.003)
            if a1 > 0.01:
                out += sphere(pM, moon_r, moon_dark, moon_lit, sun_dir, a1)

            # 7. text
            w, _, _ = tl.weights(t)
            for i in range(3):
                la = ramp(w[i], 0.45, 0.8)
                if la > 0.01:
                    self.labels[i].set_opacity(la)
                    self.names[i].set_opacity(0.55 * la)
                    out += [self.labels[i], self.names[i]]
            out.append(self.note)
            return out

    return ZoomScene, tl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", help="output directory (media dir)")
    ap.add_argument("--name", default="zoom")
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 = plan size, 0.5 = half size, 2.0 = 4K")
    ap.add_argument("--stills", help="comma separated seconds; renders PNG stills instead of the movie")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if args.report:
        Timeline(plan).report()
        return

    from manim import config
    v = plan["video"]
    w, h = int(round(v["width"] * args.scale)), int(round(v["height"] * args.scale))
    config.pixel_width, config.pixel_height = w, h
    config.frame_width, config.frame_height = FRAME_W, FRAME_H
    config.frame_rate = v["fps"]
    config.media_dir = str(Path(args.out).resolve())
    config.disable_caching = True
    config.verbosity = "WARNING"
    config.progress_bar = "none"

    if args.stills:
        for tstr in args.stills.split(","):
            tv = float(tstr)
            cls, _ = build_scene_class(plan, still_t=tv)
            config.save_last_frame = True
            config.write_to_movie = False
            config.output_file = f"{args.name}_t{tv:05.1f}"
            cls().render()
    else:
        cls, _ = build_scene_class(plan)
        config.save_last_frame = False
        config.write_to_movie = True
        config.output_file = args.name
        config.movie_file_extension = ".mp4"
        cls().render()


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
