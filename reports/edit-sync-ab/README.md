# Edit / music-sync A/B (22 Nov 2026, laptop only, no generation)

Same clips, same music, same order (`keepOrder`), same "lonely" look, 720x1280, 24 fps; only the edit changes.
Each Short is 4 raw Wan clips (16 fps, ~5 s) from `reports/sailing-short/raw` scored with an existing track.
Ambience off (needs Freesound). Re-encoded at CRF 25 to stay small. Rebuild: `uv run python -m workers.dream.edit_ab`
(see `workers/dream/edit_ab.py`). Numbers are in `<short>-metrics.json`.

| Short | Clips | Music |
|---|---|---|
| sailing-a | s1_clip1-4 | music_lyria |
| sailing-b | s2_clip1-4 | music_horror |
| sailing-c | s3_clip1-4 | music_nostalgia |

**before** = today's finish (hard-coded defaults). **after** = all five polish flags on:

- `beatSnap`: cuts land on a reachable bar line, else a beat; a shot may be slowed up to 6% to reach it instead of only
  being trimmed. Result: shots keep more of themselves (shortest shot 4.19 -> 4.35 s, 4.34 -> 4.96 s, 4.47 -> 4.67 s).
  Cut-to-beat error stays 0.00 s (today's trim-only sync was already on the beat); cut-to-bar error is not better in
  every Short (a 0.0 -> 0.67 s regression in sailing-b, 0.57 -> 0.19 s better in sailing-c). Honest read: little gain here.
- `smartTransitions`: these clips continue each other (frame difference at every cut is small), so each 0.5 s
  dissolve became a quick 0.12 s blend. Shorts run ~2-3 s longer because less time is spent overlapping.
- `hqInterp`: finer motion interpolation (small blocks, bidirectional search, scene-change guard). Used only where the
  laptop RIFE is unavailable (it was not here, so FFmpeg minterpolate ran in both; this is the visible difference to judge).
- `twoPassLoudness`: measure, then apply linear loudnorm -14 LUFS + a true-peak limiter at -1.5 dBTP.
  Integrated loudness: -13.0 / -13.6 / -14.1 -> -13.9 / -13.9 / -14.1 (before overshoots; the single pass is dynamic).
- `softEndFade`: 2.5 s eased (quarter-sine) music tail instead of a straight 1.5 s ramp.
- Voice ducking (`voiceUri`) is wired but not exercised here: these Shorts have no narration.

Judge by eye and ear: the owner decides whether any flag is switched on (all default OFF).
