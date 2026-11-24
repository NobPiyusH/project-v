-- Creative Director: story review at three levels (shot, paragraph/act, whole video), one row per reviewed attempt.
CREATE TABLE creative_review (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id    uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    stage       text NOT NULL,
    attempt     int  NOT NULL,
    model       text,
    review_json jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (video_id, stage, attempt)
);
CREATE INDEX creative_review_video_idx ON creative_review (video_id, stage);
