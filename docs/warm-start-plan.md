# Warm start for rented GPU pods (plan, 22 Nov 2026)

Goal: a pod ready in minutes, not ~29. Status: Phase 1 (boot timing) is built; the image (option A) is built behind a flag, default OFF.
Nothing here has been run on a real pod, pushed, or paid for.

## 1. Where the time goes (what we know, what we don't)

| Fact | Source |
|---|---|
| Weights: Wan 2.2 fp8 + text encoder + VAE + LoRAs = 41.6 GB; FLUX.2 klein 4B = 16.1 GB (first picture job); SVI LoRAs = 2.5 GB (first scene job). About **60 GB** in a full Short run. | sizes in `workers/pod/server.py` |
| HF download speed seen: ~114 MB/s (RunPod A6000, 14.3 GB in 126 s); bench 42 GB in 478 s = ~88 MB/s. So 60 GB is **~9-11.5 min**, whatever else we do, while weights come from HF. | handoff 20 Nov; `docs/svi-scenes.md` |
| Pip + ComfyUI + custom-node setup: ~3 min; SVI setup (KJNodes, restart): 2-3 min. | `docs/svi-scenes.md` |
| Last real test: ~29 min rent to first clip on a Vast 4090 (estimate was 15). Clips then ~71 s each. | owner/CTO brief |
| **Not known:** how the ~29 min splits between image pull, container start, pip, ComfyUI, and each download. | Phase 1 answers this |

Phase 1 now records, per rental, seconds since the rent for: provider says running, container (worker) started, deps installed, ComfyUI
fetched, weights done, ComfyUI up, ready; plus one row per download (GB, seconds, MB/s, source). It is logged
(`GPU boot timing ...`), stored in table `gpu_boot_timing` (V42) and returned as `boot` by `GET /api/dream/{id}/gpu`. **One real rental
tells us the split; do not tune further before that.**

## 2. Options

| | A. Image with ComfyUI + nodes + deps baked, weights from HF | B. Same + weights baked in | C. RunPod network volume with weights | D. RunPod template / cached image |
|---|---|---|---|---|
| Saves | pip + ComfyUI + KJNodes (~3-5 min, and a pip/GitHub failure point) | the above + the HF download, **but adds a ~60 GB pull** | the whole download after the first fill | nothing new: a template is just image + settings |
| Start estimate (rent to ready) | base-image pull + ~10 min weights = **~12-15 min** | pull of ~75 GB image (host-dependent, 8-20 min at 100-150 MB/s incl. unpack) = **no better than A**; faster only if the host already has it | base pull + volume mount + read: **~3-6 min** (not measured; the code path exists, untested) | same as A |
| Works on | RunPod, Vast (any OCI host) | same | **RunPod only**; volume is tied to one datacenter, and a pod must run in that DC | RunPod only |
| Monthly fixed | $0 (public GHCR image, no storage bill seen for public packages) | $0 public; Docker Hub free is 1 private repo / 2 GB, so private needs paid | 60 GB x $0.07 = **~$4.2/mo** (billed hourly, used or not; $0.07/GB first 1 TB) | $0 |
| One-off | $0 (free GitHub runner) | $0-few $ (needs a big builder) | the fill: one pod boot (~$0.3) | $0 |
| Risks | image drifts from the pinned ComfyUI (mitigated: tag in folder name, worker falls back); first rental on a host pays the full pull | huge image, slow on every cold host, registry layer/size limits unverified, every weight change = 60 GB rebuild + push | GPU choice shrinks to that DC's stock; Auto's cheapest-across-providers logic loses RunPod volume advantage on Vast; global volume has odd file rules (already handled in worker) | none |

Rejected: **B** (a bigger pull on every cold host buys little over HF's 88-114 MB/s, and costs rebuild pain); **D** (no benefit beyond A for our
flow; the broker already creates pods with an explicit image).

## 3. Recommendation: A now. C is the add-on only if A is not enough

**Build A**: one public image `ghcr.io/nobpiyush/projectv-pod:comfy-v0.45.0` (base = the current plain image, plus what `server.py --bake` installs).
- Expected start: **~12-15 min** vs 29 observed (the ~3-5 min setup disappears; the ~10 min weight download stays). Honest caveat: if Phase 1
  shows the 29 min is mostly image pull or HF slowness on Vast, A helps less than this.
- Monthly fixed: **$0**. One-off: **$0** (GitHub Actions free runner; for a private repo this uses the free minute allowance, unverified here).
- Risks: public image (fine: nothing private in it, and public means no registry credential on a Vast pod, which must hold no account secret);
  GHCR storage billing for containers is "currently free" per community sources, not guaranteed; the runner's 14 GB disk needs the cleanup step in the workflow.
- **A alone will not reach single-digit minutes.** For that, weights must be next to the GPU: C (~$4.2/mo, RunPod only). `POD_VOLUME_ID` already
  does this; nothing to build. Decide after the Phase 1 numbers, not before.

## 4. Building the image without the laptop's C: drive

| Place | Fit |
|---|---|
| **GitHub Actions** (`.github/workflows/pod-image.yml`, manual trigger) | **Chosen.** Free runner SSD is 14 GB total (docs; ~25-30 GB free after removing preinstalled tools, community figures); the workflow deletes dotnet/android first. Base image ~12 GB + ~2 GB deps fits, tightly. Pushes to GHCR with the built-in token. |
| Any Linux box with Docker (`docker/pod/build.sh`) | Fallback if the runner runs out of disk (a cheap rented CPU box for ~1 h, cents). Needs `docker login`. |
| Depot / paid builders | Only if GitHub's runner keeps failing; not needed for A. |

## 5. How the broker picks the image

`projectv.gpu.warm-image` (env `POD_WARM_IMAGE`) in `application.properties`; blank = **OFF**, the plain `projectv.gpu.pod-image` and today's boot
path run unchanged. When set, `DreamGpu.podImage()` hands it to every provider (RunPod and Vast both pull any public OCI image; no per-provider
image needed) and sets `SV_BAKED=/opt/projectv` so the worker uses the baked ComfyUI **only if its pinned tag is there**, else it fetches as before.
The canary and preflight use the same image, so the canary is the proof. The worker still ships through `SV_BOOT_*` env: worker edits need no rebuild;
only a ComfyUI tag, node or Python-dependency change does.
Not covered: Jarvislabs and E2E use their own start mechanisms (parked / unproven); they keep the plain path.

## 6. What the owner must do before a test build or rental

1. Approve the plan (A). 2. GitHub: allow the workflow to run, then run it once; set the package `projectv-pod` to **public** (Package settings). 3. Approve ONE canary
rental (RunPod or Vast, <$0.60) with `POD_WARM_IMAGE` set, to compare against Phase 1 numbers of a plain rental. 4. Optional: approve C (volume ~$4.2/mo, one fill rental).
Until then: flag OFF, no pushes, no spend.
