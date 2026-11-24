-- M3b (18 Nov 2026): the owner can switch a provider off from the dashboard. Kept apart from `enabled`, which the
-- registry reloads from config/providers.yaml at every start.
ALTER TABLE provider ADD COLUMN owner_disabled boolean NOT NULL DEFAULT false;
