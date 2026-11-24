-- M2c part 2a: smoothed (RIFE 24 fps) version of a shot's chosen clip. derived_from links it to the exact clip it was
-- made from, so the edit only uses it while that clip is still the one in use.
ALTER TABLE shot ADD COLUMN smooth_clip_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
ALTER TABLE asset ADD COLUMN derived_from uuid REFERENCES asset (id) ON DELETE SET NULL;

INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a006', 'Practical-RIFE v4.25 (MIT)', true, false,
  'https://github.com/hzwer/Practical-RIFE', 'Frame interpolation run on Modal; output derives from the source clip''s licence');
