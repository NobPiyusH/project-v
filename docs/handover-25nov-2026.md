# Project V (Cinestack): complete handover, 25 Nov 2026 (late)

Written by the CTO session for the owner (Gendu, CEO; channel Strange If True) and for whoever picks the project up next. Read this file first, then `CLAUDE.md`, `docs/DEVELOPER-TAKEOVER.md` and `docs/pipeline-end-to-end.md`. Prices at Rs 94 per dollar; estimates are marked "est".

## 1. What the project is
A personal AI video studio: a text prompt or a plan file becomes a finished video (plan, research, script, narration, music, pictures, moving clips, edit, captions) plus Shorts. Python only:
- `studio/`: FastAPI control plane on :8080 and the Temporal Python worker (queue `projectv-control-py`), 23-stage video workflow, budgets, holds, GPU broker.
- `workers/`: media worker (queue `projectv-media`): voice, pictures, clips on rented pods, assembly, quality gate hooks.
- `tools/`: film4k (plan generators, camera presets, edit and sound tools), podkit (reaper, ledger, model groups), qualitygate, vetting lab, gpu_lease.
- `dashboard/`: React (Vite) on :5173.
- Services (Docker): Postgres :5433, Temporal :7233 (UI :8233), SeaweedFS :8333. Migrations V1-V48 in `db/migration/` (V48 is new tonight, see section 6).
- Media, models and caches live on `E:\project-data`. Never touch `E:\Downloads` or `C:\Users\piyus\Downloads*`.

Two product lanes share the studio: the **video lane** (documentary and Shorts from a plan) and the **Dream Shorts lane** (`studio/dream`, `studio/autopilot`). The owner wants both kept and independent (section 6).

## 2. Owner rules (do not break)
- Spend only with his own "yes" and a figure in the CTO chat; his yes there covers side sessions (quote his words in briefs). A relayed approval from another session does not count.
- Commit, merge and push only on his word. No attribution lines in commits.
- Keys only in `.env`, never read, printed or echoed. Key rotation is his job.
- Quality first: judge motion on moving frames before long renders; panned-still slideshows are never acceptable. Do not ship a dull video just to ship.
- Every pod: watchdog lease, independent kill timer, daily GPU cap $3.50, the external reaper. Modal cap 0 until 1 Dec 2026.
- **ElevenLabs is a PAID Starter plan (since 23 Nov 2026, commercial).** Never call it free.
- Short, plain replies. Technical decisions are the CTO's; spend, accounts, publishing and deleting are his.

## 3. State of the machine right now
- Pods: none running (the clip pod was released at 22:53 on 25 Nov). Today's GPU spend about $1.38 of $3.50 (reaper log, est).
- Reaper loop: run by the owner in his own PowerShell window, `E:\project-data\logs\reaper_loop.ps1`, now `-Apply -MaxHours 2.5`, log `E:\project-data\logs\reaper-loop.log`. It is a hand-run loop, not a scheduled task; it stops when that window closes.
- `.env` flags set this week: `VIDEO_ONE_POD=true`, `INTERPOLATE_FPS=local`, `GEMINI_MODEL=openai:gpt-5.4-mini,gemini-3.6-flash` (reviews), `POD_CLOUD_PREFERENCE=secure_only` (owner chose Secure pods).
- The running studio has NOT been restarted since tonight's code changes; restart it to apply migration V48 and the new routes (`tools/ops/start_studio.ps1`, or stop and start the two `studio` processes).

## 4. The ant video ("What an ant hears at night"): result and lessons
- Video id `a013569d-50c7-44e6-9c75-159cc25ca005`, made from `docs/shorts/ant-hears-at-night-plan.json` (90 s, 9 scenes x 2 beats). It ran PLAN to DRAFT_GEN through the studio, then stopped at CRITIC. An abandoned first attempt (`539db64a...`, 45-shot plan) still sits at Shot plan; archive it once the studio is restarted (new Archive button).
- Cost: about Rs 44 in API and earlier sunk spend, plus the clip pod (RTX 4090 Secure, about 1.3 h at $0.89/h, est Rs 110). Project total est about Rs 155 of the approved Rs 180 (Rs 193 allowed with the L40S fallback, not used).
- Owner verdict: unwatchable. **Root causes** (my post-mortem, confirmed by the research report):
  1. The plan gave 18 nearly identical dark meadow pictures and wrote no visible event into any shot, so Wan only moved the camera.
  2. The 4-step LightX2V speed-up slows subject motion (its makers say so); tall 720x1280 is the worst case.
  3. The camera preset changed every 5 s beat, each starting from rest: "left, straight, left".
  4. No sound stage (AUDIO is a placeholder) and a script about 16 words per 10 s scene: silent gaps.
  5. I spent on all 18 clips before a cheap 2-scene proof. Rule from now on: proof clip first.
  6. My after-fixes (synthesised sound, colour grade, ripple and shake effects, gamma lift) were rejected by the owner; a shadows-only lift and real CC0 sounds were closer but the footage cannot be saved. The owner dropped this video.
