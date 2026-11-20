-- Dream Shorts GPU power (owner, 20 Nov 2026):
--  gpu_hold 'auto'   = the studio turns the pod off as soon as the GPU steps are over (the behaviour so far).
--  gpu_hold 'manual' = the pod stays on until the owner turns it off (button), or until nothing used it for
--                      gpu_idle_minutes, or the Short is cancelled. A warm pod makes a redo skip the ~10 min start-up.
-- The look 'unfiltered' needs no column: it is a value of dream_short.look (the owner's prompt is used as written).
ALTER TABLE dream_short ADD COLUMN gpu_hold text NOT NULL DEFAULT 'auto';
ALTER TABLE dream_short ADD COLUMN gpu_idle_minutes int NOT NULL DEFAULT 10;
