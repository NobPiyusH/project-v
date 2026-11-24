-- Dream Shorts: music from the owner's own track (the "music bed") by default; Lyria stays a per-Short option.
ALTER TABLE dream_short ADD COLUMN music_source text NOT NULL DEFAULT 'bed' CHECK (music_source IN ('bed', 'lyria'));

-- Licence for music the owner supplies himself (his file, his rights).
INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a012', 'Owner-supplied music', true, false, NULL,
  'A track the owner gave the studio to use; rights are the owner''s responsibility');
