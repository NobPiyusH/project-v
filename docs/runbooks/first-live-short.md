# First live Short: "What if the Moon were 10 times closer?" (runbook, prepared 25 Nov 2026)

Status: **prepared, nothing spent, nothing rented.** The plan file, its tests and a free rehearsal exist; the live run needs the owner's yes and a figure
(section 8). All prices below are ESTIMATES from earlier measurements (`docs/pod-tests-24nov.md`, `docs/film4k.md`), not quotes: read the live price first (CLAUDE.md rule 7).

## 1. What was prepared

| Thing | Where |
|---|---|
| The plan (video plan, one-location bank, 3 timed beats, narration, camera presets, resolved prompts) | `docs/shorts/moon-10x-closer-plan.json` |
| Test that the plan loads, validates (`bank.validate`), pastes the bank verbatim, uses legal Wan sizes and tested camera presets, and goes through the real `ShotStage` | `studio/tests/test_moon_plan.py` |
| Free rehearsal of the studio on live Temporal (no keys, no pod) | section 2 |

The idea: 15 s vertical, 3 beats of 5 s, 720x1280, 81 frames at 16 fps (5.06 s per beat), style "realistic cinematic night". Camera: `zoom_in`, `pan_left`, `zoom_in`
(the two presets judged good by eye on 24 Nov). No characters; one location, `moon-town`, whose text is pasted word for word into every beat prompt.

## 2. Free rehearsal: how far the Python studio got (live Temporal, no keys, no pod)

Setup used: a throwaway Postgres database, every API key blank (so every paid call fails closed), `DREAM_GPU_PROVIDER=modal` (no pod can be rented),
a separate Temporal namespace (`TEMPORAL_NAMESPACE=rehearsal`, see bug 1 below), API + control worker + media worker started from a worktree.

| Stage | Result |
|---|---|
| PLAN | **Works on live Temporal for the first time**: rule-based plan (short, 15 s, 9:16, `shorts_punchy`) after the duration fix (bug 2). Workflow, activity, DB and API all ran. |
| RESEARCH | Fails closed: `GEMINI_API_KEY is not set` (the stage then waits for the owner: "Approve = continue without it" worked). |
| CONCEPT | Fails closed: no LLM key anywhere in the text chain. |
| SCRIPT, NARRATION, SHOT_PLAN | `No concept yet` / `No script yet` (correct: they depend on the LLM stages). |
| MUSIC | Fails closed: no Gemini key (Lyria). |
| KEYFRAMES | `No shots to render; run the shot plan first`. This is the first stage that would spend (images) and the first GPU stage. **Stop point.** |

So: the whole control path (API, workflow, stage modes, owner approve/skip, failure-waits-for-owner, DB records) is proven live. Everything from CONCEPT on needs
paid LLM keys; everything from KEYFRAMES on needs a pod. Not exercised at all: any media activity that succeeds, the clip batch, ANIMATIC, ASSEMBLY, the quality-gate hook.

### Bugs found and fixed (tests included, same commit)

1. **Studio could not be isolated from the live workers.** Queue names are fixed (`projectv-control-py`, `projectv-media`) and the studio ignored `TEMPORAL_NAMESPACE`,
   so the owner's running workers (with real keys) picked up my rehearsal's activities (and failed them against the live DB; nothing was spent: no cost rows, the
   workflow was terminated). Now `TEMPORAL_NAMESPACE` is honoured by the API, the gateway and the control worker (the media worker already did). `studio/tests/test_worker.py`.
2. **Rule-based planner ignored "15-second"** (hyphen) and fell back to the preset's 45 s. `studio/planner/planner.py`, `studio/tests/test_planner.py`.
3. **Warm-image / volume / image env names were wrong in the Python port.** The wiring read `GPU_WARM_IMAGE`, `GPU_POD_VOLUME_ID`, `GPU_POD_IMAGE`, but `.env.example`, the docs and the
   Java app use `POD_WARM_IMAGE`, `POD_VOLUME_ID`, `POD_IMAGE`: the owner's warm image would have been silently ignored (boot about 29 min instead of 8-18). Now both names are read, POD_* first.
   `studio/api/wiring.py`, `studio/tests/test_dream_gpu.py`.

