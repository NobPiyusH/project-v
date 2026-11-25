# First live Short: "What if the Moon were 10 times closer?" (runbook, prepared 25 Nov 2026, plan rewritten to the reference style the same day)

Status: **prepared, nothing spent, nothing rented.** The plan file, the studio code for it (plan import, camera presets on the pod route, plan timing, $3.50/day cap; section 3) and
a free rehearsal exist. The owner approved $2 for the first LIVE Moon Short (25 Nov 2026, "ok for 2$"); the live run itself is a separate step (section 8). All prices below are ESTIMATES from earlier measurements (`docs/pod-tests-24nov.md`, `docs/film4k.md`), not quotes: read the live price first (CLAUDE.md rule 7).

## 1. What was prepared

| Thing | Where |
|---|---|
| The plan (video plan, one-location bank, 2 timed beats, narration, camera presets, resolved prompts, `moon` layer settings and fallback prompts, caption) | `docs/shorts/moon-10x-closer-plan.json` |
| The Moon layer composite (pure command builder, scale curve; ffmpeg only runs by hand) and its tests | `tools/film4k/moon_layer.py`, `tools/film4k/tests/test_moon_layer.py` |
| Measurements of the five reference Shorts | `docs/shorts/moon-reference-analysis.md` |
| Test that the plan loads, validates (`bank.validate`), pastes the bank verbatim, uses legal Wan sizes and tested camera presets, and goes through the real `ShotStage` | `studio/tests/test_moon_plan.py` |
| Plan import: `POST /api/videos` with `planFile` / `plan`; plan-driven PLAN, CONCEPT, SCRIPT, SHOT_PLAN and per-beat timing | `studio/planner/imported.py`, `studio/api/video_routes.py`, `studio/production/shot_stage.py` |
| Camera presets on the pod route (Fun Camera, 720x1280, 81 frames, chains of 2) | `studio/production/camera.py`, `video_stage.py`, `workers/pod/server.py`, `workers/activities/clips.py` |
| Tests for all of it (fakes only) | `studio/tests/test_camera_and_plan_import.py`, `workers/tests/test_pod_camera.py` |
| Free rehearsal of the studio on live Temporal (no keys, no pod) | section 2 |

