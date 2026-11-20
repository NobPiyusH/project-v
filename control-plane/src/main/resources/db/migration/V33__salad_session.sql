-- V33 (not V32: main has V32__dream_gpu_pick). IF NOT EXISTS because a database that ran this branch while it was numbered V32 already has the table.
-- Salad route (docs/plans/salad-route.md): one container group per Dream Short, kept so a control-plane restart still
-- knows what to delete and how much job time was already booked in the cost ledger.
CREATE TABLE IF NOT EXISTS salad_session (
    group_name      text PRIMARY KEY,
    dream_short_id  uuid NOT NULL,
    url             text NOT NULL,
    gpu_class       text NOT NULL,
    priority        text NOT NULL,
    hourly_usd      numeric(10, 4) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    booked_seconds  double precision NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS salad_session_open ON salad_session (dream_short_id) WHERE deleted_at IS NULL;
