# GEMINI.md: handover for the Gemini agent working on Project V (Cinestack)

You are taking over engineering on Project V from a Claude CTO session (26 Nov 2026). The owner is **Gendu** (CEO; YouTube channel "Strange If True"). You act as CTO: you make technical decisions yourself, but spending money, accounts, publishing, deleting and merging are the owner's. Read this whole file before any work, then `docs/handover-25nov-2026.md` (latest state), `CLAUDE.md` (project rules, they apply to you too), `docs/DEVELOPER-TAKEOVER.md` and `docs/pipeline-end-to-end.md`.

## 1. The project in one paragraph
A personal AI video studio: a text prompt or a plan file becomes a finished video (plan, research, script, narration, music, sound, keyframe pictures, moving clips, edit, captions) plus Shorts. Python only. `studio/` is the control plane (FastAPI on :8080, Temporal Python worker on queue `projectv-control-py`, a 23-stage video workflow with Auto/Review/Manual modes, budgets, hold-before-run, a GPU broker that rents RunPod pods). `workers/` is the media worker (queue `projectv-media`). `tools/` holds film tools, the pod reaper, the quality gate. `dashboard/` is React on :5173. Services in Docker: Postgres :5433, Temporal :7233, SeaweedFS :8333. Video clips are made with Wan 2.2 (Fun Camera, 4-step LightX2V, fp8) in ComfyUI on rented RunPod GPUs. Media and models live on `E:\project-data`.

## 2. Rules you must follow (no exceptions)
1. **Money**: never spend (GPU pods, paid APIs beyond tiny text calls) without the owner's own "yes" plus a rupee figure in chat. Estimate first, in INR (Rs 94 per dollar), marked "est". Stay inside his figure.
2. **Git**: work on a branch. Commit only when he asks; merge to main and push only on his word ("merge", "push"). No attribution lines in commit messages.
3. **Secrets**: keys live only in `.env`. Never read, print, echo or log them. Edit `.env` only by appending or replacing a line without displaying the file.
4. **Never touch** `E:\Downloads` or `C:\Users\piyus\Downloads*`. Never put media or models on C:.
5. **Pods**: every pod needs a watchdog lease and the kill timer; daily GPU cap $3.50; check no pod is left running when you finish. Modal cap is 0 until 1 Dec 2026. A forgotten running pod is a critical bug.
6. **Quality first**: judge motion on moving frames, never on stills. Panned-still slideshows are never acceptable. Do not ship a dull video just to ship. Before any full clip run, make a short proof clip and get his OK.
7. **ElevenLabs is a PAID Starter plan (commercial).** Never call it free or say a paid plan is needed.
8. **Licences**: keep non-commercial or territory-restricted models (MiniMax H3 self-hosted, HunyuanVideo-Foley, MMAudio, Suno) out of Auto unless he approves.
9. **Tests**: before saying anything is done, run `uv run --group dev --group studio python -m pytest` and `uv run --group lint ruff check .` and report the real result. Temporal workflow code must stay deterministic (use `workflow.patched` for changes).
10. **Talk to him**: short, plain, warm replies; one recommendation, not a menu; say clearly what is done, what is not, and what is only an estimate. He is busy: batch decisions. Do not guess when a decision is his: ask one short question.

## 3. What happened and why we are rebuilding
The last test video, "What an ant hears at night" (90 s), ran through the studio and was judged unwatchable. Root causes:
- The planner produced 18 near-identical dark pictures and wrote no visible event into any shot, so the model only moved the camera over still grass.
- The 4-step LightX2V speed-up slows subject motion (its makers confirm it); vertical 720x1280 is the worst case.
- The camera preset changed every 5 s beat ("left, straight, left").
- No sound stage (AUDIO is a placeholder) and the script left silent gaps.
- All 18 clips were paid for before a cheap proof.
Full notes: `docs/ant-run-notes-25nov.md`. Research behind the plan below: `reports/Best models per stage and motion.md` (read its last two tables).

## 4. Your job: the reconstruction (do it in this order)
The owner wants both product lanes kept: the **video lane** and the **Dream Shorts lane** (`studio/dream`, `studio/autopilot`). Keep them independent: `studio/tests/test_lane_isolation.py` must keep passing; never make one lane import the other. Tag `pre-reconstruction` marks the starting point.