**The idea (reference style, rewritten 25 Nov 2026 after measuring the owner's five reference Shorts: `docs/shorts/moon-reference-analysis.md`).** ONE continuous 10 s vertical shot, not three scenes:
2 chained beats of 5 s (beat 2 starts from the last frame of beat 1: the "at most 2 chained" rule), 720x1280, 81 frames at 16 fps (5.06 s per beat), Fun Camera `slow_push` on both (a tested preset, speed 0.5: the references have a still or slowly drifting camera).
The plate is a real-looking night shore with waves rolling in (one location, `moon-shore`, text pasted word for word into both beat prompts, **no Moon in the plate prompts**).
**The Moon is the hero and is a separate layer (option b, primary):** one Moon picture composited over the whole 10 s timeline in code by `tools/film4k/moon_layer.py` (scale x1.0 to x2.2 with a smoothstep curve, rising from 62% to 36% of the frame height, soft glow underneath), so its growth is continuous across the beat seam and does not depend on the model. A short caption sits at the top for the first 4 s: "What if the Moon were 10x closer?". Narration: two lines, 25 words, about 10 s.
**Fallback (a), in the plan under `moon.fallback`:** if the layer looks fake, the model draws a large Moon inside the plate (the same beats with Moon prompts), no composite step.

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
| KEYFRAMES | `No shots to render; run the shot plan first`. This is the first stage that would spend (images) and the first GPU stage. **Stop point.** (Before 25 Nov: CONCEPT to SHOT_PLAN failed for lack of LLM keys. With a plan file those four stages need no key, so the rehearsal now reaches KEYFRAMES: section 10.) |

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

## 3. What was fixed on 25 Nov 2026, and what still surprises (read first)

The three blockers the first version of this runbook listed are fixed in code (tests: `studio/tests/test_camera_and_plan_import.py`, `workers/tests/test_pod_camera.py`); none of it has run on a real pod yet.

1. **Camera presets on the studio's pod route.** With `VIDEO_CAMERA_PRESETS=true` (the default) every clip of a **9:16** video that goes to the broker (`VIDEO_PROVIDER_DRAFT=broker`) runs the Wan 2.2 **Fun Camera** graph instead of SVI:
   720x1280, 81 frames, 16 fps, one take per shot (hero shots no longer get two takes: cost), mode `camera`, a preset per shot.
   - The preset is the shot's `camera_move` when it names a preset (`zoom_in`, `pan_left`, `pan_right`, `slow_push`, `drift_right` are TESTED; "Pan Left" / "pan-left" also match). Any other camera text (a model's "slow push-in") gets a varied TESTED preset from `plan_camera`, never the same twice in a row.
   - An **untested** preset (`pan_down`, `fast_push`, `zoom_out`, `pan_up`, `drift_left`, rolls) named by a plan file is **refused** (the plan is rejected at creation with a 400 naming the preset; the stage refuses it too) unless `VIDEO_CAMERA_ALLOW_UNTESTED=true` or the plan says `generation.allowUntested: true`. The pod worker refuses it as well unless `allowUntested` is sent.
   - **Chains of two**: the request is `chain=true, chainGroup=2`: beat 2 starts from the last frame of beat 1, beat 3 restarts from its own fresh keyframe (rule of the 25 Nov motion block: from the third chained beat the scene drifts blue and soft). Only for a whole batch of consecutive shots 1..n; a redo of single shots starts each from its own keyframe.
   - A camera clip is already 720x1280 and final quality: **FINAL_GEN makes no final pass** for camera shots unless you pick a shot for a redo.
   - The pod fetches the Fun Camera experts + LoRAs + text encoder (about 38 GB) on the first camera clip, like SVI: the estimate adds about 6 min of setup and about 3.7 min per beat (`CAMERA_SETUP_SECONDS`, `CAMERA_SECONDS_PER_81` in `studio/production/video_gpu.py`; measured on 24-25 Nov, an estimate not a quote). The warm image (`POD_WARM_IMAGE`) does **not** contain these weights yet, so the first camera clip downloads them (the volume copy is used when `POD_VOLUME_ID` is set).
   - Horizontal (16:9) videos and `VIDEO_CAMERA_PRESETS=false` keep the old SVI / plain route unchanged.
2. **Load a plan.** `POST /api/videos` accepts `planFile` (a `.json` inside `docs/shorts`, by name) or `plan` (the object). It is validated first (`studio/planner/imported.py`: bank, location ids, beat timings, camera presets, script lines on real beats); a bad plan is a 400 listing every problem and nothing is created.
   The plan is stored under `video.plan_json -> 'imported'`; the stages are the same (modes, approvals, redo) but for that video **PLAN, CONCEPT, SCRIPT and SHOT_PLAN cost nothing** (no planner/writer/director LLM call; the PLAN stage keeps the imported file next to the plan it writes). The Creative Director's shot-plan review is skipped too (it is a paid call that rewrites prompts in place). `prompt` is optional with a plan (the title is used as the label).
3. **Shot timing from the plan.** Shots take the plan's per-beat `startSec` / `durationSec` (5 s each, 10 s total) instead of being fitted to the narration; the narration lines keep the plan's own `startSec` (0.3 / 5.2 s) and may be shorter than their beat. The edit trims the 5.06 s clip to the 5 s beat. A video without a plan is timed from the narration as before.

Still surprising:
- **Nothing here has run on a pod.** The Fun Camera graph is the one that worked on 24-25 Nov (`tools/film4k/camera_graphs.py`); the pod worker has its own copy (frames saved as images, encoded by the existing encoder) and a test that fails when the two drift. First live use of: `/clip` with `camera`, `ensure_camera` downloads, chain groups on the pod, the broker for video. Watch the first beat's log.
- **Colour matching is not implemented.** Beat 3 restarts from its own FLUX keyframe, which is a fresh picture, not a colour-matched copy of beat 2's end. For one location with one text block pasted into every prompt this should look consistent; judge it by eye (and `quality-gate` `color_drift`).
- **KEYFRAMES make one FLUX picture per beat** (two pictures). Only the first is a real start picture: beat 2 starts from beat 1's last frame (chain), so the second picture is an unused extra of a few cents; it is kept because the stage makes one per shot, about 16 GB of FLUX weights on the pod the first time.
- A deterministic precondition error (`No shots to render`, `No concept yet`) is retried by Temporal up to 10 times (about 6 minutes) before the stage waits for the owner.
- Two `studio.worker` processes can listen on `projectv-control-py` (two launcher/child pairs); harmless for the same code, but stop one before a live run. After this change restart the control plane, both workers (`studio.worker`, `workers.main`) so they run the new code.

