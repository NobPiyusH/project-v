-- Dream Shorts learning loop (phases 3-4): the owner's reviews of clips and Shorts, and the lessons (rules) they become.
-- A lesson is PROPOSED from a review, the owner makes it ACTIVE (it then goes into the prompts) or RETIRED.
CREATE TABLE dream_review (
    id             uuid PRIMARY KEY,
    dream_short_id uuid NOT NULL REFERENCES dream_short (id) ON DELETE CASCADE,
    shot_id        uuid REFERENCES dream_shot (id) ON DELETE SET NULL,
    rating         int  NOT NULL CHECK (rating BETWEEN 1 AND 5),
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE dream_lesson (
    id         uuid PRIMARY KEY,
    rule       text NOT NULL,
    target     text NOT NULL CHECK (target IN ('WRITER', 'PICTURE', 'ALL')),
    status     text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'ACTIVE', 'RETIRED')),
    review_id  uuid REFERENCES dream_review (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dream_review_short ON dream_review (dream_short_id);
