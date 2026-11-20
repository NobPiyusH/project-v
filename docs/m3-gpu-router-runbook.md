# M3 GPU broker: runbook

Design: `docs/plans/m3-gpu-router.md`. Code: `control-plane/src/main/java/com/projectv/gpu/broker/`.
State on 20 Nov 2026: interface, wallet, broker, RunPod adapter and stubs are built and tested against **fakes only**.
The pod worker (`workers/pod/`, ported from the Salad branch) and the Dream Shorts wiring behind `DREAM_GPU_PROVIDER=broker` are built too. Nothing has rented a GPU.

## What you can switch (all default to "off / today's behaviour")
| Setting | Where | Default | Meaning |
|---|---|---|---|
| `DREAM_GPU_PROVIDER` | `.env` | `modal` | `modal` = unchanged. `broker`: pictures and clips run on one rented pod per Short (cheapest live price). Restart the control plane after changing it |
| `pricing.gpuBroker.providers.<id>.monthlyCapUsd` | `config/providers.yaml` | runpod `8` (owner), others `0` (blocked) | monthly spend cap per provider, checked against the cost ledger before every rental |
| `pricing.gpuBroker.totalCapUsd` | same | `8` | all rented GPUs together |
| `pricing.gpuBroker.order` | same | runpod, jarvislabs, cloudrift, vast, shadeform, modal | tie-break only; the cheapest live price wins |
| `RUNPOD_API_KEY` | `.env` | empty | without it the RunPod adapter is skipped |
| `GPU_WATCHDOG_DRY_RUN` | `.env` | `true` | the sweep only logs until you set `false` |
| `GPU_MAX_PODS`, `GPU_MAX_HOURLY_USD`, `GPU_MAX_RUNTIME_MINUTES`, `GPU_IDLE_MINUTES` | `.env` | 1, 2.0, 120, 15 | start-side refusal and self-stop limits |

`modal.monthlyCapUsd` is not touched by this work.

## Safety layers (rule 8)
1. The broker refuses a rental when the wallet cap is 0 or too small, no stock, or the watchdog limits would be passed.
2. Every boot command is wrapped by `PodSelfStop` (deadline, idle, job exit) and every pod name starts `projectv-`.
3. A lease is opened right after create; `release` terminates, books a CostEntry and releases the lease.
4. `PodWatchdog` terminates leftover studio pods (lease released / stale / missing). Turn dry-run off deliberately.

## Before the first paid test (owner decisions)
1. Set `pricing.gpuBroker.providers.runpod.monthlyCapUsd` (suggest 5) and `RUNPOD_API_KEY` in `.env`.
2. Decide fast-boot: global volume `runpod storage` (has the Wan weights, untested) or Hugging Face download (~10 min).
3. Steps 2 and 3 of the plan now exist (worker + wiring), tested with fakes only. The first live run is the real test of RunPod create/status and of the worker boot (ComfyUI pip, weights): expect to fix small things.
4. First live test: one Short with 1-2 clips; check `routing_decision` row, `gpu_pod_lease`, the CostEntry, and that
   `list-pods` shows no `projectv-*` pod afterwards.

## Read-only checks (no spend)
- Price/stock now (RunPod): the `get-gpu-type` tool with availability, or the dashboard; the adapter's `quote` does the same call.
- Tests: `./gradlew :control-plane:test --tests 'com.projectv.gpu.*'` (needs `JAVA_HOME` = JDK 21 and `GRADLE_OPTS=-Djava.net.preferIPv4Stack=true`).

## If something looks stuck
A step "waiting for owner" with "No GPU provider can run ..." lists every provider and why it was skipped (the same text is
the `routing_decision` row). Fix the cap or wait for stock, then Retry. Never leave a pod running: check the RunPod console
for `projectv-*` pods and terminate by hand.

## Adapter status
RunPod: built (create/status unverified live). Jarvislabs, CloudRift, Vast.ai, Shadeform, Modal: stubs in `BrokerStubs.java`,
each with a TODO naming what it needs (key, API shape).

## How a Short runs on the broker
1. Writing step: `DreamGpu.warm` rents the cheapest pod so the ~10 min download overlaps the writing (best effort).
2. Pictures / clips: `DreamGpu.session` reuses that pod; the worker activities (`workers/activities/keyframes.py`, `clips.py`) call it over
   HTTP through `workers/pod/client.py` (waits for `/health` ready, resubmits a job if the pod was lost, max 2 retries).
3. When the step reaches MUSIC / FINISH, or the Short completes / is cancelled / fails to "waiting for owner": `GpuBroker.release`
   terminates the pod and books ONE CostEntry (lifetime x live rate, provider id e.g. `runpod`). No per-job entries.
4. The worker pings `/api/internal/gpu/{shortId}/heartbeat` so an active pod is never idle to the watchdog.
After a control-plane restart the broker rebuilds its open rentals from `gpu_pod_lease` (V33 stores the Short, live rate and GPU), so release still terminates and books; a pod found GONE on reuse is released and rented again.
The control plane must run where `../workers/pod/server.py` resolves (`POD_WORKER_FILE` overrides).

## Review-fix follow-ups (documented, not built)
- Live check of the RunPod create body (`createBody`, `SV_VOLUME` mount `/workspace`, `cmd` exec form): needs a real pod and the owner's yes. Not run.
- Warm-up (`DreamGpu.warm`) rents the pod before the owner approves the clip/picture cost.
- The wallet counts finished rentals only, not pods still open.
- The pod's HTTP proxy port is unauthenticated (anyone with the pod URL can submit jobs); add a token header before wider use.
- A pod idle >15 min (e.g. a long Review/Manual wait) self-terminates by design; the next `acquire` notices (status GONE), books it, and rents again (boot ~15 min).
- The dry-run watchdog books an orphan pod's running cost every ~10 min (`gpu_pod_event.kind = orphan_billed`); the real kill books only the remainder.
