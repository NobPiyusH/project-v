-- Scout cache (optimisation review item 10, owner approved 19 Nov 2026): what Gemini saw when it watched a public Short,
-- so the Scout never pays to watch the same Short twice. Keyed by the YouTube video id.
CREATE TABLE scout_watch (
    video_key   text PRIMARY KEY,           -- 11-char YouTube id
    url         text NOT NULL,
    watched     jsonb NOT NULL,             -- ScoutVideo as watched (error = 'not about this idea' when off-topic)
    model       text,
    watched_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scout_watch_recent_idx ON scout_watch (watched_at DESC);
