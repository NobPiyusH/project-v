-- Dream Shorts, first-last frame chaining (owner, 20 Nov 2026), default OFF:
--  chain_bridge: every shot keeps its own picture; clip N runs from picture N to picture N+1 (Wan 2.2 first-last frame),
--                so each join lands exactly on an approved picture and nothing drifts along the chain. The last clip
--                ends freely. Never together with chain_shots (last-frame chaining): the studio stores one or the other.
ALTER TABLE dream_short ADD COLUMN chain_bridge boolean NOT NULL DEFAULT false;
