-- Semi-auto "Review" mode: per-video review window and the moment a waiting stage will continue on its own.
ALTER TABLE video ADD COLUMN review_minutes int NOT NULL DEFAULT 10;
ALTER TABLE stage_run ADD COLUMN review_deadline timestamptz;