### Observations, not fixed (decide before the live run)

- A deterministic precondition error (`No shots to render`, `No concept yet`) is retried by Temporal up to 10 times with backoff (about 6 minutes) before the stage waits for the owner. Harmless, slow feedback.
- On 25 Nov the owner's machine had ONE API and ONE media worker but TWO `studio.worker` processes listening on `projectv-control-py` (two launcher/child pairs). Two control workers share the queue, so an activity can run on either; harmless for the same code and DB, but stop one before a live run (`tools\ops\start_studio.ps1` is meant to be idempotent).

## 3. Three things that will surprise you on the live run (read first)

1. **The studio's GPU route cannot do the camera presets.** Studio clips on a pod (`VIDEO_PROVIDER_DRAFT=broker`) run the pod worker's Wan 2.2 + **SVI** graph
   (`workers/pod/server.py`). Fun Camera (`zoom_in`, `pan_left`) exists only in `tools/film4k/camera_graphs.py`, used by the one-off script
   `E:\project-data\outputs\camera-scene\run.py` (outside git). The shot's `cameraMove` is only a word in the prompt in the studio. So the Moon plan's presets are honoured
   by **Route B**, not by **Route A** (section 4).
2. **The studio times shots from the narration, not from the plan's 5 s beats.** `ShotStage` starts each shot at its narration line and fits the rest; with realistic line lengths the stored
   shots come out about 2.7 / 3.9 / 8.4 s (total kept at 15 s). A 5.06 s clip stretched to 8.4 s is a hold or slow-down: judge it by eye. The plan's per-beat `startSec/durationSec` are checked by the validator and used by nothing else yet.
3. **There is no "load this plan" entry point.** The Planner, Writer and Director (paid LLMs) write their own plan from the prompt. To steer them, put the beats, the narration lines and the camera words in the prompt (section 5). Importing the JSON directly into the DB would be a small, free feature; it is not built.

## 4. Two routes: pick one (owner's call)

| | Route A: the studio (Temporal, broker, gate) | Route B: the camera-scene script (Fun Camera) |
|---|---|---|
| Proves | the Python studio end to end (HIGH risk item in `docs/DEVELOPER-TAKEOVER.md`) | the Moon look with real camera moves |
| Clips | Wan 2.2 + SVI, 9:16 drafts 480x832, finals 720p; no camera control | Wan 2.2 Fun Camera, 720x1280, 81 frames, 3 chained beats (last frame to next beat) |
| Spend beyond the pod | LLM calls (plan, concept, script, shot plan), narration, music, keyframes on the pod | one keyframe (about $0.03 on Seedream) or a local image; no LLM, no audio |
| Quality gate | runs after ASSEMBLY (`quality_gate_check`) | run by hand (section 7) |
| Code state | never run on a pod; first real use of the broker for video | `run.py` ran once (24 Nov, $0.32 est., 3 beats); needs 4 edits (section 6) |

Recommendation: **Route B first** (about $0.6-1.0, one pod, the camera moves are the point of this Short), then Route A as its own approved test. If the owner wants a single run, Route A is the one that tests the studio.

## 5. Route A: the studio

Settings (put in `.env`; never print it; restart the control plane and both workers afterwards):

