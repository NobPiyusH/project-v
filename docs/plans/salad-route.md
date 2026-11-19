# Salad route for Dream Shorts (design decisions, 20 Nov 2026)

Status: built behind a flag, **off by default**. No real Salad container group has been created yet.
Why: the owner stopped Modal use until 1 Dec 2026 (`modal.monthlyCapUsd` is left as it is) and wants SaladCloud
GPU containers for Dream Shorts. The fp8 Wan 2.2 I2V A14B route was proven on one 24 GB RTX 4090 on 20 Nov
(about 76 s per 81-frame 480x832 clip, peak 24.0 GB, the owner could not tell it apart from Modal).

## Scope
- In: Dream Shorts **pictures** (Z-Image Turbo) and **clips** (Wan 2.2 I2V A14B fp8 + lightx2v 4-step LoRAs).
- Out: SeedVR2 4K upscaling (needs 48 GB+), long-form videos, music, finish (stays on the laptop).
- Flag: `projectv.dream.gpu-provider=modal|salad` (env `DREAM_GPU_PROVIDER`), default `modal`. With `modal`
  nothing changes: same requests, same Modal code paths.

## Container worker (`workers/salad/server.py`)
- One self-contained Python file (stdlib HTTP server + ComfyUI + diffusers). No private registry: it runs on the
  public image `pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime` (CUDA 12.8 covers both RTX 5090 and 4090).
- Delivery: the control plane zlib-compresses + base64-encodes the file and splits it into env vars
  `SV_BOOT_00..NN` (Salad caps each env value at 1000 chars). `container.command` is a short `python -c` that joins,
  decodes and `exec`s it. So the worker the group runs is always the one in this repo, no image build.
- At start: pip installs what is missing, fetches ComfyUI `v0.45.0` (tarball, no git), downloads weights from
  Hugging Face (Apache-2.0, rule 9) into the container disk with size checks, starts ComfyUI on 127.0.0.1.
  Weights: Comfy-Org/Wan-2.2 fp8_scaled high/low experts, umt5 fp16 text encoder, Wan 2.1 VAE, lightx2v 1022
  rank-64 LoRAs (~42 GB) + Tongyi-MAI/Z-Image-Turbo (diffusers). Disk: 120 GB.
- HTTP on `[::]:8000` (IPv6, Container Gateway), gateway `auth: true` (callers send `Salad-Api-Key`).
  - `GET /health`: 200 `{"ready":true}` once weights + ComfyUI are up, else 503 with the boot phase.
  - `POST /picture` `{prompt, seed, width, height}` -> job id; Z-Image settings copied from
    `workers/modal_apps/zimage.py` (9 steps, guidance 0, bf16).
  - `POST /clip` `{image (base64 PNG), prompt, negative, seed, frames, fps, width, height}` -> job id; the ComfyUI
    graph copied from the fp8 bench (4 steps, CFG 1, uni_pc_bh2, shift 3, 2+2 expert split, H.264 CRF 12).
  - `GET /jobs/{id}` state; `GET /jobs/{id}/result` the PNG/MP4 bytes.
  - Jobs are async because gateway timeouts are capped (100000, unit not stated) and a first clip can take minutes.
  - One GPU job at a time; Z-Image and the Wan experts never sit in VRAM together (each frees the other first).
- `--dry-run` builds the plan and graphs with no GPU; unit tests cover graph, chunking, job queue and routes.

## Lifecycle (`com.projectv.salad`)
- `SaladApi`: REST client (create/get/start/stop/delete group, list instances, list GPU classes with live prices).
  Key `SALAD_API_KEY`, `SALAD_ORG`, `SALAD_PROJECT` from `.env`; key never logged.
- `SaladSessions`: one group per Short, named `projectv-salad-<short8>-<rand4>`.
  - Created at the WRITE step (best effort) so the ~42 GB download overlaps the writing; created again on demand by
    the pictures/clips requests if missing.
  - Reused for every picture and clip of that Short.
  - Deleted (not stopped: a stopped group keeps nothing useful, disk is wiped on reallocation) when the Short
    completes, is cancelled, or a step fails to "waiting for owner". A Retry creates a fresh group.
  - Lease in `gpu_pod_lease` (provider `salad`, pod id = group name); heartbeat on every job poll.
- Watchdog: the existing `PodWatchdog` logic with a Salad `PodApi` adapter, its own limits
  (`projectv.salad.*`: max lifetime 90 min, idle 15 min, lease grace 10 min, max 1 group, max $1/h), only groups
  starting with `projectv-salad-`, **dry-run by default**. Runs on the same schedule as the RunPod one.
- GPU class: first available of `projectv.salad.gpu-classes` (default `RTX 5090,RTX 4090`), priority from config
  (default `high`: not preempted by other workloads; node loss can still reallocate).

## Failure handling (rule 2b)
- Preemption / reallocation: the new instance re-downloads and the job id is unknown there; the laptop client
  waits for `/health` ready again and resubmits that picture/clip (max 2 retries per item), then reports it failed.
- Group never ready within `projectv.salad.ready-timeout-minutes` (default 30): the activity fails, the step goes to
  FAILED_WAITING, the group is deleted. Retry starts a new one.
- Salad unavailable / no capacity / cap reached: the step fails to the owner. **Never** a silent fallback to Modal.

## Cost and caps (rules 6, 7)
- Prices are read live (`GET /organizations/{org}/gpu-classes`, the chosen class's price for the chosen priority).
  Config holds only time estimates (seconds per image / per 81 frames / start-up), never prices.
- Each picture/clip writes a CostEntry `salad`, seconds x live rate, `estimated=true`. On delete, the rest of the
  group's billed time (download, idle) is booked as one more estimated entry. Watchdog deletions book the lifetime.
- Each pictures/clips batch writes a RoutingDecision (provider `salad`, capability `image.dream` /
  `video.i2v.dream`).
- Budget bucket `salad`: `pricing.salad.monthlyCapUsd` (5) and `totalCapUsd` (5, the owner's whole credit, auto
  recharge off), both checked before creating a group and before each batch. The clips' owner-approval gate stays.

## Router plug-in
- `config/providers.yaml`: provider `salad` (kind `gpu_container`, capabilities `image.dream`, `video.i2v.dream`,
  quality 6, budget `salad`, `enabled: true`). The flag decides whether it is offered; the router still checks
  enabled / owner switch / cooldown / budget and records the decision.
