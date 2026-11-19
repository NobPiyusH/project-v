# Salad route runbook (Dream Shorts pictures + Wan clips on SaladCloud)

For the coordinator. Design: `docs/plans/salad-route.md`. Status 20 Nov 2026: built and tested against a fake Salad
server and fake ComfyUI; **no real group has ever been created**. Nothing here runs until the flag is on.

## What it spends
- Hourly rate is read live: RTX 5090 high ≈ $0.60/h (20 Nov), RTX 4090 high ≈ $0.44/h. Priority `high` by default
  (`SALAD_PRIORITY`: high / medium / low / batch; lower tiers are cheaper but preemptible).
- A group is billed while the instance runs, including the weight download. Estimate for the first test:
  start-up + ~42 GB download ≈ 10-20 min, one picture ≈ 10 s, one clip ≈ 1-2 min, so about **0.3-0.4 h ≈ $0.15-0.25**
  (≈ ₹15-25). The hard ceiling is the owner's credit: `pricing.salad.monthlyCapUsd` and `totalCapUsd` in
  `config/providers.yaml` are 5 / 5. Every job and the rest of the group's time are written as CostEntry `salad`.
- Modal stays untouched (`modal.monthlyCapUsd` is not changed by this work; nothing falls back to Modal).

## Enable (all in the git-ignored `.env`, never printed)
Keys `SALAD_API_KEY`, `SALAD_ORG=tataconsultancy`, `SALAD_PROJECT=default` are already there. Add:
```
DREAM_GPU_PROVIDER=salad
SALAD_WATCHDOG_DRY_RUN=false      # recommended for the first real run: lets the watchdog actually delete a forgotten group
```
Then restart the control plane (the owner does this; the coordinator must not stop the running one without asking).
Check `GET http://127.0.0.1:8080/api/gpu/salad` -> `enabled:true, configured:true`, `lastSweep` fills in within ~3 min,
`dryRun:false`. To turn the route off again: `DREAM_GPU_PROVIDER=modal` and restart.

## Exact first real test (one group, one picture, one clip, then delete)
Use the smallest possible Short so only one picture and one clip are made: create a Dream Short with **1 shot**
(shot count 1), look `lonely`, quality `480p`. Leave Pictures and Clips on Review/Manual so nothing runs on by itself.
1. Create the Short. The Write step starts the Salad group ("warm-up", log line `Salad group projectv-salad-xxxxxxxx-yyyy created`).
   Open https://portal.salad.com -> project `default` -> container groups: the group should be there, state allocating/running.
2. Pictures step: the worker waits for `/health` (the dashboard shows the step running; the download takes 10-20 min the
   first time). Expect 1 picture. Check the PNG in the dashboard.
3. Clips step: the cost card shows the Salad estimate; Approve. Expect 1 mp4 (~80 s of render). Watch it.
4. Finish the Short or press Cancel. Either deletes the group (log `Salad group ... deleted`).
5. If anything looks stuck, delete the group by hand in the Salad portal (the watchdog would also remove it within the
   limits: 15 min idle, 90 min lifetime, only groups named `projectv-salad-*`).

## Verify afterwards
- Portal: **no container group left** (project `default`), usage page shows the minutes billed; compare with the dashboard cost
  (≈ ₹ amounts of the CostEntry rows `salad` for that Short; they are estimates, the portal is the truth).
- Dashboard / DB: cost entries `salad` (one per job + one "not covered by jobs" row), a routing decision per batch
  (`DREAM_PICTURES`, `DREAM_CLIPS`, provider `salad`), assets with provider `salad`.
- `GET /api/gpu/watchdog` is RunPod's; `GET /api/gpu/salad` is Salad's. Leases in table `gpu_pod_lease` (provider `salad`) should all be released.

## If it fails
- Salad down / no capacity / over the cap: the step shows "Failed ... Retry when ready" (rule 2b), the group is deleted,
  nothing is sent to Modal. Retry creates a fresh group (and downloads again).
- Instance preempted or lost: the worker waits for the new instance and resubmits that picture/clip (2 retries), costing a re-download.
- Boot failure (e.g. a Hugging Face file unreachable): `/health` reports phase `failed` with the error; the step fails and the group is deleted.
- Known unknowns to read off the first real run (the docs were ambiguous): which fields the create call accepts
  (cpu/memory/disk limits), the gateway's real response-size limit (worker serves jobs async, MP4s are a few MB),
  whether the readiness probe path works as set, and real download speed.

## Hand-built image / command (only if the in-env boot is ever replaced)
Image: `pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime` (public). Command: `python -c "<join SV_BOOT_* env, zlib+base64 decode, exec>"`
(built by `com.projectv.salad.SaladBoot`; the payload is `workers/salad/server.py`). Port 8000, protocol http, auth on
(send header `Salad-Api-Key`), readiness probe `GET /health`.
