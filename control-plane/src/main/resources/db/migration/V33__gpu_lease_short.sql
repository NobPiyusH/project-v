-- A lease now remembers which Dream Short rented the pod, at what live rate, so the broker can rebuild its open
-- rentals after a control-plane restart (release still terminates the pod and books the CostEntry).
ALTER TABLE gpu_pod_lease ADD COLUMN dream_short_id uuid;
ALTER TABLE gpu_pod_lease ADD COLUMN hourly_usd double precision;
ALTER TABLE gpu_pod_lease ADD COLUMN gpu text;
