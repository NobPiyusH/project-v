-- Inspector (second-level, independent checks of every stage's output). One row per inspected stage attempt.
CREATE TABLE inspection (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id      uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    stage         text NOT NULL,
    attempt       int  NOT NULL,
    verdict       text NOT NULL CHECK (verdict IN ('PASS', 'WARN', 'FAIL')),
    findings_json jsonb NOT NULL DEFAULT '[]',
    checked_json  jsonb NOT NULL DEFAULT '[]',
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (video_id, stage, attempt)
);
CREATE INDEX inspection_video_idx ON inspection (video_id, stage);
