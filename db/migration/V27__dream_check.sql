-- Dream Shorts quality check (learning loop, phase 3): Gemini's report on the pictures (before clips are paid
-- for) and on the finished Short.
ALTER TABLE dream_short ADD COLUMN check_json jsonb;
ALTER TABLE dream_short ADD COLUMN picture_check_json jsonb;
