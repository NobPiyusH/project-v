"""Renders a diagram spec (workers/diagram/spec.py) to an MP4 with Manim: 1920x1080, 24 fps, dark background.

Why Manim: it installs on Windows from wheels (manimpango, pycairo, skia-pathops) and renders plain `Text` through
Pango, so no LaTeX is needed; its vector output is crisp at 1080p and small on disk.

Every animation is a function of time: one updater advances a clock each frame and applies the steps of the spec
(captured start state + easing), so overlapping steps compose and the run is deterministic. Rendering runs in a
subprocess (`python -m workers.diagram.render spec.json out.mp4`): Manim keeps global state, and a crash in it must
never take the Temporal worker down.
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from workers.diagram.spec import PALETTE, color_hex, enrich_motion, fallback_spec, validate_spec

import os

FONT = os.environ.get("DIAGRAM_FONT", "Segoe UI")  # Pango falls back to its default sans when the font is missing
REPO_ROOT = Path(__file__).resolve().parents[2]
FPS, WIDTH, HEIGHT = 24, 1920, 1080


def render_to_file(spec: dict, out_path: str | Path, timeout: int = 900, width: int = WIDTH, height: int = HEIGHT) -> Path:
    """Renders in a subprocess; raises RuntimeError with the renderer's last output on failure."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="diagram-") as tmp:
        spec_file = Path(tmp) / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "workers.diagram.render", str(spec_file), str(out_path), str(width), str(height)],
                           cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
                           env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT)})
    if p.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"diagram render failed (exit {p.returncode}): {(p.stderr or p.stdout)[-800:]}")
    return out_path


def render_or_fallback(spec, out_path: str | Path, title: str = "", lines: list[str] | None = None,
                       duration: float = 6.0, width: int = WIDTH, height: int = HEIGHT) -> dict:
    """Validates and renders the spec; an invalid spec or a failed render becomes the animated text card. Returns
    {"path", "fallback": bool, "reason": str|None}."""
    reason = None
    errs = validate_spec(spec)
    if errs:
        reason = "invalid spec: " + "; ".join(errs[:3])
    else:
        try:
            render_to_file(spec, out_path, width=width, height=height)
            return {"path": str(out_path), "fallback": False, "reason": None}
        except Exception as e:  # noqa: BLE001 - never lose the shot over a drawing problem
            reason = f"render failed: {e}"[:400]
    d = spec.get("duration", duration) if isinstance(spec, dict) and isinstance(spec.get("duration"), (int, float)) else duration
    card = fallback_spec(title or (spec.get("title") if isinstance(spec, dict) else "") or "", lines, d)
    render_to_file(card, out_path, width=width, height=height)
    return {"path": str(out_path), "fallback": True, "reason": reason}


# ------------------------------------------------------------------------------------------------- scene (in-process)