## 4. Routes: pick one (owner's call)

| | Route A: the studio (Temporal, broker, gate) | Route B: the camera-scene script (Fun Camera moves, fallback) |
|---|---|---|
| Proves | the Python studio end to end (HIGH risk item in `docs/DEVELOPER-TAKEOVER.md`) with real camera moves | the Moon look with real camera moves, no studio |
| Clips | Wan 2.2 Fun Camera, 720x1280, 81 frames, chains of 2 beats | the same graph, 3 chained beats (last frame to next beat; the "at most 2" rule is NOT in that script) |
| Spend beyond the pod | keyframes on the pod (FLUX); narration (Gemini TTS or ElevenLabs) and music (Lyria) if those stages are on; **no LLM for plan/concept/script/shots** | one keyframe (about $0.03 on Seedream) or a local image; no LLM, no audio |
| Quality gate | runs after ASSEMBLY (`quality_gate_check`) | run by hand (section 7) |
| Code state | the camera route has never run on a pod | `run.py` ran once (24 Nov, $0.32 est., 3 beats); needs 4 edits (section 6) |

Recommendation: **Route A** for the first live run (the owner approved the $2 for the first LIVE Moon Short): it is the Short and the studio test in one. Keep Route B as the fallback if the studio's pod route fails on the first beat.

## 5. Route A: the studio

Settings (put in `.env`; never print it; restart the control plane and both workers afterwards):

