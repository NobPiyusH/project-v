-- Per-shot redo: every keyframe version is linked to its shot (all versions kept, one is active), plus status/error.
ALTER TABLE asset ADD COLUMN shot_id uuid REFERENCES shot (id) ON DELETE SET NULL;
CREATE INDEX asset_shot_idx ON asset (shot_id);
ALTER TABLE shot ADD COLUMN error text;

-- Back-fill: link existing active keyframes to their shots.
UPDATE asset a SET shot_id = s.id FROM shot s WHERE s.keyframe_asset_id = a.id;
UPDATE shot SET status = 'READY' WHERE keyframe_asset_id IS NOT NULL;
