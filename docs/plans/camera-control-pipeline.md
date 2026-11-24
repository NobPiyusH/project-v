# Camera control in the pipeline: plan (24 Nov 2026)

Owner: "Yes, plan it" after the pod tests. Nothing here is built or paid for until he approves each step with a figure.

## Why
Wan 2.2 + SVI animates the subject but barely moves the camera; our fix was a post zoom, which the owner judged "slideshow-like" (film Act 1). Real camera
movement has to come from the generator.

## What the 24 Nov tests showed (one Secure 4090, total $0.60 of the $1.00 cap; files in `E:\project-data\outputs\pod-tests\out\`)
| Option | Result (480x832, 33 frames = 2 s, 4 steps, lightx2v) | Time | Verdict |
|---|---|---|---|
| Wan 2.2 Fun Camera, preset Zoom In | real forward move: road and puddle grow, buildings approach, creature stays | 58 s | use |
| Fun Camera, Pan Left | gentle believable sideways move | 51 s | use |
| Fun Camera, Anti Clockwise | roll only (not an orbit); last frame warps | 51 s | rarely |
| Fun VACE + edge video of a push-in path | follows the path closely, scene faithful to the keyframe | 51 s | use for custom paths (Blender later) |
| SeedVR2 3B fp8 vs Real-ESRGAN | slightly crisper, less smear; about 10 min per 5 s beat at 4K vs 3-5.5 min | 250 s / 33 frames | hero shots only |
Not tested: 720p, 81 frames, chaining beats, subject action quality, Pan Right/Up/Down, Zoom Out. Cost at 720p x 81 frames is an ESTIMATE (about 5.7x the pixels-frames of the test: roughly 5 min per beat, about $0.07 on a $0.89/h pod).

## Design
1. **Per shot a camera field** in `tools/film4k/storyboard.json`: `"camera": "zoom_in" | "pan_left" | "pan_right" | "static" | {"vace": "path_name"}` (default zoom_in); the post push-in in `camera.py` stays only as a fallback for plates without camera control.
2. **Generator order per scene:** keyframe (Seedream/FLUX) -> Fun Camera I2V (2 experts fp8, lightx2v I2V 4-step loras) per beat; beat k+1 starts from the LAST FRAME of beat k (the owner preferred last-frame chaining, 20 Nov), same camera preset continued or changed per beat. SVI is not used with Fun Camera (different model); SVI stays for subject-motion scenes.
3. **VACE lane for custom paths:** control video = edge/depth render of a camera path (ffmpeg warp now, Blender depth pass later), reference image = keyframe. Needs the Blender set (separate plan).
4. **Pod boot:** add the Fun Camera experts (2x15.3 GB) and the VACE experts (2x17.3 GB) as optional file groups (boot only fetches the group a job needs); pip installs must not compete with downloads (this run's deps took 16 min because they overlapped the 80 GB download).
5. **Upscale:** keep Real-ESRGAN as the default; SeedVR2 as an opt-in per shot (`"upscale": "seedvr2"`), node pinned to numz/ComfyUI-SeedVR2_VideoUpscaler sha 4490bd1.
6. **Editor:** `tools/film4k/edit.py` already cuts shots and ignores the zoom; with real camera plates the extra punch-in crop is reduced (a flag), so the plate's own move is not doubled.

## Steps and costs (all ESTIMATES until measured; each needs the owner's yes with a figure)
| Step | What | Cost |
|---|---|---|
| S1 (free) | Code: camera field, Fun Camera graph + last-frame chain in `tools/film4k/comfy.py`, boot groups in `tools/podkit/boot.py`, unit tests | $0 |
| S2 | ONE real scene at 720p: 3 beats Fun Camera zoom_in -> pan_left -> zoom_in, last-frame chained, then ESRGAN; owner judges motion by eye | about $0.5 incl. boot (about $0.3 if the pod is Community and starts) |
| S3 | If S2 passes: redo the 3 weakest Act 1 scenes (avenue, tunnel, wall) | about $1-1.5 |
| S4 | VACE with a Blender depth path (after the Blender set exists) | about $0.3 per test |
| S5 | Act 2 scenes 9-15 with camera control, within the owner's cap | about $2.5-3 (owner said no to the $6 cap on 24 Nov: a new figure is needed) |

## Risks
- MEDIUM: 720p/81-frame quality of Fun Camera is unproven; last-frame chaining can drift the creature (mitigation: a fresh keyframe per scene, as now).
- MEDIUM: Community pods in Sweden pulled the 10 GB image for 25+ min and never started (cost $0.14): rent logic must abort a pod with no boot log after 9 min (added to `pod-tests/run.py`, to be ported to podkit).
- LOW: VACE edge control gives little subject motion; combine with a prompt that describes the action.

## Done = 
S2 judged by the owner as clearly better motion than the zoom-only cut, with measured minutes and cost recorded in `docs/film4k.md`.