```
DREAM_GPU_PROVIDER=broker          # the broker itself
VIDEO_PROVIDER_DRAFT=broker        # clips on a rented pod (default is wan = Modal, which is capped at 0 until 1 Dec 2026)
VIDEO_PROVIDER_FINAL=broker
VIDEO_PROVIDER_KEYFRAMES=broker    # FLUX.2 klein on the same pod; default would use Gemini images (paid) or Modal
VIDEO_GPU_IDLE_MINUTES=5
VIDEO_SHOT_BANK=true               # default; the bank is pasted into every shot
QUALITY_GATE_ENABLED=1             # default
PROJECTV_VIDEO_AUTO_APPROVE_INR=0  # default: a clip batch always waits for you to approve its estimate
POD_WARM_IMAGE=ghcr.io/nobpiyush/projectv-pod:comfy-v0.45.0   # as in docs/handoffs/2026-11-22-cto-handover-2.md; blank = plain image, slower boot
GPU_WATCHDOG_DRY_RUN=false         # the watchdog really terminates pods past their limits
GPU_MAX_PODS=1
GPU_MAX_HOURLY_USD=1.0             # refuse any pod dearer than this (the Secure 4090 was $0.89/h on 24 Nov)
GPU_MAX_RUNTIME_MINUTES=60
GPU_IDLE_MINUTES=10
RUNPOD_API_KEY=...                 # already in .env
```

