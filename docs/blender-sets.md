# Blender sets and anchor frames

A **set** is a reusable 3D location (here: a gothic cathedral) that Blender renders as exact, drift-free
**anchor frames**. Wan 2.2 image-to-video then repaints and animates from those anchors.

## How a set is stored
`E:\project-data\blender\library\<set>\` holds `<set>.blend`, `textures/`, `hdri/`, `LICENSES.md`, `anchors/`, `preview/`.
Media never goes in git. Git holds only the build script (`tools/blender/cathedral_set.py`), the texture fetcher
(`polyhaven_fetch.py`), camera routes (`tools/blender/routes/*.json`) and a copy of the licence list.
The build is rerunnable and deterministic: same arguments give the same `.blend`.

```powershell
# one-time asset fetch (CC0 Poly Haven only)
python tools/blender/polyhaven_fetch.py E:\project-data\blender\library\cathedral tex:church_bricks_02 tex:monastery_stone_floor tex:cobblestone_floor_01 tex:medieval_blocks_05 hdri:moonless_golf
# build the set
E:\Blender\blender.exe -b -P tools/blender/cathedral_set.py -- build --out E:\project-data\blender\library\cathedral\cathedral.blend --hour 22 --fog 0.009 --candles 700 --crowd 260
```

Parameters: `--hour` (time of day, 0-24), `--fog` (volume density), `--candles`, `--crowd`, `--glow` (windows, flames,
light shafts), `--seed`, `--route <json>`. They are saved on the scene (`scene['set_params']`).

## Reusing it for a new video
1. Copy `tools/blender/routes/cathedral_plaza_to_nave.json`, edit `keys` (`t` seconds, `pos` camera, `look` target, metres;
   +Y runs plaza, doors at y=0, nave, rose-window wall at y=48), `duration`, `fps`, `lens`.
2. Rebuild with `--route` (or pass `--route` to `render` to re-key the camera of an existing .blend).
3. Render anchors every ~5 s: `-- render --blend X.blend --outdir D --anchors 0,5,10 --res 1080x1920 --samples 48`.
   Quick preview: `--preview name.mp4 --res 480x854 --samples 12`.

## How anchors feed Wan
Each anchor PNG is the start image (I2V) for the next ~5 s clip. Wan keeps the layout and lighting of the
anchor, and the next anchor (rendered from the same camera path) re-grounds the scene so shots stay long without drift.
Measured on the RTX 3050 4 GB: 2.4-4.5 s per 1080x1920 anchor (48 samples), 0.31 s per 480x854 preview frame.

## Missing before Wan can use them
- Anchor-to-prompt format: a small per-anchor JSON (anchor file, t, camera pos/look, motion text such as "slow dolly
  forward", style/negative prompt, denoise/strength, clip length) and the code that turns it into a Wan I2V job.
- Decide Wan's input size (Wan 2.2 works near 480x832 or 720x1280; anchors are 1080x1920 and need a resize step).
- Stitching: last frame of clip N versus anchor N+1 blend/handoff, and colour match.
- Look test: Wan repaint of a Blender anchor has not been run (no GPU spend allowed in this task).
