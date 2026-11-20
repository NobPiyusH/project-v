# Dream Shorts: "Choose the path" (points 1 and 2)

Owner's growth pick (20 Nov 2026): viewers steer the dream. Each Short ends on a two-way choice; the chosen path becomes
the next Short, continuing the same world. This builds the end card and the branch memory + manual "Continue this dream".
YouTube publishing and comment voting (point 3) come later.

**Off by default.** Per Short: tick **Choose the path** in the new-Short form (API: `"pathChoice": true`). Shorts without
it are unchanged: the writer prompt, the finish and the clips are byte-for-byte what they were.

## 1. End-card choice

- **Write**: the writer is also asked for `"paths"`: two 2-4 word options for where the camera could go next from the
  last shot (e.g. "the door" / "the stairs"). Cleaned (quotes, arrows, markup removed; at most 4 words; two different
  ones) and stored in `dream_short.path_a / path_b`.
- **Owner edits**: the **Choose the path** card on the Short's page (review or manual), or `POST /api/dream/{id}/paths
  {"a": "...", "b": "..."}`. An edit sets `path_locked`, so a rewrite never replaces it. It shows at the next finish
  (Redo from Finish is free).
- **Finish**: an end card over the last ~2 s, drawn by the hook title's renderer (`workers/dream/hook.py`,
  `end_card_ass`): same font, outline and safe margins; "WHICH WAY?" with the big word in the hook colour, then
  `← A: THE DOOR` and `B: THE STAIRS →`, and a small "COMMENT A OR B" (A/B labels make comment votes easy to count later).
  A path-choice Short keeps its shot order (pacing never moves the last shot), because the card asks about the last shot.

## 2. Branch memory + Continue

- When a path-choice Short finishes, `dream_short.branch_json` stores: title, world, palette, light, hero, camera path,
  the last shot's scene, the last clip's asset id, the world seed, look and chain/locked-scene settings, both paths
  and the episode number.
- **Continue this dream**: the buttons on a finished path-choice Short ("A: the door" / "B: the stairs"), or
  `POST /api/dream/{id}/continue {"path": "A"}`. It creates a new Short (the next episode) that:
  - links to its parent (`parent_id`, `episode` = parent + 1, `chosen_path`, `pick_source`);
  - inherits the parent's settings (look, length, quality, modes, editing, hook, chain options; path choice stays on;
    the Scout is skipped) and the world seed (`scene_seed`);
  - gives the writer the parent's branch memory and the chosen path ("Shot 1 begins exactly on the previous episode's
    last frame...");
  - starts shot 1 from the **last frame of the parent's last clip** (`start_clip_asset_id`): shot 1 needs no picture, its
    clip job carries `chainFromUri` = that clip, and the worker's `start_image` takes its last frame (same code as
    chained shots). The raw clip is used, not the finished Short, so the end card never leaks into episode 2.
- The dashboard shows the chain (Episode 1 · 2 · 3, with the path picked) on each episode's page, an "Ep N" pill in the
  list, and links to the episodes that continue a Short. `GET /api/dream/{id}/chain` returns it.
- Continuing does not spend by itself: the new Short starts in Review mode for any step the parent did not set, and its
  paid steps follow the usual cost approvals.

## Plugging in the comment vote (point 3, later)

Picking is a `DreamBranches.PathPick(path, source, votes)`. The manual button sends `PathPick.owner("A")`. The vote
counter only has to build `new PathPick("B", DreamBranches.COMMENTS, Map.of("A", 12, "B", 30))` and call
`DreamStudio.continueDream(parentId, pick)`; the source is stored in `pick_source` and the votes in the Short's event log.

## Data

Migration `V35__dream_choose_path.sql`: `path_choice`, `path_a`, `path_b`, `path_locked`, `parent_id`, `episode`,
`chosen_path`, `pick_source`, `start_clip_asset_id`, `scene_seed`, `branch_json` on `dream_short`.

## Tests

- Java: `DreamBranchesTest` (option clean-up, the pick, the writer's continuation text), `DreamChoosePathTest`
  (Testcontainers, fake writer: off by default, paths written and owner edits kept, finish request carries the card,
  branch memory, episode 2 from the parent's last clip with the same seed, episode 3 picked by "comments", the chain).
- Python: `workers/tests/test_dream_path_choice.py` (the card's .ass, a real FFmpeg finish with the card only at the end
  and together with the hook title, shot 1 starting from the previous episode's last frame).
