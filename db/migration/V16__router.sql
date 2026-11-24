-- M3a router core (18 Nov 2026): every routing decision is logged with its video and stage (rule 6), and providers
-- that fail are cooled down for a while instead of being retried at once.
ALTER TABLE routing_decision ADD COLUMN video_id uuid REFERENCES video (id) ON DELETE CASCADE;
ALTER TABLE routing_decision ADD COLUMN stage text;
ALTER TABLE routing_decision ADD COLUMN capability text;
CREATE INDEX routing_decision_video_idx ON routing_decision (video_id, created_at);

CREATE TABLE provider_health (
    provider_id    text PRIMARY KEY,
    fails          int NOT NULL DEFAULT 0,
    cooldown_until timestamptz,
    last_error     text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);
