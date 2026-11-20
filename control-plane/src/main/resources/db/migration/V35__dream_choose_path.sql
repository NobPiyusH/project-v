-- Dream Shorts, "Choose the path" (owner's growth pick, 20 Nov 2026). Per-Short setting, default OFF (existing Shorts unchanged).
--  path_choice:  the writer writes two short path options and the Finish burns an end card over the last ~2 s
--                ("which way?" with an arrow per option). path_a / path_b are the options; path_locked = the owner edited
--                them, so a rewrite by the writer never replaces them.
ALTER TABLE dream_short ADD COLUMN path_choice boolean NOT NULL DEFAULT false;
ALTER TABLE dream_short ADD COLUMN path_a text;
ALTER TABLE dream_short ADD COLUMN path_b text;
ALTER TABLE dream_short ADD COLUMN path_locked boolean NOT NULL DEFAULT false;
--  Branch memory: the episode chain. parent_id = the Short this one continues (null = episode 1); chosen_path = the
--  parent's option that was picked; pick_source = who picked it ('owner' now, 'comments' when the YouTube vote lands).
--  start_clip_asset_id = the parent's last clip: shot 1 of this Short starts from its last frame (chained-shots machinery).
--  scene_seed = the world's seed, inherited down the chain (null = derived from the Short's id, as before).
ALTER TABLE dream_short ADD COLUMN parent_id uuid REFERENCES dream_short (id);
ALTER TABLE dream_short ADD COLUMN episode int NOT NULL DEFAULT 1;
ALTER TABLE dream_short ADD COLUMN chosen_path text;
ALTER TABLE dream_short ADD COLUMN pick_source text;
ALTER TABLE dream_short ADD COLUMN start_clip_asset_id uuid REFERENCES asset (id);
ALTER TABLE dream_short ADD COLUMN scene_seed bigint;
--  branch_json: what a continuation needs, saved when a path-choice Short finishes: world, palette, light, hero, the last
--  shot's scene, seed / locked-scene info, the last clip's asset id and the two paths.
ALTER TABLE dream_short ADD COLUMN branch_json jsonb;
CREATE INDEX dream_short_parent_idx ON dream_short (parent_id);
