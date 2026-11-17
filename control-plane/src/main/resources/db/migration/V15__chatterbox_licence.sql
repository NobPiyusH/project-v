-- Chatterbox (Resemble AI, MIT): the channels' main narration voice on the laptop GPU (owner, 18 Nov 2026).
-- Its output carries Resemble's inaudible Perth watermark (AI-generated audio).
INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a011', 'Chatterbox TTS (MIT)', true, false, 'https://huggingface.co/ResembleAI/chatterbox',
  'Open text-to-speech run locally; output is the studio''s own render; only clone voices the owner has rights to');
