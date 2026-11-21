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
RunPod: built (create/status unverified live). Vast.ai: built 21 Nov 2026 (`VastProvider`; search checked live read-only, create/show/destroy
checked against docs.vast.ai only, see "Vast.ai" below). Jarvislabs, CloudRift, Shadeform, Modal: stubs in `BrokerStubs.java`,
each with a TODO naming what it needs (key, API shape).

## Vast.ai (owner decision 21 Nov 2026; strangers' machines, so security first)
**Keys (22 Nov 2026).** `VAST_API_KEY` in `.env` is used for offer search only. This account has 2FA on, so instance and account calls need a
**2FA session key**: run `vastai tfa login --method-type totp -c <code>`; the CLI saves it in `~/.config/vastai/vast_tfa_key`. The control plane reads
`VAST_SESSION_KEY` (`projectv.vast.session-key`) or, if empty, that file on every call (a fresh login works without a restart). The session key's
lifetime is not documented. When Vast answers 401/403 on an account call, the GPU card shows "Vast 2FA session expired: run `vastai tfa login
--method-type totp -c <code>`", the broker rents nothing, and the watchdog logs it once per distinct failure and destroys nothing.
**Endpoints:** instance list and show use `GET /api/v1/instances/` (keyset paged, 25 per page; show = `select_filters={"id":{"eq":N}}`, since v1 has no
by-id route). The v0 list answers 410 (deprecated). Create (`PUT /api/v0/asks/{id}/`) and destroy (`DELETE /api/v0/instances/{id}/`) are still v0 in the docs
and answer with proper v0 error bodies live (checked read-only with invalid ids, 22 Nov); there is no v1 create/destroy.

**Safety design**
1. *No secret on the instance.* The create body has: image, label `projectv-vast-...`, disk, launch mode `args` (no SSH, no Jupyter), the guard command, and
   env `SV_BOOT_*` (worker source), `SV_PORT`, `SV_ROOT`, `SV_TOKEN`, plus `-p 8000:8000`. Never the Vast, RunPod, S3 or Gemini keys. Vast adds its own
   per-instance `CONTAINER_API_KEY` (controls only that instance); do NOT set "account environment variables" on cloud.vast.ai (they are injected into every container).
2. *Worker token.* `WorkerAuth`: token = HMAC(secret, pod name), passed as `SV_TOKEN`; the worker answers 401 to everything except `/live`
   (constant-time compare; an unauthorised request does not even touch the idle heartbeat). The pod address handed to Python is
   `http://worker:<token>@ip:port`; `workers/pod/client.py` turns it into `Authorization: Bearer`. Secret = `POD_TOKEN_SECRET` or derived from the provider keys.
   RunPod pods get the same token (`POD_TOKEN_RUNPOD=false` turns it off for RunPod only). The token stops outsiders; it does NOT hide anything from the host:
   the host machine can read every prompt, picture and clip on it. Keep private material (reference photos of people) off Vast.
3. *Teardown is ours.* `terminate` = `DELETE /instances/{id}/`. The in-pod guard (`PodSelfStop.wrapExit`) only ends the worker (container exits); the
   broker release and a second `PodWatchdog` bean (`vastWatchdog`, label prefix `projectv-`) destroy the instance, including exited ones. Never-running
   instances are destroyed after `startTimeoutSeconds` (480, under the watchdog's 10 min lease grace) and the next offer is tried; `status()` maps
   exited / offline / unknown / 404 to GONE so the existing replace path rents again.
4. *Offer filter (all in config/providers.yaml, `providers.vast`).* On-demand, verified, reliability >= 0.97, 1 GPU, gpu_name in RTX 4090 / 5090 / L40S / A6000,
   >= 24 GB VRAM, >= 48 GB RAM, disk >= 90 GB, download >= 500 Mb/s, a direct public port. Price = the live `dph_total` (disk included via `allocated_storage`)
   plus the host's own internet price per GB (Vast bills it; some hosts ask $50+/TB, which would cost more than the GPU) spread over 0.75 h; hosts above `maxBandwidthUsd` are skipped.
   The RoutingDecision candidate row carries the host's country and machine id. Cap: `monthlyCapUsd: 5`; the shared $3.50/day cap still applies first.
5. *Blocking create.* Vast gives the public port only once the instance runs (image pull), so `create` waits up to 480 s for it (the broker lock is held meanwhile).

**Unverified (needs the first paid test):** create body acceptance (`runtype: args` with an array, the long `env` string), the show/list/destroy response shapes with a working key,
the address/port mapping field names on a running instance, the field names in the v1 list rows (`ports`, `public_ipaddr`, `actual_status`, `label`), bandwidth cost really billed per GB, that RunPod's proxy passes the `Authorization` header.

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
