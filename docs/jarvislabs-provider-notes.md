# JarvisLabs provider notes (22 Nov 2026)

Status: **adapter built on the Python SDK (owner chose "wrap jl SDK", 22 Nov 2026), unproven.** JarvisLabs documents no REST API, so
`JarvislabsProvider` calls `workers/jarvislabs_helper.py` (one JSON command per process: gpus, list, get, create, destroy; the key is passed
only in that process's `JL_API_KEY`). Run needs `uv` (the default command is `uv run --with jarvislabs python`, override with
`JARVISLABS_COMMAND`). `proven=false`, monthly cap 0 in `config/providers.yaml`: raise the cap only when the owner approves the canary.
The adapter and helper were written from the SDK docs and `client.py`/`instances.py` source and tested against a fake helper only.

Sources read (docs.jarvislabs.ai overview, CLI, Python SDK, getting started, FAQs, legacy API; jarvislabs.ai/pricing; the GitHub README of
jarvislabsai/jarvislabs). Nothing was called against the live API (no key was used).

## What is documented
- **Control surfaces are the `jl` CLI and the `jarvislabs` Python SDK**, not a REST reference. The old `jlclient` is deprecated.
- **Auth:** API key from jarvislabs.ai/settings/api-keys; SDK/CLI read `JL_API_KEY` (our `.env` name is `JARVISLABS_API_KEY`).
- **Create** (`jl create` / `client.instances.create`): gpu_type, num_gpus, template (default `pytorch`; `vm` = full VM), storage (GB, default 40),
  name (max 40 chars), region (`IN1` or `IN2`), http_ports, script_id + script_args, disk_type.
- **Pause / resume / destroy:** `pause(machine_id)`, `resume(...)` (region-locked, **may return a new machine_id**), `destroy(machine_id)`.
  Paused instances bill storage only ($0.00014/GB/h). Destroy is the only way to stop all billing: the control plane must destroy, never pause.
- **Startup script without SSH:** scripts are uploaded (`scripts.add`) and attached with `script_id`; they run on create and on resume.
- **Ports:** `--http-ports`, and the docs say a service on port 6006 is reachable through the instance's API endpoint, public and
  outside the password. The instance object has `endpoints`, `url`, `public_ip` (VMs). The URL pattern is not documented.
- **Billing:** per second. Credits from $5, non-refundable. Zero balance pauses instances and can delete their data.

## Prices, USD per hour, on-demand / spot (jarvislabs.ai/pricing, 22 Nov 2026; the page does not say if this is per GPU)
H200 SXM 4.59 / 2.29. H100 SXM 3.49 / 1.54. RTX Pro 6000 Blackwell 2.19 / 1.15 (IN1). A100 80GB 1.49 / 0.89. A100 40GB 0.89 / 0.79.
A30 0.41 / 0.29. L4 0.44 / 0.29. **No RTX 4090, L40S or A6000 is listed**, so our cheapest-24GB-class choice would be L4 (24 GB, too
small for fp8 Wan plus text encoder headroom? unverified) or A100 40GB at $0.89, which is dearer than RunPod/Vast 4090s. Prices must
come live from `jl gpus` / `gpu_availability()` (rule 7); these are for the owner's orientation only. (An earlier note said "A100 40GB Rs84/h".)

## Live read-only check (22 Nov 2026, SDK `jarvislabs` 0.2.19, key in the child env only; no instance created, nothing rented)
- **The account bills in INR.** `jl gpus` prices are rupees per hour; `account.currency()` says "INR"; balance read as 500.0 (INR, grants 0).
  The first adapter treated them as USD: fixed (helper returns `currency`, Java converts with `inrPerUsd` 94 in `config/providers.yaml`).
  GST is not stated in the list prices: unverified (E2E adds 18% on top; if Jarvislabs does too, the real cost is 18% higher).
- Live rows (container, per GPU, Rs/h = USD at 94): A30 38.88 = $0.41 (2 free, IN2); L4 41.31 = $0.44 (7 free, IN2); **A100 40GB 84.24 = $0.90
  (2 free, IN2)**; A100-80GB 140.94 = $1.50 (0 free); RTX-PRO6000 96GB 207.36 = $2.21 (1 free, IN1); H200 434.97 = $4.63. H100 listed for VM only.
  Prices match the pricing page, so the page's figures are per GPU.