- Files: clips `E:\project-data\outputs\ant-redo\clips\clip00..17.mp4`, previews `film_v2_preview.mp4`, `film_v3_preview.mp4`, CC0 sounds `ant-redo\snd\` with `SOURCES.md`. Live notes of everything that needs improving: `docs/ant-run-notes-25nov.md`.

## 5. Research done tonight
`reports/Best models per stage and motion.md` (notes in `research_notes/`, git-ignored). Headlines:
- Keep: ElevenLabs v4 Turbo voice (#1 on the speech arena; test other voices), Lyria music, gpt-5.4-mini scripts (re-test vs Opus 5.5), Wan 2.2 as the base video model.
- Upgrade: planning and shot design to Claude Opus 5.5; test cheaper, better picture models (MAI-Image-2.7-Flash, Nano Banana 2.1) against FLUX.2 klein; Gemini clip judge asked "did a visible event happen?", calibrated on owner ratings.
- Hosted video: MAGI-2 is #1 image-to-video at about Rs 7.5/s vs Wan about Rs 0.85/s; Sora 2 API shut down, Veo left the Gemini API. Hybrid is a quality choice, not a cost one.
- Prices changing soon: gpt-5.6-sol promo ends 30 Nov; Gemini 4 doubles 1 Dec; Gemini TTS and 3.8 Flash double 1 Jan 2027.
- Not verified: commercial terms of hosted video models; some open-model licences.

## 6. Code changes tonight (in this commit; lint clean; the earlier full run passed 809 tests before the lane-isolation change, and a new full run was still going at commit time)
- **Archive**: migration `V48__video_archive.sql` (`video.archived_at`), `VideoService.set_archived`, `PUT /api/videos/{id}/archive {"archived": true|false}` (refuses a running video), `GET /api/videos?archived=true` lists archived ones too, Archive button on each video row in the dashboard.
- **RunPod create errors** now carry RunPod's reason (`studio/gpu/broker/runpod.py`), not only "400 Bad Request".
- **CRITIC** placeholder no longer asks Rs 5 (`config/providers.yaml` holdEstimates inr 0).
- **Camera continuity**: `imported.camera_warnings` warns when a scene changes camera move mid-scene; the ant plan generator now uses one move per scene.
- **Lane isolation**: if the Dream lane or the video lane fails to start, the API and worker log it and run the other lane alone (`studio/api/app.py`, `studio/worker.py`); `studio/tests/test_lane_isolation.py` fails if one lane imports the other. Still shared (owner said not now): the video lane borrows the LLM, gateway, clip progress and GPU wrapper from the Dream wiring.
- **Docs**: ElevenLabs wording fixed in `docs/pipeline-end-to-end.md`, `docs/plans/four-month-plan.md`, `docs/DEVELOPER-TAKEOVER.md`; new `docs/pipeline-end-to-end.md`, `docs/CLIENT-OVERVIEW.md` (draft, not for sharing yet), `docs/ant-run-notes-25nov.md`.
- **Tools**: `tools/film4k/make_ant_plan.py`, `ant_sound.py` (CC0 bed, cues, ducking, loudness; first version of the AUDIO stage), `ant_fx.py` (ripple and shake experiment, rejected by the owner; kept for reference).
- Tag `pre-reconstruction` marks the state before any rebuild.

## 7. The rebuild plan (from the research, in order)
Free code first:
1. Planner gives every shot its own picture idea and one visible event (subject, verb, start, end); a gate rejects near-duplicate pictures and event-free prompts. (CRITICAL)
2. Camera planned per scene: one direction and speed carried across chained clips (SVI), eased starts and stops.
3. Real AUDIO stage from `ant_sound.py`: bed, event cues, music ducked under narration, -14 LUFS; a check that the script leaves no silent gaps.
4. Shorts pacing and publish gates: 2-4 s cuts, a hook in the first 2 s, loop check, AI-content flag at upload, episode-variety check.
5. Proof-clip gate: one 10 s proof before any full clip run; cheap previews of every shot, full renders only for picked takes.
6. Planning and shot design on Opus 5.5; skip the 7 placeholder stages; true cost totals that include pod rentals.
Paid tests (each needs his yes and figure; about Rs 400 together, est): motion sampler settings on 3 shots (about Rs 85), picture-model A/B (about Rs 50), hero shot on MAGI-2/Seedance vs Wan (about Rs 115-210).
Then: clip judge, finishing chain (48 fps, 4K upscale, aligned captions), voice test with two other voices.

## 8. Open items and owner jobs
- Waiting on the owner: go-ahead for the free rebuild steps and a figure for the paid tests; whether to commit/merge this handover branch to main and push.
- Owner jobs: rotate pasted keys; pay the Google billing; decide on the Lyria licence for monetised videos; schedule the reaper as a Windows task (needs his OK); clear 15 stuck Java workflows and remove the JDK (needs his OK).
- Known bugs not fixed yet: video list `spent_inr` excludes pod rentals; Lyria cap vs estimate mismatch; KEYFRAMES hold text quotes the Gemini picture price; inspector false alarm on imported plans; stage card shows no "clip n of m" progress; old lease rows from 19 and 24 Nov unreleased.

## 9. Commands
- Services: `docker compose -f infra/docker-compose.yml up -d`; after a reboot `tools/ops/start_studio.ps1`.
- Control plane: `uv run --group studio python -m studio` (:8080); worker: `uv run --group studio python -m studio.worker`; media worker: `uv run python -m workers.main`; dashboard: `npm --prefix dashboard run dev`.
- Tests: `uv run --group dev --group studio python -m pytest`; lint: `uv run --group lint ruff check .`.
- Sound tool: `uv run python -m tools.film4k.ant_sound <outputs_dir> [video]`.

## 10. Memory and sessions
Memory notes: `C:\Users\piyus\.claude\projects\C--dev-project-v\memory\` (index `MEMORY.md`). Most relevant: `cto-mode`, `owner-working-rules`, `spend-approval-via-cto`, `elevenlabs-free-plan` (now says PAID), `video-look-lessons-23nov`, `gpu-pods-need-watchdog-lease`.