def _build_scene_class():
    """Imports Manim lazily (heavy; only the renderer process needs it)."""
    from manim import (
        DOWN, LEFT, RIGHT, UP, Arrow, Circle, DashedLine, Dot, Intersection, Line, Mobject, Polygon, RoundedRectangle,
        Scene, Text, VGroup, rate_functions as rf,
    )
    import numpy as np
    from manim.utils.color import ManimColor, interpolate_color

    EASE = {"linear": rf.linear, "smooth": rf.smooth, "ease_in": rf.ease_in_cubic, "ease_out": rf.ease_out_cubic,
            "ease_in_out": rf.ease_in_out_cubic, "bounce": rf.ease_out_bounce, "there_and_back": rf.there_and_back}
    DIRS = {"up": UP, "down": DOWN, "left": LEFT, "right": RIGHT}

    def col(c, default="text"):
        return ManimColor(color_hex(c, default))

    def fit(t, max_w):
        if t.width > max_w > 0:
            t.scale_to_fit_width(max_w)
        return t

    def label(text, size, color, max_w):
        return fit(Text(text, font=FONT, font_size=size, color=col(color, "text")), max_w)

    class Actor:
        def __init__(self, spec, group, shapes):
            self.spec, self.g, self.shapes = spec, group, shapes
            self.type = spec["type"]
            self.base = {id(m): (m.get_fill_opacity(), m.get_stroke_opacity()) for m in group.family_members_with_points()}
            self.alpha = 1.0
            self.scale_f = 1.0

        def set_alpha(self, a):
            a = max(0.0, min(1.0, a))
            self.alpha = a
            for m in self.g.family_members_with_points():
                f, s = self.base.get(id(m), (m.get_fill_opacity(), m.get_stroke_opacity()))
                m.set_fill(opacity=f * a)
                m.set_stroke(opacity=s * a)

    def make(e):
        t = e["type"]
        c = col(e.get("color"), "cyan" if t != "text" else "text")
        sw = float(e.get("width", 4))
        size = int(e.get("size", 26))
        txt = e.get("text")
        fill = float(e.get("fill", 0.14))
        shapes = []
        if t in ("box", "layer", "circle"):
            if t == "box":
                body = RoundedRectangle(width=e["w"], height=e["h"], corner_radius=min(0.14, e["h"] / 4))
                body.move_to([e["x"], e["y"], 0])
            elif t == "layer":
                s = float(e.get("skew", 0.55))
                x, y, w, h = e["x"], e["y"], e["w"], e["h"]
                body = Polygon([x - w / 2 + s, y + h / 2, 0], [x + w / 2 + s, y + h / 2, 0],
                               [x + w / 2 - s, y - h / 2, 0], [x - w / 2 - s, y - h / 2, 0])
            else:
                body = Circle(radius=e["r"]).move_to([e["x"], e["y"], 0])
            body.set_stroke(c, sw).set_fill(c, fill)
            parts = [body]
            shapes = [body]
            if txt:
                width = (e.get("w") or e.get("r", 1) * 2) * 0.9
                lab = label(txt, size, e.get("text_color", "text"), width)
                lab.move_to(body.get_center())
                parts.append(lab)
            return VGroup(*parts), shapes
        if t in ("line", "beam", "arrow"):
            p1, p2 = np.array([e["x1"], e["y1"], 0.0]), np.array([e["x2"], e["y2"], 0.0])
            if t == "beam":
                parts = [Line(p1, p2, stroke_width=sw * 3.2, color=c).set_stroke(opacity=0.12),
                         Line(p1, p2, stroke_width=sw * 1.8, color=c).set_stroke(opacity=0.28),
                         Line(p1, p2, stroke_width=sw, color=c)]
                return VGroup(*parts), parts
            if e.get("dashed"):
                shaft = DashedLine(p1, p2, stroke_width=sw, color=c, dash_length=0.18)
            else:
                shaft = Line(p1, p2, stroke_width=sw, color=c)
            if t == "line":
                return VGroup(shaft), [shaft]
            d = (p2 - p1) / max(np.linalg.norm(p2 - p1), 1e-6)
            n = np.array([-d[1], d[0], 0.0])
            tl = 0.32
            tip = Polygon(p2, p2 - d * tl + n * tl * 0.5, p2 - d * tl - n * tl * 0.5).set_fill(c, 1).set_stroke(c, 1)
            return VGroup(shaft, tip), [shaft, tip]
        if t == "text":
            tx = Text(txt, font=FONT, font_size=size, color=c if "color" in e else col("text"), line_spacing=0.9)
            fit(tx, 13.2)
            tx.move_to([e["x"], e["y"], 0])
            return VGroup(tx), [tx]
        if t == "particles":
            rng = random.Random(zlib.crc32(e["id"].encode()))
            spread = float(e.get("spread", 0.5))
            dots = [Dot([e["x"] + rng.gauss(0, spread), e["y"] + rng.gauss(0, spread * 0.8), 0], radius=float(e.get("r", 0.05)),
                        color=c) for _ in range(int(e["count"]))]
            return VGroup(*dots), dots
        if t == "dots_grid":
            dots = []
            for i in range(int(e["cols"])):
                for j in range(int(e["rows"])):
                    dots.append(Dot([e["x"] + (i - (e["cols"] - 1) / 2) * e["dx"], e["y"] + (j - (e["rows"] - 1) / 2) * e["dy"], 0],
                                    radius=float(e.get("r", 0.05)), color=c))
            return VGroup(*dots), dots
        if t == "rgb_mix":
            r = e["r"]
            cs = [PALETTE["pure_red"], PALETTE["pure_green"], PALETTE["pure_blue"]]
            circles = [Circle(radius=r).set_stroke(ManimColor(k), 3).set_fill(ManimColor(k), 0.85) for k in cs]
            overlaps = []
            final = _mix_positions(e, 1.0)
            for circ, p in zip(circles, final):
                circ.move_to(p)
            for a, b, k in ((0, 1, PALETTE["yellow"]), (1, 2, PALETTE["cyan"]), (0, 2, "#FF3DF0")):
                ov = Intersection(circles[a], circles[b], color=ManimColor(k), fill_opacity=1, stroke_width=0)
                overlaps.append(ov)
            white = Intersection(*circles, color=ManimColor("#FFFFFF"), fill_opacity=1, stroke_width=0)
            overlaps.append(white)
            return VGroup(*circles, *overlaps), circles
        if t == "callout":
            tx = label(txt, size, e.get("color", "text"), 4.8)
            tx.move_to([e["x"], e["y"], 0])
            tgt = np.array([e["tx"], e["ty"], 0.0])
            ln = Line(tx.get_edge_center(DOWN) if e["ty"] < e["y"] else tx.get_edge_center(UP), tgt, stroke_width=2.5, color=c)
            dot = Dot(tgt, radius=0.07, color=c)
            return VGroup(tx, ln, dot), [ln, dot]
        raise ValueError(f"unknown element type {t}")

    def _mix_positions(e, k):
        """Centres of the red, green, blue circles: k=0 far apart, k=1 overlapped like the classic colour diagram."""
        r = e["r"]
        d = r * (2.0 - 1.1 * k)
        out = []
        for ang in (90, 210, 330):
            a = math.radians(ang)
            out.append(np.array([e["x"] + d * math.cos(a) * 0.62, e["y"] + d * math.sin(a) * 0.62 + (0.08 * r), 0]))
        return out

    class SpecScene(Scene):
        def __init__(self, spec, **kw):
            super().__init__(**kw)
            self.spec = spec

        def construct(self):
            spec = self.spec
            steps = sorted(({**s} for s in spec.get("steps", [])), key=lambda s: s["at"])
            hidden_by_step = {s["target"] for s in steps if s["do"] in ("show", "draw")}
            mixers = {s["target"] for s in steps if s["do"] == "mix"}
            actors: dict[str, Actor] = {}
            for e in spec["elements"]:
                g, shapes = make(e)
                a = Actor(e, g, shapes)
                actors[e["id"]] = a
                g.add_updater(lambda m: None)  # marks it "moving": the cairo renderer caches static mobjects as one image
                self.add(g)
                if e["type"] == "rgb_mix" and e["id"] in mixers:
                    self._mix(a, 0.0)
                if e["id"] in hidden_by_step and not e.get("visible"):
                    a.set_alpha(0.0)
            self.actors, self.steps, self.t = actors, steps, 0.0
            self.flow_dots: dict[int, list] = {}
            clock = Mobject()
            clock.add_updater(lambda m, dt: self._tick(dt))
            self.add(clock)
            self.wait(float(spec["duration"]))

        # -- one frame
        def _tick(self, dt):
            self.t += dt
            for i, s in enumerate(self.steps):
                if s.get("_done") or self.t < s["at"]:
                    continue
                a = min(1.0, (self.t - s["at"]) / s["dur"])
                default = "linear" if s["do"] == "flow" else "smooth"
                e = EASE[s.get("ease") or default](a)
                if "_st" not in s:
                    s["_st"] = self._start(s)
                self._apply(i, s, e)
                if a >= 1.0:
                    s["_done"] = True
                    self._finish(i, s)

        def _start(self, s):
            a = self.actors[s["target"]]
            st = {"c": a.g.get_center().copy(), "prev": 1.0, "alpha0": a.alpha}
            if s["do"] == "color":
                st["from"] = [(m, m.get_fill_color() if m.get_fill_opacity() > 0 else m.get_stroke_color()) for m in a.shapes]
            if s["do"] == "draw":
                st["orig"] = [(m, m.copy()) for m in a.shapes if m.has_points()]
                a.set_alpha(1.0)
            if s["do"] == "show" and s.get("from") in DIRS:
                st["off"] = DIRS[s["from"]] * 0.45
            if s["do"] == "flow":
                e = a.spec
                d = np.array([e["x2"] - e["x1"], e["y2"] - e["y1"], 0.0])
                n = int(s.get("count", 10))
                dots = [Dot(radius=float(s.get("r", 0.08)), color=col(s.get("color") or e.get("color"), "white")) for _ in range(n)]
                for dt_ in dots:
                    dt_.set_opacity(0)
                    dt_.add_updater(lambda m: None)
                    self.add(dt_)
                st["dots"], st["d"] = dots, d
            return st

        def _apply(self, i, s, e):
            a, st, kind = self.actors[s["target"]], s["_st"], s["do"]
            if kind == "show":
                if "off" in st:
                    a.g.move_to(st["c"] + st["off"] * (1 - e))
                a.set_alpha(st["alpha0"] + (1 - st["alpha0"]) * e)
            elif kind == "hide":
                a.set_alpha(st["alpha0"] * (1 - e))
            elif kind == "move":
                tx = s["x"] if "x" in s else st["c"][0] + s.get("dx", 0)
                ty = s["y"] if "y" in s else st["c"][1] + s.get("dy", 0)
                a.g.move_to(st["c"] + (np.array([tx, ty, 0.0]) - st["c"]) * e)
            elif kind in ("scale", "pulse"):
                f = 1 + (s["to"] - 1) * e if kind == "scale" else 1 + float(s.get("amount", 0.14)) * math.sin(math.pi * e)
                a.g.scale(f / st["prev"], about_point=st["c"])
                st["prev"] = f
            elif kind == "color":
                to = col(s["to"])
                for m, c0 in st["from"]:
                    c = interpolate_color(ManimColor(c0), to, e)
                    if m.get_fill_opacity() > 0:
                        m.set_fill(c)
                    m.set_stroke(c)
            elif kind == "draw":
                for m, orig in st["orig"]:
                    if orig.get_num_points() > 0 and not (a.type == "arrow" and m is a.shapes[1]):
                        m.pointwise_become_partial(orig, 0, e)
                if a.type == "arrow":
                    shaft, tip = a.shapes
                    tip.set_opacity(1 if e > 0.02 else 0)
                    o_tip = st["orig"][1][1]
                    start = a.spec
                    p1 = np.array([start["x1"], start["y1"], 0.0])
                    p2 = np.array([start["x2"], start["y2"], 0.0])
                    tip.move_to(o_tip.get_center() + (p1 - p2) * (1 - e))
                else:
                    for m in a.g.family_members_with_points():
                        if m not in a.shapes and not any(m in sh.get_family() for sh in a.shapes):
                            m.set_opacity(e)  # labels fade in while the outline is drawn
            elif kind == "flow":
                p1 = np.array([a.spec["x1"], a.spec["y1"], 0.0])
                p2 = np.array([a.spec["x2"], a.spec["y2"], 0.0])
                if s.get("reverse"):
                    p1, p2 = p2, p1
                loops = float(s.get("loops", 1.0))
                dots = st["dots"]
                for k, d in enumerate(dots):
                    ph = (e * loops + k / len(dots)) % 1.0 if loops > 1 or len(dots) > 1 else e
                    live = 0.0 <= e < 1.0
                    d.move_to(p1 + (p2 - p1) * ph)
                    d.set_opacity(math.sin(math.pi * ph) ** 0.5 if live else 0)
            elif kind == "mix":
                self._mix(a, e)

        def _finish(self, i, s):
            st = s["_st"]
            for d in st.get("dots", []):
                d.set_opacity(0)

        def _mix(self, a, e):
            circles = a.g.submobjects[:3]
            overlaps = a.g.submobjects[3:]
            k = min(1.0, e / 0.6)
            for circ, p in zip(circles, _mix_positions(a.spec, k)):
                circ.move_to(p)
            ov_alpha = max(0.0, (e - 0.6) / 0.4)
            for ov in overlaps:
                ov.set_fill(opacity=ov_alpha)

    return SpecScene


