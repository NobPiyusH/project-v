-- AI Producer stop rule (owner approved, 19 Nov 2026): when the same issue comes back twice in a row the Producer stops
-- re-rolling. For a known recurring cause it first tries one firm prompt / negative-prompt fix, then asks the owner.
ALTER TABLE producer_decision ADD COLUMN stop_rule text;   -- NULL | prompt_fix | ask
ALTER TABLE producer_decision ADD COLUMN stop_cause text;  -- the recurring cause, e.g. people
