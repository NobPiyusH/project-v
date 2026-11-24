-- AI Producer mode, P1 (owner, 18 Nov 2026): every review the Producer makes, its recommendation, and what the owner
-- then decided (so its agreement with the owner can be measured, and lessons learned).
CREATE TABLE producer_decision (
    id               uuid PRIMARY KEY,
    video_id         uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    stage            text NOT NULL,
    attempt          int NOT NULL,
    verdict          text NOT NULL,            -- approve | redo | ask
    confidence       numeric(4, 3),
    summary          text,
    redo_notes       text,
    items_json       jsonb,                    -- per item (line, shot, clip): score, ok, problem, fix
    model            text,
    cost_inr         numeric(12, 4),
    owner_decision   text,                     -- approve | reject (filled when the owner decides)
    owner_notes      text,
    agreed           boolean,
    created_at       timestamptz NOT NULL DEFAULT now(),
    decided_at       timestamptz
);
CREATE INDEX producer_decision_video_idx ON producer_decision (video_id, created_at);

-- Rules the Producer learned from the owner's lessons (P2 distils them; the owner can edit or disable any).
CREATE TABLE producer_rule (
    id          uuid PRIMARY KEY,
    stage       text,                          -- null = every stage
    rule_text   text NOT NULL,
    source      text NOT NULL DEFAULT 'learned',  -- learned | owner
    enabled     boolean NOT NULL DEFAULT true,
    evidence    int NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
