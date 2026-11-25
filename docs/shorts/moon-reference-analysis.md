# Moon Short: measurements of the five reference Shorts (25 Nov 2026)

Study only. These are numbers, not a description of the footage, and nothing from the clips is reused. The files are the owner's, in
`E:\project-data\reference\moon-shorts\` (all 1080x1920 H.264 with an audio track). Measured with ffmpeg 9.0.2 and numpy, free, on the laptop.

## Method (so the numbers can be repeated)

- **Cuts**: ffmpeg `select='gt(scene,0.25)'`. **Frame-difference spikes** (a frame whose mean absolute luma difference is above 3x the clip median and above 12 of 255) are listed separately: they are brief changes, not counted as cuts.
- **Global camera motion**: frames decoded at 10 fps, 108x192 grey; phase correlation between consecutive frames gives the dominant translation. Reported as the median (and 90th percentile) in **% of frame width per second**, spike frames excluded. It sees pans and handheld shake; it does NOT see a zoom or a growing object (a pure zoom reads as about 0).
- **Frame difference**: median mean-absolute luma difference between consecutive 10 fps frames (0-255 scale): all motion in the picture (camera, object, waves, people, grass).
- **Object size (proxy)**: share of pixels with luma >= 170 in the first second versus the last second. The big object is the brightest thing, but a threshold also catches sky, flares and exposure change, so treat it as a weak hint, not a size.
- **Audio**: ffmpeg `ebur128` (integrated loudness, loudness range, true peak).

## Results

| Clip (id prefix) | Length s | Cuts (s) | Spikes (s, not cuts) | Frame diff (median, /255) | Global motion %w/s (median / p90) | Bright share first s -> last s | Audio: LUFS / LRA / true peak |
|---|---|---|---|---|---|---|---|
| 1c5dc159 | 30.2 | 2 (16.3, 21.5) | 3.4, 10.4, 16.2, 21.4, 25.0 | 3.8 | 9.3 / 18.5 | 3.1% -> 8.0% (x1.6 linear) | -22.4 / 4.8 / -0.9 dBTP |
| 9b1a4acb | 9.2 | 0 | 4.1-4.4 | 5.0 | 4.6 / 18.5 | 14.7% -> 15.1% (flat) | -13.5 / 1.7 / -2.1 |
| ae4a2aaa | 13.4 | 0 | none | 2.4 | 0.0 / 0.0 | 1.2% -> 1.3% (flat) | -14.2 / 0.9 / -1.2 |
| f9aa8654 | 9.8 | 0 (0.03 s is the first frame) | 0.0-0.3 | 4.6 | 0.0 / 9.3 | 15.4% -> 8.8% (falls) | -14.1 / 2.7 / -2.6 |
| ff63eaad | 10.2 | 0 | none | 1.8 | 0.0 / 0.0 | 6.3% -> 4.0% (falls) | -14.2 / 5.4 / -1.7 |

Audio is present in all five. Four of five sit at -13.5 to -14.2 LUFS with true peak -1.2 to -2.6 dBTP (the usual platform-normalised level); the 30 s clip is quieter (-22.4).

## What it implies for our plan

1. **One shot is the norm**: 4 of 5 clips have no cut at all; the one long clip (30 s) has 2 in 30 s. A 10 s Short as one continuous shot is inside the reference range; a 3-beat plan with a hard cut or a different camera move per beat is not.
2. **Camera is nearly still**: median global translation is 0 in 3 of 5 clips and 4.6-9.3 %w/s in the other two (a slow drift, about 5-9 % of the frame width per second at the median). Our tested `slow_push` (Fun Camera, speed 0.5) or a static camera matches; the `pan_left` / `zoom_in` plan did not. Do not ask for a fast move.
3. **The size proxy is not conclusive**: only one clip shows a clear rise (x1.6 linear over 30 s); the other four read flat or lower. This cannot say how fast the object grows in the other four (a threshold proxy is blind to a slow growth of a non-saturated object). The size curve for our layer is therefore a design choice (x2.2 over 10 s, smoothstep), to be judged by eye, not copied from a number.
4. **There is always ambient motion**: even where global motion is 0 the median frame difference is 1.8-5.0 of 255. That is the floor to keep: a Moon scaling over a frozen picture would sit near the low end and may trip the quality gate's `independent_motion` check, so the plate must carry real motion (waves).
5. **Audio is part of the format**: every reference carries sound at about -14 LUFS. Our Short needs narration plus music/ambience mixed to about -14 LUFS, true peak under -1 dBTP.
6. **Length**: 9-13 s for four of five. 10 s fits.
