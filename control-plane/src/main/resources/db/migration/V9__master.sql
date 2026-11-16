-- M2c part 1: the finished edit of a video (master MP4, Premiere package, captions).
ALTER TABLE video ADD COLUMN master_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
ALTER TABLE video ADD COLUMN master_package_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
ALTER TABLE video ADD COLUMN master_captions_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
