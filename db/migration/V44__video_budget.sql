-- Per-video spend cap (owner, 25 Nov 2026: nothing limited what ONE video could spend). video.budget_inr has existed since V1 and was
-- never used; it is now the video's own cap in INR. NULL = the default in config/providers.yaml (pricing.perVideo.defaultCapInr),
-- 0 = no cap. A Dream Short gets the same column.
ALTER TABLE dream_short ADD COLUMN budget_inr numeric(12, 2);
