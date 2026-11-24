# 2-minute 4K game cinematic: plan (CTO, 24 Nov 2026)

Owner brief: 4K, game-level, 2 minutes, medium speed (not slow), high fps, high quality, SVI + real motion. Ready when he is back.
All costs are ESTIMATES from measured runs on 24 Nov (RTX 4090 at $0.89/h; SVI beat about 3.5 min; boot about 7 min).

## What went wrong in the 35 s test, and the fix
| Problem seen | Fix in this plan |
|---|---|
| 8 beats chained, second half: camera nearly static, creature melted into smoke | max 3 SVI beats per scene (bench says content drifts after 5); every scene gets its OWN photoreal keyframe |
| Beat joins jumped (SSIM 0.43-0.76) | joins only at scene cuts (planned match cuts, like a game trailer), cross-fade inside a scene |
| Loud teal and orange, game-art look | prompts: "natural photographic grade, overcast, practical lights"; grade in post, saturation 0.8 |
| 720p soft at 2K/4K | Real-ESRGAN x4 on the pod (GPU), not Lanczos |
| 16 fps | RIFE on the laptop: 16 -> 48 fps (x3 with 1.25x speed up = about 60 fps feel) |
| slow motion feel | playback 1.25x; camera verbs in every prompt (push 5 m, crane 8 m, whip pan) |

## Structure: 8 scenes x 15 s (3 beats of 5 s) = 120 s of footage; with 1.25x speed = 96 s, so shoot 10 scenes (150 s) to land on 2:00
Scene list (one keyframe each, same ruined-city world, same creature design):
1. Wide dusk skyline push-in, creature distant
2. Street level, flooded avenue, push forward, embers
3. Creature rises behind towers, backlit
4. Overhead pass, camera tilts up, shadow sweeps the road
5. Collapsed overpass, steam, handheld move through
6. Crane up over wreckage, city opens
7. Creature lands on tower, debris and dust
8. Close low shot: water, rubble, embers, wing shadow
9. Creature launches, shockwave across water
10. Rise into storm sky, silhouette, end card

## Pipeline
1. Keyframes: 10 x Seedream v4 (photographic wording), view all, redo bad ones. About $0.30.
2. SVI on 2 parallel RTX 4090 pods (same code as 24 Nov: `E:\project-data\outputs\game-motion-gate\svi_rent.py`, `svi_run.py`; each pod 5 scenes x 3 beats). Lease + kill-timer each.
3. ESRGAN x4 on the pod after generation (about 2,400 frames), pull 4K-ready frames.
4. Laptop: RIFE to 48 fps, speed 1.25x, grade, grain, 2160x3840 H.265. Sound: free stock + effects.
5. Check gates: keyframes viewed before GPU; first scene viewed before the other nine run.

## Cost and time (estimates)
| Item | Cost | Time |
|---|---|---|
| Keyframes | $0.30 | 10 min |
| SVI generation, 30 beats, +25% retries | $1.9-2.6 | about 1.4 h on 2 pods in parallel |
| ESRGAN 4K | $0.8-1.2 | about 0.8 h |
| Laptop RIFE, encode | $0 | about 1.5 h |
| **Total** | **about $3.5-4.5 (Rs 330-420)** | **about 4.5 h end to end, mostly unattended** |
Cap to ask: $5.00.