Cap fields (`config/providers.yaml`, `pricing.gpuBroker`): `dailyCapUsd` is **10.00 today** ("while testing", 22 Nov) although the owner's standing GPU cap is **$3.50/day**; `totalCapUsd: 20`;
`providers.runpod.monthlyCapUsd: 12`. For this run set `dailyCapUsd: 3.50` (a config change: owner's decision). Every rental also reserves against these caps before it starts.
The Gemini monthly cap (`monthlyCapInr: 1000`) and the AICredits cap (Rs 150) apply to LLM spend; the OpenAI key (script writer) has no per-video cap in config: keep its key balance small.

Start everything (after the owner's yes):

```powershell
powershell -ExecutionPolicy Bypass -File tools\ops\start_studio.ps1      # Docker, Postgres 5433, Temporal 7233, SeaweedFS 8333, API :8080, workers
curl.exe -s http://127.0.0.1:8080/api/system                             # planner shows "claude/gemini ..." when keys are loaded
curl.exe -s http://127.0.0.1:8080/api/gpu/broker/options                 # which providers the broker would use, caps, why one is skipped
```

Independent kill-timer (rule 8; the studio's own watchdog is the second layer, this is the third). In a separate terminal for the whole run:

```powershell
# dry-run first: prints WOULD TERMINATE lines only
powershell -File tools\podkit\reaper.ps1
# real: terminates RunPod pods named projectv-* with no lease for 15 min, running over 3 h, or past the $3.50/day estimate. Run it every 10 minutes while the pod exists:
powershell -File tools\podkit\reaper.ps1 -Apply -MaxHours 1.5
```

Create the video (MANUAL so every stage waits for you; the prompt carries the beats because there is no plan import, section 3):

```powershell
$prompt = @'
What if the Moon were 10 times closer? A 15-second vertical Short for the channel Strange If True, realistic cinematic night, no people, no text.
Three 5-second beats. Beat 1: slow push-in on a quiet night coastal town with an oversized Moon; narration "Imagine the Moon were ten times closer."
Beat 2: pan left, the huge Moon rising, ocean tides surging against the shore; narration "Tides would climb far higher, flooding coasts twice a day."
Beat 3: zoom in on the cratered Moon surface filling the sky; narration "And every night, the sky would be wall to wall Moon."
Use exactly these three narration lines, one location (a quiet coastal town at night under a vast Moon), no characters.
'@
$body = @{ prompt = $prompt; defaultMode = "MANUAL" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/videos -ContentType 'application/json' -Body $body   # -> {id}
```

Walk the stages: `GET /api/videos/{id}` (stage runs with summaries), `POST /api/videos/{id}/stages/{STAGE}/approve` or `/reject` (with notes). Switch a stage to AUTO with
`PUT /api/videos/{id}/modes/{STAGE}`. Order and what each costs: PLAN, RESEARCH (the plan file says `needsResearch: false`, but the planner decides: if it asks for research and you do not want to pay for it, set that stage's mode and Approve/skip it), CONCEPT and SHOT_PLAN (Claude, paid), SCRIPT (OpenAI, paid),
NARRATION and MUSIC (Gemini TTS / Lyria; Google billing returned 403 on 24 Nov: expect these to fail until billing is fixed, then "Approve = continue without it" and add voice by hand), KEYFRAMES (pod), DRAFT_GEN (**pod: stops at an estimate; read it, compare with the figure the owner approved, then approve**), ASSEMBLY (laptop ffmpeg + gate).
At the DRAFT_GEN estimate: if it is above the approved figure, Reject; do not approve.

Where things land: assets and the master are in SeaweedFS bucket `projectv` (data on `E:\project-data`), listed per video by `GET /api/videos/{id}/production`; open one with
`GET /api/assets/{asset_id}/content`. The master is `videos/{id}/master/...`; the gate report is `videos/{id}/master/quality-gate.json` (its URI is in the ASSEMBLY stage summary).
Spend: `GET /api/videos/{id}` -> `costs`, `GET /api/budget`; the pod is booked as ONE CostEntry on the video when it is released. Pod state: `GET /api/gpu/pods`, `GET /api/gpu/pods/{id}/logs`, `GET /api/gpu/watchdog`.

## 6. Route B: the camera-scene script (Fun Camera moves)

`E:\project-data\outputs\camera-scene\run.py` already does this for another scene: Community then Secure RTX 4090 with a price ceiling, lease (`tools/gpu_lease/lease.py keep`) plus kill-timer
(`tools/bakeoff/killer.py`) started before any work, a 9-minute no-boot-log abort, a hard USD cap with the pod reserved at ceiling price x deadline, terminate in `finally`.
It refuses to rent if the studio database (lease) is unreachable. Edits needed for the Moon (do them on a COPY, in a new folder, e.g. `E:\project-data\outputs\moon-short\`):

1. `CAP_USD = 1.00` (the figure the owner approves); keep the deadline logic.
2. `PRESETS = ["zoom_in", "pan_left", "zoom_in"]` (already) and `PROMPTS`: use the three `motionPrompt` values from `docs/shorts/moon-10x-closer-plan.json` -> `resolvedPrompts` (they contain the location text word for word).
3. Keyframe: replace `scene02_720x1280.png` / `scene02_576x1024.png` in `in\` with ONE Moon keyframe (720x1280, a quiet night town under an oversized Moon). Make it with an approved image source (Seedream v4 about $0.027, or the studio's FLUX route); look at it before any GPU is rented.
4. Pod name `projectv-camscene` -> `projectv-moon-maxh=1` so the reaper's wall-clock rule is 1 h for this pod.

Run: `python run.py --dry` (prints the plan, spends nothing) and only then `python run.py --secure` (or without `--secure` to try Community first, cheaper but some hosts are bad).
Outputs: `...\moon-short\out\` (`log.txt`, `spend.json`, per-beat clips, chained last frames); joining the three beats and adding the narration is done locally with ffmpeg
(`tools/film4k/edit.py` / `assemble.py` for the cut; no audio in this route: narration by hand, ElevenLabs free plan is non-commercial: private upload only).

## 7. Reading the quality-gate report

Studio: stage ASSEMBLY, then the hook (`workers/activities/gate_check.py`): `fail` holds the stage (Approve = continue without the gate, Reject = redo the edit), `warn` only logs.
By hand (free, either route):

```powershell
uv run python -m tools.qualitygate E:\project-data\outputs\moon-short\out\moon.mp4 --shots auto --judge off
```

Per check: pass / warn / fail with a value and the `where` seconds. What matters for this Short:
- `independent_motion` below 1.5 = fail (a panned or zoomed still), below 4.5 = warn (look at it by eye: a slow zoom of a flat picture lands here). The camera-scene clip scored 10.2.
- `color_drift` above 0.9 = warn (the "blue wing" problem: a night scene can drift blue; check the Moon stays silver).
- `motion` below 0.25 = near-static; `sharpness`, `flicker` as in `docs/quality-gate.md`. Loudness: a silent clip skips it; with narration the target is about -14 LUFS.
- Thresholds are calibrated on very few clips: a warn is "look at it", not a verdict. The owner's eye decides.

## 8. Cost estimate (ESTIMATE, not a quote) and what the owner must approve

| Item | Estimate |
|---|---|
| Pod boot (weights, ComfyUI): about 10 min, about 8-18 with the warm image | about $0.15 at $0.89/h Secure 4090 (about $0.06-0.09 Community $0.34-0.55/h) |
| 3 beats at 720x1280 x 81 frames: about 3.6 min each, about 11 min | about $0.16 |
| Safety margin: one retry of a beat, a second boot attempt after a bad Community host | about $0.15-0.45 |
| Keyframe (Seedream v4) | about $0.03 |
| Route B total | about **$0.6-1.0** with the margin (the 24 Nov camera scene was about $0.32 without margin, bill not yet posted) |
| Route A extra: Claude (concept, shots), OpenAI (script), keyframes on the pod, narration/music | LLM part about Rs 10-30 (estimate from earlier Shorts); Lyria about Rs 10 if billing works |

Checklist, each needs the owner's own yes in chat (CLAUDE.md rule 2):
- [ ] Which route: A (studio, no camera presets), B (Fun Camera), or both as two runs.
- [ ] A spend figure for the GPU (suggested hard cap **$1.00** for one Route B run; $1.00 for the pod of Route A plus the LLM figure below). The $3.50/day GPU cap stays; today's RunPod balance was $4.73 on 25 Nov: re-read it.
- [ ] LLM / API spend for Route A (suggest Rs 50 total) and which keys may be used (Gemini, Claude, OpenAI, ElevenLabs). Google billing is still failing (403) so Gemini TTS and Lyria may not work.
- [ ] The keyframe source for Route B (Seedream $0.027 each; or FLUX on the pod).
- [ ] `config/providers.yaml` `dailyCapUsd` 10.00 -> 3.50 (and `GPU_MAX_HOURLY_USD`, `GPU_WATCHDOG_DRY_RUN=false`).
- [ ] That the reaper may run with `-Apply` during the run.
- [ ] Narration: ElevenLabs free plan is non-commercial (Private upload only) or the paid plan first.
- [ ] Publishing: none in this run. Upload Private only, after he has watched it.

## 9. Terminating pods and rollback

Normal end: the broker (Route A) or `run.py` (Route B) terminates the pod and books the cost. Check after every run (read-only):
the RunPod MCP `list-pods` must be empty; `GET /api/gpu/pods`; the `gpu_pod_lease` table has `released_at` set.

If a pod is left running (critical bug, rule 8):
1. Terminate by hand: RunPod console, or `delete-pod` in the RunPod MCP, or `powershell -File tools\podkit\reaper.ps1 -Apply`, or the kill-timer script `python tools\bakeoff\killer.py <pod_id> 1 <state.json>` (deadline 1 s).
2. Release the lease so the watchdog stops watching: `python tools\gpu_lease\lease.py release runpod <pod_id>`.
3. Note the minutes: the ledger is an estimate; the RunPod bill (`list-billing`) lags a few hours.

Rollback of settings: put `DREAM_GPU_PROVIDER=modal`, `VIDEO_PROVIDER_DRAFT=wan`, `VIDEO_PROVIDER_FINAL=omni`, `VIDEO_PROVIDER_KEYFRAMES=default` back and restart; nothing can then rent a pod.
A video stuck in a stage: `POST /api/videos/{id}/pause`; to cancel the workflow terminate `video-{id}` in the Temporal UI (http://127.0.0.1:8233). Code rollback: this change set is one branch, revert the commit; the migration set is unchanged.

## 10. Rehearse it yourself (free, no keys, no pod)

```powershell
# separate Temporal namespace so the live workers never see it. Register once (Temporal Python client: workflow_service.register_namespace(RegisterNamespaceRequest(namespace="rehearsal", workflow_execution_retention_period=Duration(seconds=259200))))
$env:TEMPORAL_NAMESPACE = "rehearsal"; $env:POSTGRES_DB = "projectv_dryrun"   # create that database first
# blank every key in the shell, then start: uv run --group studio python -m studio ; ... -m studio.worker ; ... -m workers.main (with an empty .env in the folder so the real one is not found)
```

Expect PLAN to pass and RESEARCH to fail with "GEMINI_API_KEY is not set" (section 2).