```
DREAM_GPU_PROVIDER=broker          # the broker itself
VIDEO_PROVIDER_DRAFT=broker        # clips on a rented pod (default is wan = Modal, which is capped at 0 until 1 Dec 2026)
VIDEO_PROVIDER_FINAL=broker        # camera shots make no final pass; this only keeps FINAL_GEN off Omni/Gemini
VIDEO_PROVIDER_KEYFRAMES=broker    # FLUX.2 klein on the same pod; default would use Gemini images (paid) or Modal
VIDEO_CAMERA_PRESETS=true          # default true: Fun Camera presets on 9:16 pod clips (this change)
VIDEO_CAMERA_ALLOW_UNTESTED=false  # default false: untested presets are refused
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

Cap fields (`config/providers.yaml`, `pricing.gpuBroker`): `dailyCapUsd` is **3.50** (set 25 Nov 2026, test `test_the_studio_daily_gpu_cap_is_three_fifty`); `totalCapUsd: 20`; `providers.runpod.monthlyCapUsd: 12`. Every rental reserves against these caps before it starts.
The Gemini monthly cap (`monthlyCapInr: 1000`) and the AICredits cap (Rs 150) apply to LLM spend; the plan stages need none of it.

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

Create the video from the plan file (MANUAL so every stage waits for you; no prompt needed, the plan carries the beats, camera presets and narration):

```powershell
$body = @{ planFile = "moon-10x-closer-plan.json"; defaultMode = "MANUAL" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/videos -ContentType 'application/json' -Body $body   # -> {id}; 400 with every problem when the plan is invalid
```

Walk the stages: `GET /api/videos/{id}` (stage runs with summaries), `POST /api/videos/{id}/stages/{STAGE}/approve` or `/reject` (with notes). Switch a stage to AUTO with
`PUT /api/videos/{id}/modes/{STAGE}`. Order and what each does with this plan:

| Stage | With the plan file |
|---|---|
| PLAN, CONCEPT, SCRIPT, SHOT_PLAN | free: built from the file (RESEARCH is not in the stage list: `needsResearch: false`). Check the SHOT_PLAN summary: 2 shots, 10 s, camera moves `slow_push / slow_push`; each shot prompt has the `moon-shore` text word for word and says "no Moon" (layer mode). |
| MUSIC | Lyria (paid, Gemini): Google billing returned 403 on 24 Nov; expect failure, then "Approve = continue without it" and add music by hand, or switch the stage's mode and skip it. |
| NARRATION | Gemini TTS or ElevenLabs (free plan: non-commercial, Private upload only); same 403 caveat for Gemini. Keep the plan's two lines as they are (25 words, about 10 s). |
| KEYFRAMES | **pod**: two FLUX.2 klein pictures (first GPU use, the pod boots here). The batch releases the pod when it is saved, so DRAFT_GEN boots a SECOND pod (see below). Look at the first picture before approving: a real-looking shore, an EMPTY sky, room above the horizon for the Moon. |
| DRAFT_GEN | **pod: stops at an estimate; read it, compare with the figure the owner approved, then approve.** 2 Fun Camera beats, one chain of two (`slow_push`). |
| FINAL_GEN | nothing to render for camera shots (pick a shot only to redo it). |
| ASSEMBLY | laptop ffmpeg + quality gate. **The studio does not composite the Moon layer yet**: the master from ASSEMBLY is the plate only. Composite by hand afterwards (free): section 5b. |

At the DRAFT_GEN estimate: if it is above the approved figure, Reject; do not approve.
**Two boots**: KEYFRAMES releases the pod as soon as its pictures are saved (code: `ShotStage.release_gpu`), so DRAFT_GEN rents again: two boots (8-18 min each with the warm image, plus the Fun Camera download on the second pod, about 6 min). The section 8 estimate includes both. Not built yet. **CTO decision (25 Nov 2026): keep ONE pod across KEYFRAMES and DRAFT_GEN** (one boot, lower cost), provided the lease, the kill-timer/reaper and the $2 cap guard stay armed for the whole span and the pod is terminated at the end or on any failure (rule 8). It needs a code change (hold the pod from KEYFRAMES until DRAFT_GEN is saved or fails; `ShotStage.release_gpu` currently releases after the keyframe batch) and its own tests; until then the two-boot behaviour above applies.

Where things land: assets and the master are in SeaweedFS bucket `projectv` (data on `E:\project-data`), listed per video by `GET /api/videos/{id}/production`; open one with
`GET /api/assets/{asset_id}/content`. The master is `videos/{id}/master/...`; the gate report is `videos/{id}/master/quality-gate.json` (its URI is in the ASSEMBLY stage summary).
Spend: `GET /api/videos/{id}` -> `costs`, `GET /api/budget`; the pod is booked as ONE CostEntry on the video when it is released. Pod state: `GET /api/gpu/pods`, `GET /api/gpu/pods/{id}/logs`, `GET /api/gpu/watchdog`.

## 5b. The Moon layer (by hand, free, after the plate exists)

1. **The Moon picture** (a lit disc with real craters on a black sky; the owner's decision, nothing downloaded or generated yet): either a public-domain full-Moon photograph (a download: ask the owner first, state file, source and size) or one extra FLUX picture on the pod ("full Moon, sharp craters, black background"; a few cents). Key it to alpha with `moon_matte_cmd` in `moon_layer.py` (luma key), or use a picture that already has alpha.
2. **Plate**: the ASSEMBLY master (or the two clips joined), 720x1280, 10 s. Composite, with the caption (this prints the command; drop `--dry` to run):

```powershell
uv run python tools/film4k/moon_layer.py E:\project-data\outputs\moon-short\plate.mp4 E:\project-data\outputs\moon-short\moon.png E:\project-data\outputs\moon-short\moon-layer.mp4 --caption "What if the Moon were 10x closer?" --dry
```

   The defaults equal the plan's `moon.layer` block. The caption uses `C:\Windows\Fonts\arialbd.ttf` (the helper's `FONT_WIN`).
3. Judge by eye: does the Moon read as part of the scene (glow, size, position), does the growth feel slow and natural? If it looks stuck-on, switch to fallback (a) (`moon.fallback.beatPrompts`: redo the two beats with the Moon in the plate), another round of the pod beats (about $0.35-0.55, estimate).
4. Narration and music: mix to about -14 LUFS, true peak under -1 dBTP (the five references sit at -13.5 to -14.2 LUFS).
5. Not built: wiring the composite into the studio's ASSEMBLY stage. The helper is a pure, tested command builder (checked on synthetic input only); the by-hand run above is its first use on real footage.

## 6. Route B: the camera-scene script (Fun Camera moves)

`E:\project-data\outputs\camera-scene\run.py` already does this for another scene: Community then Secure RTX 4090 with a price ceiling, lease (`tools/gpu_lease/lease.py keep`) plus kill-timer
(`tools/bakeoff/killer.py`) started before any work, a 9-minute no-boot-log abort, a hard USD cap with the pod reserved at ceiling price x deadline, terminate in `finally`.
It refuses to rent if the studio database (lease) is unreachable. Edits needed for the Moon (do them on a COPY, in a new folder, e.g. `E:\project-data\outputs\moon-short\`):

1. `CAP_USD = 1.00` (the figure the owner approves); keep the deadline logic.
2. `PRESETS = ["slow_push", "slow_push"]` and `PROMPTS`: use the two `motionPrompt` values from `docs/shorts/moon-10x-closer-plan.json` -> `resolvedPrompts` (they contain the location text word for word).
3. Keyframe: replace `scene02_720x1280.png` / `scene02_576x1024.png` in `in\` with ONE plate keyframe (720x1280, a real-looking night shore with an empty sky). Make it with an approved image source (Seedream v4 about $0.027, or the studio's FLUX route); look at it before any GPU is rented.
4. Pod name `projectv-camscene` -> `projectv-moon-maxh=1` so the reaper's wall-clock rule is 1 h for this pod.

Run: `python run.py --dry` (prints the plan, spends nothing) and only then `python run.py --secure` (or without `--secure` to try Community first, cheaper but some hosts are bad).
Outputs: `...\moon-short\out\` (`log.txt`, `spend.json`, per-beat clips, chained last frames); joining the two beats and adding the narration is done locally with ffmpeg
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

Expected quality-gate results for this plan (a prediction, not a measurement): no `fail`. A near-still camera with small waves as the only independent motion can land `independent_motion` in the warn band (1.5 to 4.5): look at it by eye (the references have a median frame difference of 1.8-5.0 of 255 even when the camera is still). `color_drift` should stay quiet with only two chained beats. `loudness` is skipped until narration is mixed in; the composited Moon is not in the ASSEMBLY master, so run the gate on the final composite too.

## 8. Cost estimate (ESTIMATE, not a quote) and what the owner must approve

The owner approved **$2** for the first live Moon Short (25 Nov 2026, his words "ok for 2$"); the daily cap is $3.50. Prices below are estimates from 24-25 Nov measurements at $0.89/h (Secure 4090); read the live price first (rule 7).

| Item (Route A) | Estimate |
|---|---|
| KEYFRAMES: pod boot (about 8-18 min with the warm image) + FLUX download (about 16 GB, first picture job) + 2 pictures | about $0.25-0.4 |
| DRAFT_GEN: a SECOND pod boot (KEYFRAMES released the first) + Fun Camera download (about 6 min) + 2 beats x about 3.7 min | about $0.35-0.55 |
| Safety margin: one retried beat, a bad host, a replacement pod (the client replaces a lost pod once) | about $0.25-0.5 |
| The Moon layer: composite on the laptop (ffmpeg) | $0 |
| Plan, concept, script, shot plan | $0 (plan file) |
| Narration / music | Gemini TTS and Lyria: billing returned 403 on 24 Nov; ElevenLabs free plan is non-commercial. Expect a manual voice. |
| Route A total (GPU), two boots as built today | about **$0.85-1.45** (ESTIMATE), inside the $2; with ONE pod kept across KEYFRAMES and DRAFT_GEN (decided, not built) about **$0.5-1.0**; the DRAFT_GEN estimate screen shows the studio's own figure before you approve |

Route B (fallback) is about $0.6-1.0 including margin (the 24 Nov camera scene was about $0.32 without margin).

Checklist, each needs the owner's own yes in chat (CLAUDE.md rule 2); the first line is already given:
- [x] First live Short spend: **$2** (owner, 25 Nov 2026). Re-read the RunPod balance before starting (it was $4.73 on 25 Nov).
- [x] `config/providers.yaml` `dailyCapUsd` 3.50 (done in code, 25 Nov 2026).
- [ ] Set in `.env`: `GPU_MAX_HOURLY_USD=1.0`, `GPU_WATCHDOG_DRY_RUN=false`, the `VIDEO_PROVIDER_*` lines of section 5 (manual: the code never edits `.env`).
- [ ] That the reaper may run with `-Apply` during the run.
- [ ] Narration and music: which provider, or "add them by hand afterwards" (Gemini billing 403; ElevenLabs free plan = Private upload only, or the paid plan first).
- [ ] Which stages to keep MANUAL (suggest all, so DRAFT_GEN's estimate is read before it spends).
- [ ] Publishing: none in this run. Upload Private only, after he has watched it.

### Still manual / not done before the live run

- Restart the control plane and both workers so they run this code; stop the second `studio.worker` if two are running.
- Put the `.env` settings in (section 5). Read the live pod price and the balance.
- The pod image: the warm image does not contain the Fun Camera weights (they download on the first camera clip) and the pod worker file is shipped to the pod at rental time, so no image rebuild is needed for the worker change.
- Look at the FLUX keyframe before approving DRAFT_GEN; look at the clips by eye (the quality gate cannot see colour drift: run `motion-block/drift.py` style Lab check if in doubt).
- A colour-matched restart keyframe is not needed with two beats (beat 2 chains from beat 1; no fresh restart). Keeping one pod across KEYFRAMES and DRAFT_GEN is not built (two boots).
- Narration, music and the final mix of a first Short are the owner's call (see the list above); nothing was run live in this task.

## 9. Terminating pods and rollback

Normal end: the broker (Route A) or `run.py` (Route B) terminates the pod and books the cost. Check after every run (read-only):
the RunPod MCP `list-pods` must be empty; `GET /api/gpu/pods`; the `gpu_pod_lease` table has `released_at` set.

If a pod is left running (critical bug, rule 8):
1. Terminate by hand: RunPod console, or `delete-pod` in the RunPod MCP, or `powershell -File tools\podkit\reaper.ps1 -Apply`, or the kill-timer script `python tools\bakeoff\killer.py <pod_id> 1 <state.json>` (deadline 1 s).
2. Release the lease so the watchdog stops watching: `python tools\gpu_lease\lease.py release runpod <pod_id>`.
3. Note the minutes: the ledger is an estimate; the RunPod bill (`list-billing`) lags a few hours.

Rollback of settings: `VIDEO_CAMERA_PRESETS=false` returns the pod route to SVI/plain clips (a video created from a plan file still takes its plan's text and timing). Put `DREAM_GPU_PROVIDER=modal`, `VIDEO_PROVIDER_DRAFT=wan`, `VIDEO_PROVIDER_FINAL=omni`, `VIDEO_PROVIDER_KEYFRAMES=default` back and restart; nothing can then rent a pod.
A video stuck in a stage: `POST /api/videos/{id}/pause`; to cancel the workflow terminate `video-{id}` in the Temporal UI (http://127.0.0.1:8233). Code rollback: this change set is one branch, revert the commit; the migration set is unchanged.

## 10. Rehearse it yourself (free, no keys, no pod)

```powershell
# separate Temporal namespace so the live workers never see it. Register once (Temporal Python client: workflow_service.register_namespace(RegisterNamespaceRequest(namespace="rehearsal", workflow_execution_retention_period=Duration(seconds=259200))))
$env:TEMPORAL_NAMESPACE = "rehearsal"; $env:POSTGRES_DB = "projectv_dryrun"   # create that database first
# blank every key in the shell, then start: uv run --group studio python -m studio ; ... -m studio.worker ; ... -m workers.main (with an empty .env in the folder so the real one is not found)
```

Expect PLAN to pass and RESEARCH to fail with "GEMINI_API_KEY is not set" (section 2). With `planFile` on `POST /api/videos` (section 5) PLAN, CONCEPT, SCRIPT and SHOT_PLAN pass with no keys; MUSIC and NARRATION fail closed (no Gemini key), then KEYFRAMES stops because no pod can be rented (`DREAM_GPU_PROVIDER=modal`): the free stop point is now KEYFRAMES.