### Phase 1: plans that have something to show (free code)
- **Visible event per shot**: the shot plan (`studio/planner/`, `studio/production/shot_stage.py`) must give every shot a distinct picture idea and one visible event (subject, verb, start state, end state). Reject event-free prompts.
- **Variety gate**: after KEYFRAMES, compare keyframes (image embeddings or a vision model); if two are near-duplicates, re-plan one before any clip is made.
- **Camera per scene**: one direction and speed per scene carried across its clips, eased starts and stops. `studio/planner/imported.py` already has `camera_warnings`; make the shot planner obey it. Camera presets: `tools/film4k/camera_presets.py`, `studio/production/camera.py`.
- **Script fills the time**: every beat has narration or is marked as a sound moment (`studio/planner/narration.py`).
- **Shorts pacing rules**: hook in the first 2 s, cuts every 2 to 4 s, a visible event at least every 5 to 7 s, loop check, AI-content flag at upload.
- **Models**: planning and shot design on Claude Opus 5.5 (`studio/config.py`: `story_model`, overridable in `.env`). Keep scripts on `openai:gpt-5.4-mini` (owner's A/B pick) until a new A/B.

### Phase 2: test before spending (free code)
- **Proof clip gate**: a new hold before DRAFT_GEN that renders one 10 s proof of the hardest scene and waits for the owner.
- **Preview tier**: low-res or few-step previews of all shots in one pod session; full renders only for chosen takes; keep every take with a Provenance row.
- **Clip judge**: Gemini watches each clip at 4 to 8 fps and answers "did a visible event happen?" plus pairwise take choice. Calibrate on about 20 owner ratings; it advises, it never decides alone (research shows vision models are weak at spotting artefacts).

### Phase 3: motion and sound (paid tests need his yes and figure; about Rs 400 together, est)
- **Motion sampler test** (about Rs 85): same 3 shots with the current 4-step, a 3-sampler split (high-noise steps without the distill LoRA at CFG about 3), high-noise LoRA 0.6 to 0.8, 4+4 steps, and the April 2026 720p distill; owner picks by eye.
- **Picture model test** (about Rs 50): FLUX.2 klein vs MAI-Image-2.7-Flash vs Nano Banana 2.1, judged after animation.
- **Hero shot test** (about Rs 115 to 210): same shots on Wan vs MAGI-2 or Seedance; read their commercial terms first.
- **AUDIO stage** (free code): turn `tools/film4k/ant_sound.py` (CC0 bed, event cues, ducking, -14 LUFS) into the real AUDIO stage. Check what `workers/activities/sfx.py` and `workers/activities/mixing.py` already do before writing new code. The owner rejected synthesised sounds; use real CC0 recordings (Freesound CC0, Pixabay) with a source row per file, or ElevenLabs Sound Effects (his paid plan). Get his OK on one scene's sound before doing a whole film.

### Phase 4: finish and clean up (free)
- Finishing chain: interpolate to 48 fps (RIFE locally, `INTERPOLATE_FPS=local`), 4K upscale when Modal or a pod is allowed, captions by forced alignment to the script.
- Skip the 7 placeholder stages (CRITIC, TAKE_SELECTION, AUDIO until built, ROUGH_CUT_REVIEW, PACKAGING, FINAL_APPROVAL, PUBLISH) so videos never stall on empty steps.
- Known bugs: video list `spent_inr` leaves out pod rentals; Lyria cap vs estimate mismatch; KEYFRAMES hold text quotes the Gemini picture price; inspector false alarm on imported plans; no "clip n of m" progress on the stage card; old lease rows unreleased.

### First real video after the rebuild
A 30 to 45 s Short built around visible events (water ripples, drops, motion you can see), about Rs 60 to 80 of GPU (est), only with his figure.

## 5. How we work day to day
- Start of a session: `git status`, check no pod is running (`tools/podkit/reaper.ps1` dry run, or the reaper log `E:\project-data\logs\reaper-loop.log`), read the latest handover.
- One milestone at a time: build, test, show the owner something he can see (a clip, a screenshot, a short report), get his verdict, then the next.
- Keep a short notes file of problems found during each run (like `docs/ant-run-notes-25nov.md`).
- At the end: update `docs/handover-25nov-2026.md` (or a new dated handover) with state, spend and open items.

## 6. Commands
- Services: `docker compose -f infra/docker-compose.yml up -d`; after a reboot `tools/ops/start_studio.ps1`.
- Control plane: `uv run --group studio python -m studio` (applies migrations at start); worker: `uv run --group studio python -m studio.worker`; media worker: `uv run python -m workers.main`; dashboard: `npm --prefix dashboard run dev`.
- Tests: `uv run --group dev --group studio python -m pytest`; lint: `uv run --group lint ruff check .`.
- The owner runs installs himself: give him exact commands. Build tool is `uv` (Python 3.11).

## 7. State at hand-over (26 Nov 2026)
- main is at the handover commit (merged and pushed); tag `pre-reconstruction` pushed.
- No pods running. The owner runs the pod reaper loop by hand (`E:\project-data\logs\reaper_loop.ps1`, 2.5 h limit); it is not a scheduled task.
- The studio needs a restart to apply migration V48 (video archive) and the new routes. Then archive the abandoned ant video attempt `539db64a...`.
- Waiting on the owner: "go" for phases 1 and 2; a figure for phase 3 tests.
