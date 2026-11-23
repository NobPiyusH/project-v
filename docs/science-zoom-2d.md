# Science zoom (2D, Manim)

A 30 s, one-take, vertical zoom-out: Moon around Earth, Earth around the Sun, Sun around the Milky Way. Plan-driven
(one JSON file read by one script), procedural (no downloaded art, no LaTeX), CPU only, costs nothing.

Code: `tools/science_zoom/` (`zoom.py` scene and CLI, `galaxy.py` texture generator, `plans/*.json`).

## Install (done once; project-local, nothing on C: beyond this repo)

The environment lives on E: so the repo's main `uv` env (torch etc.) is not touched.

```powershell
$env:UV_CACHE_DIR = "E:\project-data\uv-cache"
mkdir E:\project-data\tools\science-zoom
cd E:\project-data\tools\science-zoom
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe manim     # Manim Community 0.22.1, Cairo renderer
```

FFmpeg must be on PATH (it is). Manim uses Pango for `Text`; the font is Segoe UI (ships with Windows).

## Run

```powershell
$py = "E:\project-data\tools\science-zoom\.venv\Scripts\python.exe"
cd tools\science_zoom
& $py zoom.py --plan plans\moon_earth_milkyway.json --report                                   # timeline numbers only
& $py zoom.py --plan plans\moon_earth_milkyway.json --out E:\project-data\outputs\science-zoom-2d\work --scale 0.444 --stills 0,5,10,15,20,25,29.9
& $py zoom.py --plan plans\moon_earth_milkyway.json --out E:\project-data\outputs\science-zoom-2d\work --scale 0.444 --name test480
& $py zoom.py --plan plans\moon_earth_milkyway.json --out E:\project-data\outputs\science-zoom-2d\work --scale 1 --name final1080
& $py zoom.py --plan plans\moon_earth_milkyway.json --out ... --scale 2 --name final2160       # 4K, about 4x the time
```

Always render stills first and look at them before a long render.

## How the plan works

`plans/moon_earth_milkyway.json`:

- `video`: width, height, fps, duration.
- `zoom`: the first stage's orbit starts at `first_orbit_screen_radius` units, the last stage's orbit ends at
  `last_orbit_screen_radius`. From that the script derives one constant zoom rate in powers of ten per second
  (`--report` prints it). The view width is `W0 * 10^(k t)`, so every second is the same power of ten. No cuts.
- `stages` (exactly three nested systems: inner body around centre, centre around next centre, last around the galactic centre):
  `orbit_radius_m` is the **display** radius, `true_orbit_radius_m` is the real one (reference only), `period_days`,
  `phase0`, `rate_days_per_s` (the clock speed while this stage is the focus), `label` (on-screen text), `name`,
  `fade_in_radius` / `fade_out_radius` (screen radii in units at which the layer fades in and out; frame is 4.5 x 8
  units) and `handover` (screen radius range during which the camera slides to this stage's centre).
- The simulated clock is integrated from the stage rates, blended smoothly in log space as the camera hands over,
  so the speed-up is continuous. The on-screen time label cross-fades with the focus.

Honesty note: the true gaps (Moon to Earth orbit 390x, Earth orbit to the Sun's galactic orbit 1.6 billion x) would
leave most of 30 s empty, so display radii are compressed to 30x steps. The "NOT TO SCALE" note says so.

## Look

Dark space (`#02040a`), two star layers (fixed backdrop and world-anchored stars that stream inward), lit spheres
built from stacked shifted circles with the real Sun direction, Sun and atmosphere glow from stacked low-opacity
circles, a generated spiral-galaxy texture (`galaxy.py`) cropped to the visible window each frame.
