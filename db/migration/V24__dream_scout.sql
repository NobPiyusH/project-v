-- Dream Shorts Scout (learning-loop plan, part 2): before writing, find similar Shorts, watch them, and pick a way to
-- be different. scout = run it for this Short; scout_json = what was found and the brief; scout_angle = the angle
-- the owner picked (1-based; Auto keeps 1).
ALTER TABLE dream_short ADD COLUMN scout boolean NOT NULL DEFAULT true;
ALTER TABLE dream_short ADD COLUMN scout_json jsonb;
ALTER TABLE dream_short ADD COLUMN scout_angle int NOT NULL DEFAULT 1;
