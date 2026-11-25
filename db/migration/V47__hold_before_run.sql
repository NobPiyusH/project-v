-- Hold before run (25 Nov 2026): a stage that can spend money waits for the owner BEFORE it runs.
-- video.approve_paid_first: ask before every paid stage, whatever its mode (default on for videos made from a plan file; set by the app).
-- stage_run.held_before_run / estimate_inr: the stage is waiting before it spends, and the estimated cost the owner is asked about.
ALTER TABLE video ADD COLUMN approve_paid_first boolean NOT NULL DEFAULT false;
ALTER TABLE stage_run ADD COLUMN held_before_run boolean NOT NULL DEFAULT false;
ALTER TABLE stage_run ADD COLUMN estimate_inr numeric(12, 2);
