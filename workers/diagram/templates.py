"""Four ready diagram scenes for "How a television works" (also the worked examples the spec writer's prompt shows).

python -m workers.diagram.templates OUT_DIR   renders every template to OUT_DIR/<name>.mp4 (+ <name>.json)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _title(text):
    return {"id": "title", "type": "text", "x": 0, "y": 3.5, "text": text, "size": 42, "color": "white"}


def _caption(text, at):
    return ({"id": "cap", "type": "text", "x": 0, "y": -3.7, "text": text, "size": 26, "color": "muted"},
            {"at": at, "dur": 0.8, "do": "show", "target": "cap", "from": "up"})


def crt() -> dict:
    els = [_title("Inside a CRT television"),
           {"id": "gun", "type": "box", "x": -5.6, "y": 0, "w": 2.3, "h": 1.5, "text": "Electron\ngun", "color": "cyan"},
           {"id": "coil1", "type": "box", "x": -2.4, "y": 1.35, "w": 2.2, "h": 0.55, "text": "Deflection coil", "size": 20, "color": "violet"},
           {"id": "coil2", "type": "box", "x": -2.4, "y": -1.35, "w": 2.2, "h": 0.55, "text": "Deflection coil", "size": 20, "color": "violet"},
           {"id": "mask", "type": "box", "x": 2.0, "y": 0, "w": 0.2, "h": 5.0, "color": "muted", "fill": 0.5},
           {"id": "hole", "type": "circle", "x": 2.0, "y": 0, "r": 0.12, "color": "bg", "fill": 1.0, "width": 1},
           {"id": "mask_l", "type": "text", "x": 1.7, "y": -2.8, "text": "Shadow mask\n(one hole per dot triplet)", "size": 20, "color": "muted"},
           {"id": "screen", "type": "box", "x": 5.2, "y": 0, "w": 0.4, "h": 5.0, "color": "green", "fill": 0.3},
           {"id": "scr_l", "type": "text", "x": 5.2, "y": -2.8, "text": "Phosphor\nscreen", "size": 20, "color": "muted"}]
    ends = [(0.62, "pure_red"), (0, "pure_green"), (-0.62, "pure_blue")]
    for i, (dy, c) in enumerate(ends):
        els.append({"id": f"pre{i}", "type": "beam", "x1": -4.4, "y1": 0.3 - i * 0.3, "x2": 1.95, "y2": 0, "color": c, "width": 3})
        els.append({"id": f"post{i}", "type": "beam", "x1": 2.05, "y1": 0, "x2": 4.95, "y2": dy, "color": c, "width": 3})
        els.append({"id": f"dot{i}", "type": "circle", "x": 5.2, "y": dy, "r": 0.15, "color": c, "fill": 0.9})
    cap, capstep = _caption("Three beams pass one hole and each lands on its own phosphor dot: red, green, blue.", 9.0)
    els.append(cap)
    steps = [{"at": 0.2, "dur": 0.8, "do": "show", "target": "title", "from": "up"},
             {"at": 0.6, "dur": 0.8, "do": "show", "target": "gun", "from": "left"},
             {"at": 1.4, "dur": 0.7, "do": "show", "target": "coil1", "from": "up"},
             {"at": 1.4, "dur": 0.7, "do": "show", "target": "coil2", "from": "down"},
             {"at": 2.3, "dur": 0.8, "do": "show", "target": "mask", "from": "up"},
             {"at": 2.3, "dur": 0.8, "do": "show", "target": "hole"},
             {"at": 2.6, "dur": 0.6, "do": "show", "target": "mask_l"},
             {"at": 3.0, "dur": 0.8, "do": "show", "target": "screen", "from": "right"},
             {"at": 3.2, "dur": 0.6, "do": "show", "target": "scr_l"}]
    for i in range(3):
        steps += [{"at": 3.9 + i * 0.2, "dur": 1.0, "do": "draw", "target": f"pre{i}", "ease": "ease_in_out"},
                  {"at": 5.0 + i * 0.1, "dur": 1.0, "do": "draw", "target": f"post{i}", "ease": "ease_in_out"},
                  {"at": 5.0, "dur": 3.6, "do": "flow", "target": f"pre{i}", "count": 6, "loops": 2, "color": "white"},
                  {"at": 6.1, "dur": 2.6, "do": "flow", "target": f"post{i}", "count": 5, "loops": 2, "color": ends[i][1]},
                  {"at": 6.3, "dur": 0.5, "do": "show", "target": f"dot{i}"},
                  {"at": 7.2 + i * 0.3, "dur": 1.4, "do": "pulse", "target": f"dot{i}", "amount": 0.5}]
    steps.append(capstep)
    steps.append({"at": 3.5, "dur": 4.5, "do": "pulse", "target": "coil1", "amount": 0.06})
    steps.append({"at": 3.5, "dur": 4.5, "do": "pulse", "target": "coil2", "amount": 0.06})
    return {"version": 1, "title": "CRT: gun, deflection, shadow mask, phosphor", "duration": 11.0, "elements": els, "steps": steps}


def lcd() -> dict:
    layers = [("bl", -2.5, "yellow", "Backlight (white LEDs)"), ("p1", -1.35, "violet", "Rear polariser (vertical)"),
              ("lc", -0.2, "cyan", "Liquid crystal + transistor grid"), ("cf", 0.95, "orange", "Red / green / blue colour filter"),
              ("p2", 2.1, "violet", "Front polariser (horizontal)")]
    els = [_title("Inside an LCD screen")]
    steps = [{"at": 0.2, "dur": 0.8, "do": "show", "target": "title", "from": "up"}]
    for i, (eid, y, c, text) in enumerate(layers):
        els.append({"id": eid, "type": "layer", "x": 1.3, "y": y, "w": 6.2, "h": 0.8, "skew": 0.7, "text": text, "size": 22, "color": c})
        steps.append({"at": 0.8 + i * 0.7, "dur": 0.7, "do": "show", "target": eid, "from": "down"})
    els.append({"id": "light", "type": "beam", "x1": -3.4, "y1": -3.0, "x2": -3.4, "y2": 2.9, "color": "white", "width": 3})
    els.append({"id": "light_l", "type": "text", "x": -5.6, "y": -3.0, "text": "light", "size": 22, "color": "muted"})
    els.append({"id": "note", "type": "text", "x": -5.3, "y": 0.6, "text": "A voltage twists the\ncrystals: the pixel\npasses light or blocks it", "size": 23, "color": "text"})
    cap, capstep = _caption("Every pixel is a tiny light valve; the colour filter turns it into red, green or blue.", 9.2)
    els.append(cap)
    steps += [{"at": 4.4, "dur": 0.6, "do": "show", "target": "light"},
              {"at": 4.6, "dur": 4.8, "do": "flow", "target": "light", "count": 7, "loops": 2, "color": "white"},
              {"at": 5.2, "dur": 0.8, "do": "show", "target": "note", "from": "left"},
              {"at": 6.0, "dur": 1.0, "do": "color", "target": "lc", "to": "red"},
              {"at": 6.0, "dur": 1.0, "do": "pulse", "target": "lc", "amount": 0.08},
              {"at": 7.6, "dur": 1.0, "do": "color", "target": "lc", "to": "cyan"},
              {"at": 8.0, "dur": 1.0, "do": "pulse", "target": "cf", "amount": 0.08}, capstep]
    return {"version": 1, "title": "LCD layer stack", "duration": 11.0, "elements": els, "steps": steps}


def rgb() -> dict:
    els = [_title("Colour on a screen: red + green + blue"),
           {"id": "mix", "type": "rgb_mix", "x": -1.0, "y": -0.6, "r": 1.4}]
    lines = [("Red + Green = Yellow", "yellow", 1.3), ("Green + Blue = Cyan", "cyan", 0.5),
             ("Red + Blue = Magenta", "#FF3DF0", -0.3), ("All three = White", "white", -1.1)]
    steps = [{"at": 0.2, "dur": 0.8, "do": "show", "target": "title", "from": "up"},
             {"at": 1.0, "dur": 3.2, "do": "mix", "target": "mix", "ease": "ease_in_out"}]
    for i, (t, c, y) in enumerate(lines):
        els.append({"id": f"t{i}", "type": "text", "x": 4.6, "y": y, "text": t, "size": 26, "color": c})
        steps.append({"at": 3.4 + i * 0.7, "dur": 0.6, "do": "show", "target": f"t{i}", "from": "right"})
    els.append({"id": "sub_l", "type": "text", "x": -5.3, "y": 1.9, "text": "One pixel =\nthree dots", "size": 24, "color": "text"})
    for i, c in enumerate(("pure_red", "pure_green", "pure_blue")):
        els.append({"id": f"sub{i}", "type": "box", "x": -5.9 + i * 0.5, "y": 0.5, "w": 0.4, "h": 1.0, "color": c, "fill": 0.9})
    steps += [{"at": 5.6, "dur": 0.6, "do": "show", "target": "sub_l", "from": "left"}]
    for i in range(3):
        steps.append({"at": 5.8 + i * 0.3, "dur": 0.5, "do": "show", "target": f"sub{i}", "from": "up"})
        steps.append({"at": 7.0 + i * 0.3, "dur": 0.9, "do": "pulse", "target": f"sub{i}", "amount": 0.35})
    cap, capstep = _caption("A TV never makes yellow: it lights red and green side by side and your eye blends them.", 7.6)
    els.append(cap)
    steps.append(capstep)
    return {"version": 1, "title": "RGB additive colour mixing", "duration": 10.0, "elements": els, "steps": steps}


def broadcast() -> dict:
    names = [("cam", "Camera", "Light becomes\nan electric signal"), ("enc", "Encoder", "Picture and sound\nare compressed"),
             ("tx", "Transmitter", "Signal rides a\nradio wave"), ("ant", "Antenna", "Wave crosses\nthe air"),
             ("tun", "Tuner", "Picks your channel,\nrecovers the signal"), ("scr", "Screen", "Signal is drawn\nas pixels")]
    cols = ["cyan", "blue", "violet", "orange", "green", "yellow"]
    els = [_title("From studio to your living room")]
    steps = [{"at": 0.2, "dur": 0.8, "do": "show", "target": "title", "from": "up"}]
    for i, (eid, name, note) in enumerate(names):
        x = -5.75 + i * 2.3
        els.append({"id": eid, "type": "box", "x": x, "y": 0.3, "w": 1.95, "h": 1.2, "text": name, "size": 21, "color": cols[i]})
        els.append({"id": eid + "_n", "type": "text", "x": x, "y": -1.2, "text": note, "size": 19, "color": "muted"})
        t0 = 0.8 + i * 1.0
        steps += [{"at": t0, "dur": 0.6, "do": "show", "target": eid, "from": "up"},
                  {"at": t0 + 0.3, "dur": 0.6, "do": "show", "target": eid + "_n"}]
        if i:
            els.append({"id": f"a{i}", "type": "arrow", "x1": x - 1.3, "y1": 0.3, "x2": x - 1.0, "y2": 0.3, "color": "white", "width": 4})
            steps.append({"at": t0 - 0.2, "dur": 0.4, "do": "draw", "target": f"a{i}"})
    els.append({"id": "wire", "type": "beam", "x1": -6.2, "y1": 1.9, "x2": 6.2, "y2": 1.9, "color": "orange", "width": 3})
    els.append({"id": "wire_l", "type": "text", "x": 0, "y": 2.45, "text": "the signal", "size": 22, "color": "orange"})
    cap, capstep = _caption("Everything you watch travels this chain in a fraction of a second.", 9.4)
    els.append(cap)
    steps += [{"at": 6.2, "dur": 0.6, "do": "show", "target": "wire"}, {"at": 6.2, "dur": 0.6, "do": "show", "target": "wire_l"},
              {"at": 6.6, "dur": 3.2, "do": "flow", "target": "wire", "count": 9, "loops": 1, "color": "yellow", "ease": "linear"}]
    for i, (eid, _, _) in enumerate(names):
        steps.append({"at": 6.8 + i * 0.5, "dur": 0.7, "do": "pulse", "target": eid, "amount": 0.12})
    steps.append(capstep)
    return {"version": 1, "title": "Broadcast signal chain", "duration": 11.0, "elements": els, "steps": steps}


TEMPLATES = {"crt-electron-gun-shadow-mask": crt, "lcd-layer-stack": lcd, "rgb-colour-mixing": rgb,
             "broadcast-signal-chain": broadcast}


def main(argv: list[str]) -> int:
    from workers.diagram.render import render_to_file
    from workers.diagram.spec import validate_spec

    out = Path(argv[0] if argv else "reports/tv-explainer/diagram-samples")
    out.mkdir(parents=True, exist_ok=True)
    for name, build in TEMPLATES.items():
        spec = build()
        errs = validate_spec(spec)
        if errs:
            print(name, "INVALID", errs)
            return 1
        (out / f"{name}.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        render_to_file(spec, out / f"{name}.mp4")
        print("rendered", name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
