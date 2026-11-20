-- Dream Shorts on the GPU broker: Auto (the studio picks the cheapest, or what the owner usually chooses) or Manual
-- (the owner picks one option from the live price list). gpu_choice is the option key "provider|gpu".
ALTER TABLE dream_short ADD COLUMN gpu_mode text NOT NULL DEFAULT 'auto';
ALTER TABLE dream_short ADD COLUMN gpu_choice text;
