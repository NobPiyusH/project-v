-- Archive (25 Nov 2026): hide an abandoned video from the lists without deleting anything (restorable).
ALTER TABLE video ADD COLUMN archived_at timestamptz;
