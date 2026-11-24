-- Dream Shorts, SVI scenes (21 Nov 2026), default OFF. A Short holds 1-3 scenes; a scene is ONE continuous SVI 2.0 Pro
-- shot of 10/15/20 s made of 5 s beats (81 frames each), rendered back to back on the pod from one picture per scene.
--  svi_scenes:        0 = off (separate clips / chains as before); 1-3 = this many scenes
--  svi_scene_seconds: 10 | 15 | 20 (3, 4 or 5 beats); capped at 20 s because content drifts after that (bench)
--  svi_between:       'cut' (default) or 'dissolve' (0.5 s) between scenes
-- Every beat is a dream_shot row (its own motion prompt, clip, take and provenance), tagged with its scene and beat.
ALTER TABLE dream_short ADD COLUMN svi_scenes int NOT NULL DEFAULT 0;
ALTER TABLE dream_short ADD COLUMN svi_scene_seconds int NOT NULL DEFAULT 20;
ALTER TABLE dream_short ADD COLUMN svi_between text NOT NULL DEFAULT 'cut';
ALTER TABLE dream_shot ADD COLUMN scene_no int;   -- 1-based; NULL when the Short is not SVI
ALTER TABLE dream_shot ADD COLUMN beat_no int;    -- 1-based within the scene; beat 1 carries the scene's picture
