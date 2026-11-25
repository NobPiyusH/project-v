-- Asset cache keyed by content (studio/assets/cache.py): a checked asset carries its content key; a reused one points at its source.
ALTER TABLE asset ADD COLUMN content_key text;
CREATE INDEX asset_content_key_idx ON asset (content_key) WHERE content_key IS NOT NULL;
ALTER TABLE provenance ADD COLUMN reused_from_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;  -- 'reused from asset X'
ALTER TABLE video ADD COLUMN no_cache boolean NOT NULL DEFAULT false;                                    -- per-video opt-out
