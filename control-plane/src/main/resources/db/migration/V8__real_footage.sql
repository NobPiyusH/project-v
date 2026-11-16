-- Free real footage (NASA Image and Video Library) chosen by the owner for a shot, instead of a generated clip.
ALTER TABLE shot ADD COLUMN footage_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;

INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a005', 'NASA media (US Government work)', true, false,
  'https://www.nasa.gov/nasa-brand-center/images-and-media/',
  'Generally not copyrighted in the US; no NASA endorsement implied; NASA insignia not used as a logo. Credit "NASA" (and the listed centre/creators) is good practice. Third-party credits in the item may carry their own rights.');
