# Quality gate (`tools/qualitygate/`)

Tiered checks on a finished video. Tiers 1 and 2 are free (ffmpeg only, no GPU, no network). Tier 3 is a VLM judge that is **off by default** and has no model SDK inside; the caller injects the model call.

```powershell
uv run python -m tools.qualitygate <video> [--shots shots.json] [--captions file.srt] [--judge off] [--json-only]
```

Prints a short report, then the JSON. Exit code 1 if any check fails. `shots.json` is `[{"id": "s1", "start": 0, "end": 4.5}, ...]`; without it the whole video is one shot. A silent clip skips the loudness check (status `skip`).

| Tier | Check | How | Fails when |
|---|---|---|---|
| 1 | Loudness | `ebur128` | more than 9 LU from -14 LUFS (warn at 3 LU), or true peak above 0 dBTP |
| 1 | Black frames | `blackdetect` | a black run of 1 s or more away from the start/end fades (shorter warns) |
| 1 | Frozen frames | `freezedetect` | a frozen run of 2 s or more (shorter warns) |
| 1 | Captions | SRT parse | a cue outside the video, or overlapping; warns on cues under 0.7 s or over 7 s; reading speed over 20 cps warns, over 25 fails |
| 2 | Motion | mean luma difference between frames, per shot | below 0.25 (warn below 0.6): near-static shot |
| 2 | Independent motion | motion left after removing one global camera move (pan/zoom/rotate fit); needs numpy, else `skip` | below 1.5 (warn below 4.5): a slid or zoomed still, or a static shot |
| 2 | Colour drift | largest chroma (U/V mean) excursion from the shot's own 17-frame average | warn above 0.9 (never fails) |
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

## Calibration (25 Nov 2026, Act 1 clips on E:, read-only)

Good = `act1_cut_v3` (22 auto-detected shots), `camera_scene_720p`, real moving beats. Bad = panned/zoomed stills made from a keyframe, near-static scenes, scene 1's blue-wing moment.

| Clip | motion | indep. motion | colour drift | loudness | Verdict |
|---|---|---|---|---|---|
| act1_cut_v3 (22 shots) | 3.5-9.3 | 3.1-35.6; three shots 3.1, 4.1, 4.2 warn | not measured on the whole cut | -21.1 LUFS (warn) | pass, 3 shots to look at |
| camera_scene_720p | 5.4 | 10.2 | 0.8 | silent | pass |
| s03, s06 beats (real motion) | 4.9-8.9 | 7.0-10.5 | 0.3 | n/a | pass |
| s02, s05 beats (near-static?) | 0.9-1.3 | 2.7-3.3 | 0.1-0.2 | n/a | independent motion warns |
| panned still (synthetic) | 4.2 | 0.9 | n/a | n/a | fails |
| zoomed still (synthetic) | 3.4 | 3.4 | n/a | n/a | warns |
| s01_b2, s01_b3 (blue wing scene) | 3.2-3.9 | 8.1 | 1.0-1.1 | n/a | colour drift warns |

Separation: the plain `motion` score does not separate stills from real shots (panned still 4.2 vs real near-static 0.9-1.3). Independent motion does for panned stills (0.9 vs at least 2.7) and flags the near-static beats as warnings; a zoomed still (3.4) overlaps the near-static real beats (2.7-3.3), so warn means "look at it". Colour drift separates scene 1's beats from the others only weakly (1.0-1.1 vs 0.1-0.5, camera_scene 0.8): treat it as a hint, the VLM judge is the real colour check. Sample size is small and the exact blue-wing window (0.6-2.7 s) was not isolated per frame.

## OpenAI judge backend (built, never run live)

`tools/qualitygate/openai_judge.py`. CLI: `--judge openai --max-usd X` prints the worst-case estimate and exits 3; add `--confirm-spend` to make paid calls. The cap is mandatory, checked before every call against the worst case, and recorded per call in the shared gate ledger (tokens and USD are logged). The key comes from .env lazily and is never logged. Tested with fakes only; no live call has been made.

### Judge tuning trial (25 Nov 2026, 7 calls, about $0.039 / Rs 3.5)

Prompt v2 (ordered checks: colour of parts, shape, real motion with first/last frame comparison, no grain/soft-focus reports; 8 frames spread first-to-last plus a frame at a flagged moment). Results on Act 1 beats: the judge did NOT flag the blue wing in s01_b2 or s01_b3 (accepted at 8.7-9.0, even with a hint frame; the hint pointed at 0.25 s because the chroma peak sits at the shot start, while the wing turns blue around 0.8-1.2 s). It called s03_b1, s02_b1 and s06_b1 "near-static" (major), but s03 and s06 have real camera/subject motion by `independent_motion` (7.0 and 10.5), so its static call is unreliable. Policy suggestion: trust `independent_motion` for static, use the judge for shape/colour, and do not rely on it for the blue wing until frames are placed by a better colour-change detector (per-region, not whole-frame mean). Not wired into `gate.run_gate`: the chroma pass (`tier2.chroma_args`) is used only by the trial script.

## Pipeline hook (26 Nov 2026)

Tiers 1-2 (judge OFF, free) now run automatically after the edit. Flag: `QUALITY_GATE_ENABLED` in `.env` (default on; `0`/`false`/`off`/`no` disables). The paid judge is never started by the hook.

- **Studio ASSEMBLY stage**: after `save_master`, the media worker's `quality_gate_check` activity (`workers/activities/gate_check.py`) downloads the master, runs `tools/qualitygate/hook.py`, and stores `videos/<id>/master/quality-gate.json` next to `master.mp4` (tiers, judge off, thresholds used, per-check results). A **fail** raises a non-retryable error, so the stage waits for the owner like any failed stage (rule 2b): Reject = redo the edit, Approve = continue without it. **Warns** are added to the stage summary and never block. A crash of the checker itself (ffmpeg missing) returns status `error`, logs, and does not block.
- **Film CLI** `tools/film4k/edit.py`: after the render it writes `<name>.quality-gate.json` next to the output, prints the summary, and exits **3** on a fail ("HOLD", output kept). `--no-gate` skips it.

## Golden regression tests (25 Nov 2026)
`tools/golden/tests/test_golden_qualitygate.py` makes tiny clips with ffmpeg lavfi (a flat-colour clip, a `testsrc2` moving clip, and moving clips with silent, quiet, mid and loud audio), runs the gate and compares the per-check status and rounded value with the JSON in `tools/golden/fixtures/qg_*.json`. Hard asserts also hold regardless of the fixture: the static clip fails motion and frozen_frames, the moving clip passes both, the quiet clip fails loudness. Numbers compare with a small tolerance (`tools/golden/golden.py`); statuses compare exactly.

A deliberate change (a threshold in `config.py`, a new check) is one command, then read the diff before committing:

```powershell
$env:GOLDEN_UPDATE = "1"; uv run --group dev --group studio python -m pytest tools/golden -q; Remove-Item Env:GOLDEN_UPDATE
git diff tools/golden/fixtures
```

`--update-golden` does the same. Skipped when ffmpeg is not installed. The fixtures pin the synthetic clips only, not real-film calibration (see Calibration above).