- Templates on the account: pytorch, fastai, automatic, axolotl, comfyui, fooocus, ollama, tensorflow, vm, ... **No custom Docker image field exists**
  in `instances.create` (only template, script_id, script_args, arguments, http_ports, fs_id). So our warm image `projectv-pod:comfy-v0.45.0`
  cannot be used: a container boots a stock template and the startup script must install ComfyUI + models itself (cold start, like the
  29-minute RunPod path), unless models sit on an attached persistent filesystem (`fs_id`) or the home folder (only `<home>` survives resume; destroy deletes it).
- Reachability (SDK source + skill doc): container `endpoints[0]` is an HTTPS URL for port 6006, public; more via `http_ports` "7860,8080" in order.
  Our worker must listen on 6006 (done via `SV_PORT`). VMs have no HTTPS URLs (SSH only): not usable, so the adapter is container-only.
- No SSH needed; the account key never goes on the node (the script holds worker env only). Startup scripts are stored on the account: the helper
  removes ours after destroy. Spot (`is_spot`) exists for containers, not used.
- Still unverified: whether the pytorch template has enough disk/CUDA for our worker without the warm image; how long the cold install takes;
  startup-script size limit; endpoint URL pattern/auth; billing between create and Running; whether `destroy` is synchronous; GST.
- Cost reality: cold boot at A100 Rs 84/h (~$0.90) vs RunPod/Vast 4090 warm boots. Auto only picks it when it is the cheapest live price including
  nothing for boot time, so expect it to be chosen over a 4090 only when 4090s are out of stock; the canary will show the real boot time.

## NOT documented (earlier note; items 1 and part of 2 and 4 are answered above) (would need a live, key-holding test)
1. **Any REST endpoints, base URL, auth header and JSON field names** for list / create / status / destroy. The only HTTP base URL in the docs is the
   token-priced model API (`models.jarvislabs.net/v1`), which is not instance control.
2. **Custom container image or env vars at create.** None found. Our worker ships as a boot payload in env vars (`SV_BOOT_*`); here it would
   have to travel as a startup script instead, and the script size limit is unknown (E2E's start-script limit bit us at 2048 chars).
3. Whether the **template/container** instance can run our worker at all: templates are container-based and "Docker cannot run inside them";
   a **VM** (`--vm`) supports Docker but its startup-script and public-port behaviour is not documented.
4. How the port-6006 endpoint authenticates and what its URL is (needed to reach `/health` with our per-rental bearer token).
5. Rate limits, instance quotas, GPU availability per region at the moment of a rental, whether `destroy` is synchronous.
6. Whether the guard (`PodSelfStop.wrapExit`, the worker ends itself, control plane destroys) can end a template instance's process
   without leaving it billing: a stopped process does not stop an instance.

## The safety design it would need (same as Vast/E2E)
Per-rental bearer token in `SV_TOKEN`; no account key on the node (startup script holds worker env only, root-only); `terminate()` = destroy;
`listPods()` filtered by the `projectv-` name prefix (watchdog); `monthlyCapUsd` in `config/providers.yaml` (now 0); `JARVISLABS_API_KEY` in `.env`.
Resume changing the machine id means a paused instance could be lost to the watchdog: never pause, only destroy.

## Options considered (option 1 was chosen)
1. **Wrap the `jl` CLI / Python SDK** (recommended if Jarvislabs is wanted): a small Python helper called by the control plane (or a tiny
   Python sidecar with the same list/create/destroy contract). Cost: one more process on the laptop, but the SDK is the supported surface.
2. **Get the REST contract** from JarvisLabs support (or capture it from the `jarvislabs` package source, which is on GitHub), then write
   `JarvislabsProvider` like `E2eProvider`. Read-only first: `list GPUs`, `list instances`, with the key, to confirm shapes before any create.
3. **Skip Jarvislabs for now.** Its GPU range (no 4090/L40S/A6000) is dearer than RunPod/Vast/E2E for our job, so the win is small.

Either way the canary (`POST /api/gpu/broker/providers/jarvislabs/canary?approve=true`) must pass before Auto can use it.
