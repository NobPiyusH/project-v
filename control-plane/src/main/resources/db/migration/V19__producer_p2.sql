-- AI Producer P2 (owner, 18 Nov 2026): what the Producer did by itself (approved / redo / asked / approved_spend),
-- and the owner's trust switch (auto = decide once agreement is high enough; advise; decide).
ALTER TABLE producer_decision ADD COLUMN acted text;
CREATE TABLE producer_setting (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
