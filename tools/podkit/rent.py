"""Rent one RunPod pod: community first, then secure, price ceilings per tier, a bad-price pod is terminated at once.

`gql` is injected (a function taking a query string and variables and returning the parsed JSON), so this logic is tested with a
fake. Never prints keys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

TERMINATE = "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }"
CREATE = (
    "mutation($input: PodFindAndDeployOnDemandInput){ podFindAndDeployOnDemand(input:$input)"
    "{ id costPerHr desiredStatus machineId } }"
)

DEFAULT_GPUS = ("NVIDIA GeForce RTX 4090", "NVIDIA RTX A6000", "NVIDIA L40S", "NVIDIA A40")
# Wan 2.2 14B fp8 + SVI peaks near 24 GB: a 4090 (24 GB) is enough, a 3090 works at lower resolution, 16 GB does not fit.


@dataclass
class Pod:
    id: str
    gpu: str
    cloud: str
    cost_per_hr: float
    created: float


def terminate(gql, pod_id: str, tries: int = 5, sleep=time.sleep) -> bool:
    for _ in range(tries):
        if not gql(TERMINATE, {"input": {"podId": pod_id}}).get("errors"):
            return True
        sleep(5)
    return False


def rent_pod(
    gql,
    make_input,
    gpus=DEFAULT_GPUS,
    tiers=(("COMMUNITY", 0.55), ("SECURE", 0.89)),
    say=print,
    clock=time.time,
    sleep=time.sleep,
) -> Pod | None:
    """Try every (tier, gpu) pair in order. make_input(cloud, gpu) builds the RunPod input. Returns the first pod whose
    price is under that tier's ceiling, or None (stock gap: the caller waits and retries)."""
    for cloud, ceiling in tiers:
        for gpu in gpus:
            r = gql(CREATE, {"input": make_input(cloud, gpu)})
            d = (r.get("data") or {}).get("podFindAndDeployOnDemand")
            if d and d.get("id"):
                price = float(d.get("costPerHr") or 0)
                say(f"RENTED {gpu} {cloud} pod {d['id']} ${price}/h")
                if price > ceiling:
                    say(f"above the ${ceiling}/h ceiling for {cloud}: terminating")
                    terminate(gql, d["id"], sleep=sleep)
                    continue
                return Pod(d["id"], gpu, cloud, price, clock())
            say(f"no {gpu} in {cloud}: {str((r.get('errors') or [{}])[0].get('message'))[:90]}")
    return None
