-- Dream Shorts (docs/plans/dream-shorts.md, owner approved D1 on 18 Nov 2026): short surreal vertical videos made
-- outside the full video pipeline. Own tables; existing tables only gain a nullable link so every asset, cost entry
-- and routing decision of a Dream Short can be traced back to it (rule 6).
CREATE TABLE dream_short (
    id             uuid PRIMARY KEY,
    idea           text NOT NULL,
    look           text NOT NULL DEFAULT 'dreamcore',
    shot_count     int NOT NULL DEFAULT 5,
    quality        text NOT NULL DEFAULT '480p',
    modes_json     jsonb NOT NULL DEFAULT '{}',
    review_minutes int NOT NULL DEFAULT 10,
    status         text NOT NULL,            -- RUNNING | WAITING | FAILED_WAITING | COMPLETED | CANCELLED
    step           text,                     -- WRITE | PICTURES | CLIPS | FINISH
    message        text,
    title          text,
    world_json     jsonb,
    final_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL,
    workflow_id    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dream_shot (
    id               uuid PRIMARY KEY,
    dream_short_id   uuid NOT NULL REFERENCES dream_short (id) ON DELETE CASCADE,
    ord              int NOT NULL,
    scene            text NOT NULL,
    picture_prompt   text NOT NULL,
    motion_prompt    text NOT NULL,
    picture_asset_id uuid REFERENCES asset (id) ON DELETE SET NULL,
    clip_asset_id    uuid REFERENCES asset (id) ON DELETE SET NULL,
    picture_takes    int NOT NULL DEFAULT 0,
    clip_takes       int NOT NULL DEFAULT 0,
    error            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dream_short_id, ord)
);

ALTER TABLE asset ADD COLUMN dream_short_id uuid REFERENCES dream_short (id) ON DELETE CASCADE;
ALTER TABLE cost_entry ADD COLUMN dream_short_id uuid REFERENCES dream_short (id) ON DELETE SET NULL;
ALTER TABLE routing_decision ADD COLUMN dream_short_id uuid REFERENCES dream_short (id) ON DELETE CASCADE;
CREATE INDEX cost_entry_dream_idx ON cost_entry (dream_short_id);
CREATE INDEX routing_decision_dream_idx ON routing_decision (dream_short_id, created_at);
