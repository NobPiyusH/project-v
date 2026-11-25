-- Paid automatic redos need approval, and a failed save can be resumed from the stored media result.
-- video.auto_redo_paid: the owner lets the studio redo a failed stage by itself even when that rents a pod or calls a paid service (default: no).
-- stage_run.*: why the stage is held for the owner, whether Resume is offered, whether the finished media result is still stored in the
-- workflow (so only the cheap save is re-run), and the estimated cost of the redo the owner is asked about.
ALTER TABLE video ADD COLUMN auto_redo_paid boolean NOT NULL DEFAULT false;
ALTER TABLE stage_run ADD COLUMN held_reason text;
ALTER TABLE stage_run ADD COLUMN can_resume boolean NOT NULL DEFAULT false;
ALTER TABLE stage_run ADD COLUMN has_stored_result boolean NOT NULL DEFAULT false;
ALTER TABLE stage_run ADD COLUMN redo_estimate_inr numeric(12, 2);
