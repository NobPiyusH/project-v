# M3 GPU broker: runbook

Design: `docs/plans/m3-gpu-router.md`. Code: `control-plane/src/main/java/com/projectv/gpu/broker/`.
State on 20 Nov 2026: interface, wallet, broker, RunPod adapter and stubs are built and tested against **fakes only**.
Nothing is wired into Dream Shorts and nothing has rented a GPU.

## What you can switch (all default to "off / today's behaviour")
| Setting | Where | Default | Meaning |
|---|---|---|---|
| `DREAM_GPU_PROVIDER` | `.env` | `modal` | `modal` = unchanged. `broker` will use rented pods once step 3 (wiring) exists |
| `pricing.gpuBroker.providers.<id>.monthlyCapUsd` | `config/providers.yaml` | runpod `8` (owner), others `0` (blocked) | monthly spend cap per provider, checked against the cost ledger before every rental |
| `pricing.gpuBroker.totalCapUsd` | same | `8` | all rented GPUs together |
| `pricing.gpuBroker.order` | same | runpod, jarvislabs, cloudrift, vast, shadeform, modal | tie-break only; the cheapest live price wins |
| `RUNPOD_API_KEY` | `.env` | empty | without it the RunPod adapter is skipped |
| `GPU_WATCHDOG_DRY_RUN` | `.env` | `true` | the sweep only logs until you set `false` |
| `GPU_MAX_PODS`, `GPU_MAX_HOURLY_USD`, `GPU_MAX_RUNTIME_MINUTES`, `GPU_IDLE_MINUTES` | `.env` | 2, 2.0, 120, 15 | start-side refusal and self-stop limits |

`modal.monthlyCapUsd` is not touched by this work.

## Safety layers (rule 8)
1. The broker refuses a rental when the wallet cap is 0 or too small, no stock, or the watchdog limits would be passed.
2. Every boot command is wrapped by `PodSelfStop` (deadline, idle, job exit) and every pod name starts `projectv-`.
3. A lease is opened right after create; `release` terminates, books a CostEntry and releases the lease.
4. `PodWatchdog` terminates leftover studio pods (lease released / stale / missing). Turn dry-run off deliberately.

## Before the first paid test (owner decisions)
1. Set `pricing.gpuBroker.providers.runpod.monthlyCapUsd` (suggest 5) and `RUNPOD_API_KEY` in `.env`.
2. Decide fast-boot: global volume `runpod storage` (has the Wan weights, untested) or Hugging Face download (~10 min).
3. Step 2 and 3 of the plan must exist first: pod worker + DreamService wiring. Until then there is nothing to test live.
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
