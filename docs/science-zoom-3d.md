# Science zoom (3D, Blender): Moon → Earth → Sun → Milky Way, one continuous pull-back

A reusable Blender set that renders a single cut-free camera pull-back across four orders of magnitude, with real orbital
motion and an accelerating clock. Vertical 2160x3840, 30 fps, 900 frames (30 s) by default; everything is driven by a JSON plan.

Files
- `tools/blender/science_zoom.py` builds the scene, bakes every animation channel as keyframes, saves a `.blend`, optionally renders stills / video.
- `tools/blender/science_zoom_plan.json` the plan: layer sizes, orbits and periods, zoom curve, focus, camera elevation, time-scale curve, on-screen labels.
- `tools/blender/science_zoom_assets.py` generates the procedural galaxy disc texture (`galaxy_disc_4096.exr`, numpy, deterministic, no download).
- Library (outside the repo): `E:\project-data\blender\library\science-zoom\` (`science_zoom.blend`, `textures/`, `LICENSES.md`).

## Run it

```powershell
$B = "E:\Blender\blender.exe"
# 1. once: procedural galaxy texture
& $B -b --factory-startup -P tools/blender/science_zoom_assets.py -- --out E:/project-data/blender/library/science-zoom/textures
# 2. build + 6 look-test stills at low res
& $B -b --factory-startup -P tools/blender/science_zoom.py -- --plan tools/blender/science_zoom_plan.json `
   --out E:/project-data/blender/library/science-zoom/science_zoom.blend --res 540x960 --samples 32 `
   --stills 1,150,300,450,640,900 --stills-dir E:/project-data/outputs/science-zoom-3d/stills
# 3. preview video
& $B -b --factory-startup -P tools/blender/science_zoom.py -- --plan tools/blender/science_zoom_plan.json `
   --out E:/project-data/blender/library/science-zoom/science_zoom_preview.blend --res 480x854 --samples 12 `
   --video E:/project-data/outputs/science-zoom-3d/preview_480x854.mp4
# 4. final / cloud: build with --pack (textures embedded, relocatable), then render frame ranges from the .blend
& $B -b --factory-startup -P tools/blender/science_zoom.py -- --plan tools/blender/science_zoom_plan.json --out science_zoom_4k.blend --pack
& $B -b science_zoom_4k.blend -E BLENDER_EEVEE -s 1 -e 300 -a     # chunked + resumable (use a PNG output and skip existing frames)
```

Textures: Earth colour and night lights are NASA Blue Marble / Black Marble (reused from the milky-seas library, licence note in `textures/LICENSES.md`).
Moon, Sun, clouds, atmosphere, stars: procedural shaders. Milky Way band behind the Earth: NASA SVS Deep Star Maps, downscaled to 4k.

## How the scale swap works (no seam)

Real sizes span ~1e9, so the scene is not built at true scale and the camera does not travel through it.

1. Three layers, in "Earth radius" units: **A** Earth-Moon (1 A), **B** Sun-Earth (1 B = 30 A), **C** galaxy (1 C = 840 A).
   Sizes inside a layer are compressed (Moon orbit 7 Earth radii instead of 60, Sun 2.2 B across the orbit…) — hence the on-screen "NOT TO SCALE".
2. Each frame the script solves absolute positions in **double precision**: Moon around Earth, Earth (and Mercury, Venus, Mars) around the Sun,
   Sun around the galactic centre, from a simulated clock.
3. The zoom curve gives the frame half-width `H(t)` (monotone cubic in log space, so the pull-back never stalls or jerks) and a focus point `F(t)`
   that glides Earth → Sun → galactic centre. Every body is placed at `p = (P − F) · g` with `g = HW / H`.
   Equivalent to a camera that scales exponentially, but all numbers stay near unit size, so there is no float-precision or clipping problem.
4. A body is never smaller than a minimum on-screen dot; sub-systems fade exactly when their parent orbit takes over:
   a layer's orbit ring fades in when its screen radius passes 3–10 % of the frame, and the bodies of the layer below fade out by the same rule, the
   galaxy disc fades in over `H = 7 000 … 24 000` A, and the Milky Way background band (the view from inside) fades out over the same range.
5. All of it is baked as one keyframe per frame per channel (constant interpolation), so the `.blend` renders with no script at all.

Clock (plan `time_scale_days_per_s`): 1 s = 1 day → 1 month → 1 year → 10 million years, interpolated in log space and integrated into the clock,
so orbital phases are continuous across the speed-ups. The on-screen label cross-fades between the four named rates.

## Reusing / changing

- Different pacing: edit `zoom`, `focus`, `time_scale_days_per_s`, `labels` in the plan.
- A fifth layer (e.g. local group): add a unit to `levels`, a body block, and zoom keys; the generic helpers (`ring_update`, min-size dot, fade by screen radius) already do the rest.
- Resolution/samples are CLI flags, the plan default is 2160x3840 at 64 samples.
- Gotchas learned: shells (clouds/atmosphere/Sun/halo) must have `visible_shadow = False` or they black out the planet; dithered transparency is noisy at low
  samples (use BLENDED); EEVEE point lights at 400+ units did not light the Earth, so the Sun light is a Sun-type lamp aimed from the Sun (no inverse-square falloff across the system).
