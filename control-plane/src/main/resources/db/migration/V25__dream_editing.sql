-- Dream Shorts editing rules (owner-approved 19 Nov 2026), each switchable per Short, default on:
-- edit_music = cuts on bar lines, climax on the music's rise, speed follows energy; edit_ambience = J-cut ambience;
-- edit_pacing = hook first, no shot under 3.4 s, climax at 38-68%.
ALTER TABLE dream_short ADD COLUMN edit_music boolean NOT NULL DEFAULT true;
ALTER TABLE dream_short ADD COLUMN edit_ambience boolean NOT NULL DEFAULT true;
ALTER TABLE dream_short ADD COLUMN edit_pacing boolean NOT NULL DEFAULT true;
