-- Where a pod's boot time goes (warm-start work, 22 Nov 2026): the broker's own timestamps (rent accepted, provider says
-- running) plus the worker's boot timeline (deps, ComfyUI, each weight download with size/seconds/MB/s, first job ready).
CREATE TABLE gpu_boot_timing (
    pod_id      text        PRIMARY KEY,
    provider    text        NOT NULL,
    image       text,
    rented_at   timestamptz NOT NULL,
    running_at  timestamptz,
    worker      jsonb,
    reported_at timestamptz
);