def render_in_process(spec: dict, out_path: str | Path, fps: int = FPS, width: int = WIDTH, height: int = HEIGHT) -> Path:
    from manim import config

    out_path = Path(out_path)
    with tempfile.TemporaryDirectory(prefix="manim-") as tmp:
        config.media_dir = tmp
        config.pixel_width, config.pixel_height, config.frame_rate = width, height, fps
        config.background_color = color_hex(spec.get("background"), "bg")
        config.verbosity = "ERROR"
        config.progress_bar = "none"
        config.disable_caching = True
        config.output_file = "diagram"
        config.write_to_movie = True
        scene = _build_scene_class()(spec)
        scene.render()
        produced = Path(scene.renderer.file_writer.movie_file_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(produced.read_bytes())
    return out_path


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 4):
        print("usage: python -m workers.diagram.render spec.json out.mp4 [width height]", file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    errs = validate_spec(spec)
    if errs:
        print("invalid spec: " + "; ".join(errs), file=sys.stderr)
        return 3
    if not spec.get("fallback") and os.environ.get("DIAGRAM_AMBIENT_MOTION", "1") != "0":
        spec = enrich_motion(spec)  # nothing sits still; the Director's steps are kept
    w, h = (int(argv[2]), int(argv[3])) if len(argv) == 4 else (WIDTH, HEIGHT)
    if w % 2 or h % 2 or not (640 <= w <= 3840) or not (360 <= h <= 2160):
        print(f"bad size {w}x{h}", file=sys.stderr)
        return 2
    render_in_process(spec, argv[1], width=w, height=h)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
