-- M2a: research results, animatic link, licence seeds.

CREATE TABLE research (
    id           uuid PRIMARY KEY,
    video_id     uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    version      int NOT NULL,
    facts_json   jsonb NOT NULL,
    sources_json jsonb NOT NULL,
    queries      text[],
    model        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX research_video_idx ON research (video_id);

ALTER TABLE video ADD COLUMN animatic_asset_id uuid REFERENCES asset (id);
ALTER TABLE asset ADD COLUMN video_id uuid REFERENCES video (id) ON DELETE CASCADE;
ALTER TABLE asset ADD COLUMN mime_type text;
CREATE INDEX asset_video_idx ON asset (video_id);
CREATE INDEX scene_video_idx ON scene (video_id);
CREATE INDEX shot_scene_idx ON shot (scene_id);

-- Licences recorded on every generated asset's provenance (HANDOVER §22). Verify terms periodically.
INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a001', 'Gemini API output (Google Generative AI terms)', true, false,
  'https://ai.google.dev/gemini-api/terms', 'Text, speech (TTS), images (Nano Banana) and music (Lyria) generated via the paid Gemini API'),
 ('00000000-0000-0000-0000-00000000a002', 'Z-Image Turbo (Apache-2.0)', true, false,
  'https://huggingface.co/Tongyi-MAI/Z-Image-Turbo', 'Open-weights model run on Modal'),
 ('00000000-0000-0000-0000-00000000a003', 'Project V render', true, false, NULL,
  'Rendered by our own pipeline from licensed inputs (animatic, edits)');
