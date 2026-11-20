# M3 GPU broker for Dream Shorts (design, 20 Nov 2026)

Status: design + first build (interface, RunPod adapter, fake-provider tests). **Nothing rents a GPU yet**; the flag
`projectv.dream.gpu-provider` defaults to `modal` (today's behaviour). Extends `docs/plans/M3-router.md` (M3c) and
reuses `com.projectv.gpu` (PodWatchdog / PodLeases / PodSelfStop, merged) and the ideas of the dropped Salad branch.

## Goal
Pictures (Z-Image Turbo) and clips (Wan 2.2 I2V A14B fp8 + lightx2v 4-step, ~76 s/clip on a 24 GB 4090, 41.6 GB
weights) must not depend on Modal alone. Modal stays hard-blocked by `modal.monthlyCapUsd: 0` until 1 Dec 2026 and is
the last fallback after that. Quality first (fp8 accepted by the owner), cost second, spend only inside caps.

## Scope
In: GPU rental broker for `image.dream` and `video.i2v.dream`. Out: SeedVR2 4K (48 GB+), long-form, music, finish.

## 1. Provider adapter interface (`com.projectv.gpu.broker.GpuProvider`)
`GpuProvider extends PodApi`, so the **same adapter feeds the watchdog** (`listPods`, `terminate`) and the broker.

| Call | Meaning |
|---|---|
| `id()`, `configured()` | stable id (`runpod`, `jarvislabs`, ...); false without a key in `.env` |
| `quote(GpuSpec)` -> `Quote` | read-only: `available` + stock grade + **live** hourly USD + chosen GPU name. Never creates anything |
| `create(CreateRequest)` -> `Instance` | the only call that spends: name (studio prefix), image, disk, boot argv (already wrapped by `PodSelfStop`), env, port, volume. Returns id + hourly USD + endpoint URL |
| `status(id)` -> `InstanceStatus` | state (`STARTING/RUNNING/GONE`), `ready` (worker `/health` is the readiness truth, see 5), endpoint |
| `terminate(id)` | permanent delete (never stop: stopped pods still bill disk) |
| `run` | not an adapter call: a pod runs its boot command by itself (self-contained worker, HTTP `/picture`, `/clip`, `/jobs/{id}`; same worker contract as the Salad branch). The broker only returns the endpoint |

Adapters: **RunPod** (built now), Jarvislabs, CloudRift, Vast.ai, Shadeform, Modal = thin stubs that report
`configured()=false` / "not implemented" until the owner gives keys and approves a test.
Quirks to honour: RunPod Community 4090/5090 often "no instances" (Secure works); CloudRift REST bodies are
`{"version":..,"data":{..}}`, header `X-API-Key`, Asia stock thin; Jarvislabs through the `jl` CLI/`JL_API_KEY`.

## 2. Selection order and fallback chain
Config `projectv.gpu.broker.order` (default `runpod,jarvislabs,cloudrift,vast,shadeform,modal`). For one request the
broker walks the chain and, for each provider, records why it was skipped or chosen:
1. enabled in config and `configured()` (key present);
2. **wallet** allows the estimated cost (section 3);
3. `quote()` says stock is available at a live price <= `maxHourlyUsd` (the watchdog's limit);
4. `PodWatchdog.checkCanStart(hourly)` (max pods, max hourly spend);
5. `create()`; on failure the instance is terminated if it exists, the failure is recorded, the next provider is tried.
First provider that passes is used for the whole Short (one pod per Short). Modal is last and only if the wallet and
`modal.monthlyCapUsd` allow it (0 until 1 Dec, so it is skipped with that reason). No provider left: the step fails
to the owner (rule 2b: Retry / Continue without it), never a silent downgrade of quality (fp8 only; no weaker model).
Within a provider, GPU preference is config (`4090, 5090, A100 40GB, L40S, A6000`, all >= 24 GB), cheapest in stock.

## 3. Budget wallet in our ledger (rule 6)
`GpuWallet`: per-provider monthly cap in USD from `config/providers.yaml` -> `pricing.gpuBroker.providers.<id>.monthlyCapUsd`
(all start at **0 = blocked**; the owner raises one when he says yes) plus a `totalCapUsd` for all rented GPUs.
Spent = sum of `cost_entry` rows of that provider id in the current month (`runpod`, `jarvislabs`, ...), the same
table every other cap uses. Before create: `estimate = hourly x expectedHours` (boot 15 min + jobs) must fit the cap.
After release: one `CostEntry` per pod (`gpu_hour`, estimated, hours x live rate; the provider bill is the truth) and,
for every acquire attempt, one `routing_decision` row (candidates_json = each provider with skip reason / quote,
chosen_provider_id, reason_text, `dream_short_id`). Existing Modal accounting is untouched.

## 4. Safety (rule 8)
- Every pod name starts with `projectv-` (+ provider + short id); boot argv is always `PodSelfStop.wrap(...)` (deadline,
  idle, job-exit self-terminate). The broker refuses to create without it (`GpuBroker` builds the argv itself).
- Lease written right after create (`PodLeases.open`), heartbeat on every poll/job, released + terminate in `finally`.
- `PodWatchdog` keeps sweeping with the adapter as its `PodApi` (dry-run stays default, owner flips it). One watchdog
  per provider via `GpuProvider`; the existing RunPod bean is unchanged.
- Failure paths (step failed, cancelled, control plane restart) end in terminate or released lease -> watchdog terminate.

## 5. Start-up overlap
The weights download (~10 min) is the long pole. At **Short start** (before the writing steps) the workflow calls
`GpuBroker.warmUp(shortId)` as a best-effort activity: it acquires the pod (only if the wallet allows and the owner
enabled the flag) and returns at once; pictures/clips later call `ensure(shortId)` which reuses it and waits for
`/health` ready. If warm-up failed, `ensure` retries; if the Short is cancelled or ends, `release` terminates. The
clips' owner-approval gate (`auto-approve-inr`) stays. Warm-up is skipped for Shorts that will use Manual mode.

## 6. Provider-neutral weights store
Order inside the pod boot script: (a) provider volume mounted at `/workspace/weights` if it holds the files with the
right sizes (RunPod global volume `runpod storage` already holds the Wan weights: fast-boot **untested**, test only
with the owner's yes), (b) a Cloudflare R2 mirror (S3-compatible, zero egress; **idea only**, needs an owner-created
bucket, never required), (c) Hugging Face (primary, rule 9: Comfy-Org/Wan-2.2 fp8_scaled experts, umt5, VAE,
lightx2v 1022 LoRAs, Tongyi-MAI/Z-Image-Turbo). Each file is size-checked; the manifest lives in the worker, not in the
control plane. Google Drive never.

## 7. Flag and rollout
`projectv.dream.gpu-provider=modal|broker` (env `DREAM_GPU_PROVIDER`, default **`modal`**). With `modal` nothing
changes (same requests, same code paths). `broker` makes DreamService ask the broker for pictures/clips; this wiring
is **not built in this step** (needs the pod worker from the Salad branch adapted to RunPod; separate step after the
owner approves a first paid test, est. well under US$1).

## 8. Build plan
1. (done here) interface, models, `GpuBroker`, `GpuWallet`, `RunPodProvider`, stubs, config, flag, fake-provider tests, runbook.
2. Pod worker (`workers/pod/server.py`, adapted from Salad `server.py`) + boot script + RunPod create body.
3. DreamService/workflow wiring behind the flag; warm-up activity.
4. First paid RunPod test (owner yes, cap set), then Jarvislabs adapter, CloudRift (needs key), Vast/Shadeform later.

## Risks / open
- RunPod REST v2 create-pod body is taken from the fp8 runbook (MCP create-pod shape); verified only against fake
  servers, not live (no spend allowed).
- Hourly price/stock must always come from the live quote (rule 7); config holds only caps and time estimates.
- Community stock flaps: the chain falls to Secure (higher price) only if the wallet still fits.

## Questions for the owner
1. Per-provider monthly caps to start with (suggest runpod US$5, others 0)?
2. May the first paid RunPod test use the existing global volume (fast boot) or must it download from HF?
3. OK to wire DreamService behind the flag as the next step?
