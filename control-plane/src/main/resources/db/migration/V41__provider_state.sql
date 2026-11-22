-- GPU broker provider state (owner, 22 Nov 2026): a provider is only searched by Auto once it has run a real worker.
-- 'proven' is set by the canary (one tiny owner-approved rental) or by hand; RunPod and Vast.ai have already run real pods.
CREATE TABLE gpu_provider_state (
    provider       text        PRIMARY KEY,
    proven         boolean     NOT NULL DEFAULT false,
    proven_at      timestamptz,
    proven_note    text,
    preflight_ok   boolean,
    preflight_at   timestamptz,
    preflight_note text
);
INSERT INTO gpu_provider_state (provider, proven, proven_at, proven_note) VALUES
    ('runpod', true, now(), 'ran real pods before the canary existed'),
    ('vast',   true, now(), 'ran real pods before the canary existed');
