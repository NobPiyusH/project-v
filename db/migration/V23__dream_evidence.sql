-- Dream Shorts learning loop, phase 1 (evidence + costs): what the owner decided, what failed, what each finished
-- Short measures, what each paid step was estimated vs what it cost, and GPU timings that recalibrate the estimates.

CREATE TABLE dream_event (
    id             uuid PRIMARY KEY,
    dream_short_id uuid NOT NULL REFERENCES dream_short (id) ON DELETE CASCADE,
    step           text,
    kind           text NOT NULL,          -- create, approve, redo, mode, retry, rerun, stop, cancel, failure, cost
    shot_ids       jsonb,
    notes          text,
    data           jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dream_event_short ON dream_event (dream_short_id, created_at);
CREATE INDEX dream_event_kind ON dream_event (kind, created_at);

CREATE TABLE dream_metric (
    asset_id       uuid PRIMARY KEY REFERENCES asset (id) ON DELETE CASCADE,
    dream_short_id uuid NOT NULL REFERENCES dream_short (id) ON DELETE CASCADE,
    look           text,
    metrics        jsonb NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dream_metric_short ON dream_metric (dream_short_id, created_at);

-- One row per rendered clip: how long the GPU really took, so estimates follow reality.
CREATE TABLE gpu_timing (
    id             uuid PRIMARY KEY,
    kind           text NOT NULL,          -- e.g. wan.fast.480p, wan.fast.720p
    frames         int NOT NULL,
    seconds        double precision NOT NULL,
    first_in_batch boolean NOT NULL,       -- the first clip in a container includes the warm-up
    dream_short_id uuid REFERENCES dream_short (id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX gpu_timing_kind ON gpu_timing (kind, created_at);

-- Measured values that replace the config's guesses once there are enough samples.
CREATE TABLE calibration (
    key        text PRIMARY KEY,           -- e.g. wan.fast.720p.per81, wan.fast.720p.first_extra
    value      double precision NOT NULL,
    samples    int NOT NULL,
    source     text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- History: clips made before this table existed (all 3.5 s shots = 57 frames at 16 fps). The batch's shot 1 ran
-- first in its container, so it carries the warm-up.
INSERT INTO gpu_timing (id, kind, frames, seconds, first_in_batch, dream_short_id, created_at)
SELECT gen_random_uuid(), 'wan.fast.' || CASE WHEN d.quality = '720p' THEN '720p' ELSE '480p' END, 57, c.units,
       c.note LIKE 'Dream Short clip, shot 1 %', c.dream_short_id, c.ts
FROM cost_entry c JOIN dream_short d ON d.id = c.dream_short_id
WHERE c.note LIKE 'Dream Short clip, shot % take %' AND c.unit_type = 'gpu_seconds' AND c.units > 0;
