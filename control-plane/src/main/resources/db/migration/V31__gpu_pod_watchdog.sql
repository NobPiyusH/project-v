-- GPU pod watchdog (CLAUDE.md rule 8). A lease is the studio's claim on a pod it started; the watchdog terminates
-- studio-prefixed pods with no lease, a stale heartbeat, a released lease, or past the max lifetime.
CREATE TABLE gpu_pod_lease (
    pod_id       text PRIMARY KEY,
    provider     text NOT NULL,              -- runpod
    name         text NOT NULL,
    purpose      text,                       -- e.g. wan clips batch, seedvr2 upscale
    created_at   timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    released_at  timestamptz
);

-- Evidence: every pod the watchdog terminated, and why.
CREATE TABLE gpu_pod_event (
    id         bigserial PRIMARY KEY,
    provider   text NOT NULL,
    pod_id     text NOT NULL,
    name       text,
    kind       text NOT NULL,                -- terminated
    reason     text,
    data       jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX gpu_pod_event_time ON gpu_pod_event (created_at);
