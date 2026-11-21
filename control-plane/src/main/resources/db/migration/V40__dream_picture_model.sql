-- Dream Shorts: the picture model on the rented pod, per Short (owner, 21 Nov 2026: FLUX.2 klein is the default after a
-- blind A/B against Z-Image and Nano Banana; Z-Image stays selectable). Older Shorts were made with Z-Image.
ALTER TABLE dream_short ADD COLUMN picture_model text NOT NULL DEFAULT 'zimage' CHECK (picture_model IN ('zimage', 'flux2-klein'));

INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a013', 'FLUX.2 klein 4B (Apache-2.0)', true, false,
  'https://huggingface.co/black-forest-labs/FLUX.2-klein-4B',
  'Open picture model run on the studio''s rented GPU pod; output is the studio''s own render');
