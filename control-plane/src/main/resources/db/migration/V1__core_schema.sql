-- projectv core schema (HANDOVER §8, with R1 changes: "film" is now "video", plus stage runs and per-stage modes).
-- Tables for later milestones are created now so the data model is settled early; most stay empty until then.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE channel (
    id               uuid PRIMARY KEY,
    name             text NOT NULL,
    default_style_id text,
    languages        text[] NOT NULL DEFAULT '{en}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE licence (
    id                   uuid PRIMARY KEY,
    name                 text NOT NULL,
    commercial_use       boolean NOT NULL,
    attribution_required boolean NOT NULL DEFAULT false,
    url                  text,
    notes                text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE video (
    id           uuid PRIMARY KEY,
    channel_id   uuid REFERENCES channel (id),
    prompt       text NOT NULL,
    title        text,
    brief_json   jsonb NOT NULL DEFAULT '{}',   -- optional fields the owner gave with the prompt
    plan_json    jsonb,                          -- VideoPlan written by the Planner
    modes_json   jsonb NOT NULL DEFAULT '{}',   -- current stage -> auto|manual
    status       text NOT NULL,                  -- CREATED | RUNNING | WAITING | PAUSED | COMPLETED | FAILED | CANCELLED
    current_stage text,
    budget_inr   numeric(12, 2),
    spent_inr    numeric(12, 2) NOT NULL DEFAULT 0,
    deadline     date,
    workflow_id  text UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per execution of a stage (re-runs after a rejection get a new attempt number).
CREATE TABLE stage_run (
    id            uuid PRIMARY KEY,
    video_id      uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    stage         text NOT NULL,
    attempt       int NOT NULL,
    mode          text NOT NULL,          -- auto | manual (mode when the stage started)
    status        text NOT NULL,          -- RUNNING | WAITING_APPROVAL | APPROVED | AUTO_PASSED | REJECTED | SKIPPED | FAILED
    summary       text,
    artifact_json jsonb,
    owner_notes   text,                   -- notes the owner gave when rejecting the previous attempt
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    UNIQUE (video_id, stage, attempt)
);
CREATE INDEX stage_run_video_idx ON stage_run (video_id);

CREATE TABLE approval (
    id        uuid PRIMARY KEY,
    video_id  uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    stage     text NOT NULL,
    decision  text NOT NULL,              -- approve | reject | edit | override | mode_change
    notes     text,
    actor     text NOT NULL,
    ts        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE style_bible (
    id                  uuid PRIMARY KEY,
    name                text NOT NULL UNIQUE,
    palette             jsonb,
    lighting_rules      text,
    camera_language     text,
    negative_prompts    text,
    lora_refs           text[],
    reference_asset_ids uuid[],
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE concept (
    id              uuid PRIMARY KEY,
    video_id        uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    big_idea        text,
    clock_definition jsonb,
    acts_json       jsonb,
    version         int NOT NULL,
    approved_by     text,
    approved_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE script (
    id               uuid PRIMARY KEY,
    video_id         uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    version          int NOT NULL,
    narration_md     text,
    beat_sheet_json  jsonb,
    word_count       int,
    est_duration_sec int,
    sources_json     jsonb,
    approved         boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE provenance (
    id            uuid PRIMARY KEY,
    asset_id      uuid,                   -- set after the asset row exists
    provider      text NOT NULL,
    model         text,
    model_version text,
    prompt        text,
    seed          bigint,
    licence_id    uuid REFERENCES licence (id),
    ai_generated  boolean NOT NULL,
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE asset (
    id            uuid PRIMARY KEY,
    kind          text NOT NULL,          -- image | video | audio | subtitle | timeline | doc
    uri_hot       text,
    uri_archive   text,
    sha256        text,
    width         int,
    height        int,
    fps           numeric(6, 3),
    duration_sec  numeric(10, 3),
    embedding     vector(768),
    tags          text[],
    provenance_id uuid REFERENCES provenance (id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE provenance ADD CONSTRAINT provenance_asset_fk FOREIGN KEY (asset_id) REFERENCES asset (id);

CREATE TABLE music_cue (
    id              uuid PRIMARY KEY,
    video_id        uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    asset_id        uuid REFERENCES asset (id),
    bpm             numeric(6, 2),
    beat_times_json jsonb,
    sections_json   jsonb,
    licence_id      uuid REFERENCES licence (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scene (
    id         uuid PRIMARY KEY,
    video_id   uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    ord        int NOT NULL,
    title      text,
    purpose    text,
    start_beat int,
    end_beat   int,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shot (
    id                  uuid PRIMARY KEY,
    scene_id            uuid NOT NULL REFERENCES scene (id) ON DELETE CASCADE,
    ord                 int NOT NULL,
    duration_target_sec numeric(6, 2),
    shot_type           text,
    camera_move         text,
    description         text,
    prompt_json         jsonb,
    keyframe_asset_id   uuid REFERENCES asset (id),
    priority            text NOT NULL DEFAULT 'standard',   -- hero | standard | filler
    status              text NOT NULL DEFAULT 'PLANNED',
    chosen_take_id      uuid,
    clock_value         text,
    overlay_text        text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE provider (
    id                text PRIMARY KEY,   -- e.g. modal_a100, manual_flow
    name              text NOT NULL,
    kind              text NOT NULL,      -- api | gpu_serverless | gpu_notebook | gpu_pod | local | manual
    capabilities_json jsonb NOT NULL DEFAULT '[]',
    config_json       jsonb NOT NULL DEFAULT '{}',
    enabled           boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE take (
    id                 uuid PRIMARY KEY,
    shot_id            uuid NOT NULL REFERENCES shot (id) ON DELETE CASCADE,
    provider_id        text REFERENCES provider (id),
    model              text,
    params_json        jsonb,
    seed               bigint,
    resolution         text,
    asset_id           uuid REFERENCES asset (id),
    cost_inr           numeric(12, 2),
    credits_used       numeric(12, 2),
    critic_scores_json jsonb,
    critic_verdict     text,
    owner_rating       int,
    rejected_reason    text,
    source             text NOT NULL DEFAULT 'auto',   -- auto | manual
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE shot ADD CONSTRAINT shot_chosen_take_fk FOREIGN KEY (chosen_take_id) REFERENCES take (id);

CREATE TABLE provider_account (
    id              uuid PRIMARY KEY,
    provider_id     text NOT NULL REFERENCES provider (id),
    label           text NOT NULL,
    secret_ref      text,                 -- name of the env var / secret, never the secret itself
    quota_model_json jsonb,
    reset_rule      text,
    monthly_cap_inr numeric(12, 2),
    exhausted_until timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE quota_snapshot (
    id           uuid PRIMARY KEY,
    account_id   uuid NOT NULL REFERENCES provider_account (id),
    period_start timestamptz NOT NULL,
    period_end   timestamptz NOT NULL,
    units_total  numeric(14, 3),
    units_used   numeric(14, 3),
    source       text NOT NULL,           -- api | ledger | manual
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE job (
    id           uuid PRIMARY KEY,
    video_id     uuid REFERENCES video (id) ON DELETE CASCADE,
    shot_id      uuid REFERENCES shot (id),
    type         text NOT NULL,
    payload_json jsonb,
    status       text NOT NULL,
    provider_id  text REFERENCES provider (id),
    attempts     int NOT NULL DEFAULT 0,
    started_at   timestamptz,
    finished_at  timestamptz,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cost_entry (
    id          uuid PRIMARY KEY,
    video_id    uuid REFERENCES video (id) ON DELETE CASCADE,
    job_id      uuid REFERENCES job (id),
    provider_id text NOT NULL,
    amount_inr  numeric(12, 4) NOT NULL,
    amount_usd  numeric(12, 4),
    fx_rate     numeric(10, 4),
    units       numeric(14, 4),
    unit_type   text,                     -- tokens | gpu_hour | clip | credit | chars ...
    estimated   boolean NOT NULL DEFAULT false,
    note        text,
    ts          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cost_entry_video_idx ON cost_entry (video_id);

CREATE TABLE routing_decision (
    id                  uuid PRIMARY KEY,
    job_id              uuid REFERENCES job (id),
    candidates_json     jsonb,
    scores_json         jsonb,
    chosen_provider_id  text,
    reason_text         text,
    overridden_by_owner boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE preference_event (
    id           uuid PRIMARY KEY,
    kind         text NOT NULL,           -- pair | rating | edit_diff | override | retention | mode_change
    video_id     uuid REFERENCES video (id) ON DELETE CASCADE,
    context_json jsonb,
    chosen       text,
    rejected     text,
    weight       numeric(6, 3) NOT NULL DEFAULT 1,
    ts           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE timeline (
    id         uuid PRIMARY KEY,
    video_id   uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    version    int NOT NULL,
    otio_uri   text,
    fcpxml_uri text,
    notes      text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE publication (
    id              uuid PRIMARY KEY,
    video_id        uuid NOT NULL REFERENCES video (id) ON DELETE CASCADE,
    platform        text NOT NULL,
    external_id     text,
    language_tracks text[],
    ai_disclosure   boolean NOT NULL DEFAULT true,
    published_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE retention_curve (
    id             uuid PRIMARY KEY,
    publication_id uuid NOT NULL REFERENCES publication (id) ON DELETE CASCADE,
    points_json    jsonb NOT NULL,
    fetched_at     timestamptz NOT NULL DEFAULT now()
);
