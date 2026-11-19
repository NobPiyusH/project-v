-- Daily autopilot (owner approved 19 Nov 2026): each morning one Dream Short starts by itself from the owner's idea
-- list (or an unused Scout angle), the AI Producer runs it, and it waits as "ready to post" until the owner taps Post.

-- The AI Producer now also reviews Dream Shorts: a decision belongs to a video OR a Dream Short.
ALTER TABLE producer_decision ALTER COLUMN video_id DROP NOT NULL;
ALTER TABLE producer_decision ADD COLUMN dream_short_id uuid REFERENCES dream_short (id) ON DELETE CASCADE;
CREATE INDEX producer_decision_dream_idx ON producer_decision (dream_short_id, created_at);
-- Autopilot Shorts: the Producer decides by itself on this Short, whatever the global trust switch says.
ALTER TABLE dream_short ADD COLUMN producer_decides boolean NOT NULL DEFAULT false;

-- One row of settings. Off until the owner turns it on.
CREATE TABLE autopilot_setting (
    id             int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled        boolean NOT NULL DEFAULT false,
    start_time     time NOT NULL DEFAULT '07:00',   -- local time (Asia/Kolkata), each day
    start_mode     text NOT NULL DEFAULT 'AUTO',    -- AUTO starts at the time; REVIEW waits review_minutes first; MANUAL waits for Start
    review_minutes int NOT NULL DEFAULT 30,
    daily_cap_inr  numeric(12, 2) NOT NULL DEFAULT 50,   -- owner set 19 Nov 2026
    step_modes     jsonb NOT NULL DEFAULT '{"SCOUT":"AUTO","WRITE":"PRODUCER","PICTURES":"PRODUCER","CLIPS":"PRODUCER","MUSIC":"PRODUCER","FINISH":"PRODUCER"}',
    look           text,                            -- null = rotate through the looks
    shots          int NOT NULL DEFAULT 5,
    use_scout      boolean NOT NULL DEFAULT true,   -- when the idea list is empty, use an unused Scout angle
    privacy        text NOT NULL DEFAULT 'private', -- YouTube privacy for Post: private | unlisted | public
    updated_at     timestamptz NOT NULL DEFAULT now()
);
INSERT INTO autopilot_setting (id) VALUES (1);

CREATE TABLE autopilot_idea (
    id          uuid PRIMARY KEY,
    idea        text NOT NULL,
    look        text,
    source      text NOT NULL DEFAULT 'owner',      -- owner | scout
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- One run per day. status: WAITING_START | RUNNING | NEEDS_YOU | FAILED | READY | POSTING | POSTED | POST_FAILED | SKIPPED
CREATE TABLE autopilot_run (
    id              uuid PRIMARY KEY,
    run_date        date NOT NULL UNIQUE,
    status          text NOT NULL,
    message         text,
    idea            text,
    dream_short_id  uuid REFERENCES dream_short (id) ON DELETE SET NULL,
    title           text,
    description     text,
    hashtags        text,
    privacy         text,
    youtube_id      text,
    starts_at       timestamptz,
    posted_at       timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
