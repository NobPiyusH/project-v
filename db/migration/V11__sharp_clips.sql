-- M2c part 2b: sharpened (SeedVR2 1080p) version of a shot's clip. Its asset's derived_from is the exact clip it was
-- made from (the smoothed clip, else the chosen clip), so the edit only uses it while that clip is still in use.
ALTER TABLE shot ADD COLUMN sharp_clip_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;

INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a007', 'SeedVR2 (Apache-2.0)', true, false,
  'https://github.com/ByteDance-Seed/SeedVR', 'Video restoration/upscaling run on Modal (numz CLI + numz/SeedVR2_comfyUI weights, Apache-2.0); output derives from the source clip''s licence');
