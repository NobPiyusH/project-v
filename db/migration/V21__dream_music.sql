-- Dream Shorts music (owner, 18 Nov 2026: Lyria, ~₹10 a Short): the Short's own score, kept so a re-finish reuses it.
ALTER TABLE dream_short ADD COLUMN music_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
