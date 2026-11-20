-- One shared GPU pod (owner, 20 Nov 2026): which Dream Shorts are using a pod right now, so a restart of the control plane
-- can rebuild who was using it. A row is added when a Short starts using the pod and deleted when it lets go (or the pod ends).
CREATE TABLE gpu_pod_user (
    pod_id         text        NOT NULL,
    dream_short_id uuid        NOT NULL,
    attached_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pod_id, dream_short_id)
);
