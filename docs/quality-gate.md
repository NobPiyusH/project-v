# Quality gate (`tools/qualitygate/`)

Tiered checks on a finished video. Tiers 1 and 2 are free (ffmpeg only, no GPU, no network). Tier 3 is a VLM judge that is **off by default** and has no model SDK inside; the caller injects the model call.

```powershell
uv run python -m tools.qualitygate <video> [--shots shots.json] [--captions file.srt] [--judge off] [--json-only]
```

Prints a short report, then the JSON. Exit code 1 if any check fails. `shots.json` is `[{"id": "s1", "start": 0, "end": 4.5}, ...]`; without it the whole video is one shot. A silent clip skips the loudness check (status `skip`).

| Tier | Check | How | Fails when |
|---|---|---|---|
| 1 | Loudness | `ebur128` | more than 6 LU from -14 LUFS (warn at 2 LU), or true peak above 0 dBTP |
| 1 | Black frames | `blackdetect` | a black run of 1 s or more away from the start/end fades (shorter warns) |
| 1 | Frozen frames | `freezedetect` | a frozen run of 2 s or more (shorter warns) |
| 1 | Captions | SRT parse | a cue outside the video, or overlapping; warns on cues under 0.7 s or over 7 s; reading speed over 20 cps warns, over 25 fails |
| 2 | Motion | mean luma difference between frames, per shot | below 0.25 (warn below 0.6): near-static shot |
| 2 | Independent motion | motion left after removing one global camera move (pan/zoom/rotate fit); needs numpy, else `skip` | below 1.5 (warn below 4.5): a slid or zoomed still, or a static shot |
| 2 | Sharpness | mean edge (Laplacian) response at 640 px wide | below 0.8 (warn below 1.6) |
| 2 | Flicker | mean absolute second difference of per-frame mean luma | above 4.0 (warn above 1.5) |

All numbers live in `tools/qualitygate/config.py` (`THRESHOLDS`, documented line by line). Each result is a `CheckResult` (status pass/warn/fail/skip, value, unit, `where` spans in seconds, shot id).

Panned stills: the plain frame-difference `motion` score cannot catch them (a panned still scored 4.2, higher than real gentle shots at 0.9-1.9). `independent_motion` (`localmotion.py`) does: it fits a single affine camera move between frames half a second apart, removes it, and measures what is left. Calibration on 25 Nov 2026 (few clips, owner's verdict not yet applied): panned still 0.9, zoomed still 3.4, real moving beats 5.5-10.8, and Act 1 beats of scenes 2 and 5 about 2.7-3.3 (they warn as near-static). Real parallax from a forward camera move counts as motion, but a slow zoom of a flat image can land in the warn band, so treat warn as "look at it". Tier 3's `composition` criterion is the second opinion.

Shots: `--shots auto` splits the video at ffmpeg scene cuts (`scene_threshold` 0.4, shots shorter than 0.5 s are merged).

## Tier 3: judge

`tier3.judge_shot(video, shot, judge, repairs_done)` samples `frames_per_shot` frames (slice midpoints), builds one prompt with the weighted rubric (subject consistency 0.35, artifacts 0.30, composition 0.20, colour drift 0.15), calls `judge(prompt) -> str`, parses the JSON reply and decides:

- **accept**: weighted score at least 7 and no `major` defect.
- **repair**: each defect becomes a `RepairPlan`. A boxed defect that `repair.choose_mode` accepts (at most 4 s, at most 45% of the frame) becomes a `vace_inpaint` `Region`; an unboxed, long, huge or invalid defect, a score under 4, or a weak score with no localised defect becomes a whole-shot `reroll`. A reroll replaces local patches.
- **fail**: two repairs already spent (`max_repairs_per_shot`), or the reply could not be parsed. Fail means the owner decides (Retry / Continue without it); the gate never auto-accepts an unreadable reply.

Pass `judge=` to `gate.run_gate(...)` to enable it. The CLI's `--judge on` exits with an error because no real model backend is wired yet. Tests use a fake judge and a fake frame extractor.

## Tests

`uv run --group dev python -m pytest tools/qualitygate`. Parsers and decisions are tested on canned text; one module builds a tiny clip with ffmpeg itself and is skipped if ffmpeg is missing.
