-- M2b: moving video per shot. Every clip is an asset (kind 'video', linked to its shot); the shot points at its
-- active draft and final takes. Drafts: Wan fast mode at 480p. Finals: Wan hybrid mode at 720p.
ALTER TABLE shot ADD COLUMN draft_clip_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
ALTER TABLE shot ADD COLUMN final_clip_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL;
ALTER TABLE shot ADD COLUMN clip_status text;      -- RENDERING / READY / FAILED / STOPPED
ALTER TABLE shot ADD COLUMN clip_error text;
-- false = the owner keeps the draft (no final render for this shot)
ALTER TABLE shot ADD COLUMN final_wanted boolean NOT NULL DEFAULT true;
ALTER TABLE asset ADD COLUMN clip_kind text;       -- draft / final (video assets only)

INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a004', 'Wan 2.2 I2V A14B + lightx2v LoRA (Apache-2.0)', true, false,
  'https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B', 'Open-weights video model run on Modal');
