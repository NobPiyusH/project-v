# Project V (Cinestack): developer takeover guide

For a developer who is taking over this project cold. Status date: 25 Nov 2026 (main at 95a4cd1 or later). Read this first, then the documents in section 12.

> Warning: `docs/HANDOVER.md` ("the book") is the requirements and design source of truth, but it still describes the old **Java** control plane in places. The Java control plane was removed on 24 Nov 2026 (git tag `java-final`). Where the book and this guide disagree about the stack, this guide and `CLAUDE.md` win.

## 1. What the project is
A **personal, privately owned AI video studio**. Input: a text prompt. Output: a complete, fully moving **4K video** (research, script, narration, music, sound, visuals, edit, captions, publishing kit) plus **Shorts**. The owner runs a YouTube channel ("Strange If True") and wants documentary-style films and dreamlike Shorts at low cost.

It is not a product for sale. Build to production-grade engineering standards, but the audience is one owner.

Core ideas:
- **Auto, Review and Manual modes on every stage.** Auto runs by itself; Review waits a window (default 10 minutes) for the owner, then continues; Manual holds. Never build a stage that can only run one way.
- **A failed stage never kills a video.** It waits for the owner (Retry / Continue without it). Finished stages can be redone with dependency tracking so outputs never go stale.
- **Quality first, cost second, automation third.** A panned still image passed off as video ("slideshow") is never acceptable output.
- Every generated asset gets a provenance row, every paid job a cost entry, every routing choice a routing decision.

## 2. Stack
| Layer | Technology |
|---|---|
| Control plane | Python 3.11, FastAPI (`studio/`), Temporal Python SDK (queue `projectv-control-py`) |
| Workers | Python (`workers/`): media, ML, YouTube, quality; also run on GPU pods |
| Database | Postgres 16 with pgvector, host port **5433**; Flyway-compatible SQL migrations in `db/migration/` applied by `studio/migrate.py` |
| Workflow engine | Temporal dev server (7233, UI 8233) |
| Object store | SeaweedFS S3 (8333); Google Drive for hand-offs |
| Dashboard | React + Vite + TypeScript (`dashboard/`) |
| GPU | Rented RunPod pods (main); ComfyUI as the engine; Modal is capped at 0 until 1 Dec 2026 |
| Video models | Wan 2.2 14B fp8 + LightX2V 4-step LoRAs (main), SVI continuation, Wan 2.2 Fun Camera, Wan 2.2 Fun VACE |
| Keyframes | FLUX.2 klein 4B (Apache 2.0) |
| Upscale / interpolate | SeedVR2 3B, Real-ESRGAN, RIFE |
| Edit hand-off | OpenTimelineIO to FCP7 XML (Premiere or DaVinci Resolve) |
| Build tools | `uv` (Python), npm (dashboard), ruff, pytest, GitHub Actions CI on PRs |

## 3. Repository map (`C:\dev\project-v`)
- `studio/`: the Python control plane. `api/` (routes, wiring), `workflow/` (Temporal workflow and activities), `planner/` (plan, presets), `production/` (producer, stages, timing), `gpu/` (pod broker, watchdog, leases, self-stop), `routing/` (provider router), `dream/`, `autopilot/`, `ledger/`, `media/`, `video/`, `contract/`, `migrate.py`, `worker.py`, `config.py`. See `studio/README.md`.
- `workers/`: activities and helpers (`activities/`, `youtube/`, `quality_gate/`, `pod/`, `modal_apps/`, `gemini_pool.py`, `groq_whisper.py`).
- `tools/`: standalone tooling, mostly pure functions plus tests:
  - `film4k/`: the 3-minute 4K film pipeline: `edit.py` (shot-level editor), `sound.py` (procedural sound bed), `camera_presets.py`, `camera_graphs.py` (ComfyUI API graphs), `repair.py` (fix a bad moment inside a clip).
  - `qualitygate/`: automatic quality checks (section 7).
  - `podkit/`: pod ledger (spend cap), `groups.py` (model download groups), `reaper.py` (external kill-timer).
  - `gpu_lease/`, `bakeoff/` (kill-timer `killer.py`), `vetting/` (third-party tool scanner), `metrics/`, `ops/`, `blender/`, `model_ab/`, `live_status/`.
- `config/`: `providers.yaml` (prices, caps), `style-presets.yaml`, `quality-gate.yaml`.
- `db/migration/`: SQL migrations. `infra/`: Docker compose (Postgres, Temporal, SeaweedFS). `scripts/`: start/stop/backup.
- `dashboard/`: React studio UI. `docs/`: all documentation. `reports/`: committed research and channel art. `research_notes/`: git-ignored scratch.

## 4. Setup on the owner's machine
Windows 11 Home, Asus TUF A17, RTX 3050 4 GB, 32 GB RAM. The laptop cannot run big video models: generation happens on rented GPUs. Repo is at `C:\dev\project-v` (outside OneDrive). C: has little free space; media, model weights, Docker volumes and outputs live on `E:\project-data`. Never touch `E:\Downloads` or `C:\Users\<owner>\Downloads*`.

Steps:
1. Install uv, Docker Desktop (WSL2), Node, FFmpeg on PATH.
2. Copy `.env.example` to `.env` and fill it in (see section 8). `.env` is git-ignored.
3. `docker compose -f infra/docker-compose.yml up -d` (Postgres 5433, Temporal, SeaweedFS).
4. `uv run --group studio python -m studio` (API on 127.0.0.1:8080; applies migrations at start) and `uv run --group studio python -m studio.worker`. After a reboot `tools/ops/start_studio.ps1` starts everything.
5. Dashboard: `npm --prefix dashboard run dev` (127.0.0.1:5173).
6. Tests: `uv run --group dev --group studio python -m pytest` (use the project defaults; a bare `tools/` path collects a Blender script that needs `bpy` and fails). Last full run: 1011 passed, 1 skipped. Lint: `uv run --group lint ruff check .`.
7. Tests that write data run on a throwaway database and skip if no local Postgres is up.

## 5. Architecture in short
- **Planner** turns the prompt into a plan (scenes, beats, style preset). **Producer** runs stages (script, narration, music, keyframes, video, edit, captions, publishing kit) as Temporal workflows. All I/O is in activities; workflow code must be deterministic; pass asset ids, never media bytes, through workflows.
- **Provider router**: a credit-aware router chooses between free tiers, paid APIs, rented GPUs and manual hand-offs. Prices come from `config/providers.yaml` or live reads, never hard-coded.
- **GPU broker**: `GpuBroker.acquire()` is the only way to rent a pod. Every pod gets a self-stop wrapper and a **watchdog lease**; the studio watchdog kills `projectv-*` pods without a lease. Warm images cut boot from about 29 to 8-18 minutes.
- **One watchdog, one set of jobs**: each takes a Postgres advisory lock (`studio/singleton.py`), so a second app copy never runs a second watchdog.
- **Feature switches** (all default on, set `=false` to disable): `STUDIO_DREAM_ROUTES`, `STUDIO_VIDEO_ROUTES`, `STUDIO_JOBS`, `STUDIO_GPU_WATCHDOG`, `STUDIO_TEMPORAL_WORKER`, `STUDIO_MIGRATE`. `DREAM_GPU_PROVIDER=modal` (default) rents no pods; `broker` uses RunPod.

## 6. The film pipeline (`tools/film4k/`) and camera control
Why it exists: Wan 2.2 plus SVI animates the subject but barely moves the camera, and a post-zoom reads as a slideshow.
- **Wan 2.2 Fun Camera** (ComfyUI core nodes `WanCameraEmbedding`, `WanCameraImageToVideo`) makes a real camera move. Tested good: zoom in and pan left. Roll presets warp: use rarely. Others untested.
- **Wan 2.2 Fun VACE** follows a camera path drawn as an edge video, and can repair a masked region of a clip.
- `camera_presets.py` names the moves and plans a varied sequence per scene (never the same move twice in a row). `camera_graphs.py` builds the ComfyUI API graphs (built from graphs that worked on a real pod). `repair.py` chooses reroll (new seed for the beat) or VACE inpaint (small area, at most 4 seconds), then composites back with a feathered mask.
- Sizes: 4k+1 frames, width and height multiples of 16. 720x1280 and 81 frames at 16 fps worked on a 4090.
- `tools/podkit/groups.py` lists model files per job so a pod downloads only what it needs (Fun Camera about 40 GB, boot about 10 minutes).
- Measured (24 Nov): Secure 4090 $0.89/h, Community $0.34/h but some hosts are bad (CUDA False, hung image pull): abort if no boot log in 9 minutes and retry. One 5-second beat at 720p took about 3.6 minutes.

## 7. Quality gate (`tools/qualitygate/`, `docs/quality-gate.md`)
Three tiers on every cut:
1. Free ffmpeg checks: loudness (target about -14 LUFS), black frames, frozen frames, caption timing.
2. Local scoring: motion, independent motion (removes one global camera move, catches panned stills), sharpness, flicker, colour drift. Shot cuts are found automatically (`--shots auto`).
3. Optional vision judge (OpenAI): OFF by default. Trials showed it misses colour drift (blue wing) and falsely flags good beats as near-still, so it is not trusted as a detector.
It runs after the edit stage (`workers/activities/gate_check.py`, and in the film CLI). A fail holds the stage for the owner; warns are logged only. Command: `uv run python -m tools.qualitygate <video> --shots auto --judge off`. Thresholds are calibrated on very few clips: expect tuning.

## 8. Secrets and accounts
Secrets live only in `.env` (git-ignored); `.env.example` is committed. Never log, print or commit a key. Keys used: Gemini, Groq, OpenRouter, Anthropic, OpenAI, ElevenLabs, Freesound, NVIDIA, RunPod, Vast, HF token, YouTube OAuth, and others. **Several keys were pasted into chat in the past and have not been rotated: rotate them first.** Do not ask the owner to share a password; do not use anyone else's Adobe login (not needed).

## 9. Rules you must follow
1. Ask the owner before anything irreversible, anything that spends money, anything touching his accounts. He approves spend with a figure in chat.
2. Merge and push to main only when he says so. Work on branches; no attribution lines in commit messages.
3. Every pod needs a watchdog lease and an independent kill-timer. A forgotten running pod is a critical bug (rule 8). Daily GPU cap $3.50. Ledger cap in `tools/podkit/ledger.py`.
4. Never hard-code market GPU prices; re-verify API prices and model IDs before integrating.
5. Model weights come from Hugging Face or a cached volume, never Google Drive.
6. Judge motion by eye before long renders. A change that saves money ships off by default until an A/B shows equal quality.
7. Minimal, targeted changes; do not restructure without reason.
8. Licences matter: check them before adopting any model or tool (see section 11).

## 10. Current status (25 Nov 2026)
Done and on main: Python-only studio (Java removed), film editor and sound bed, camera presets and graphs, shot repair, quality gate with hook after edit, external pod reaper (dry-run by default, not scheduled), vetting lab, ruff and CI, code analytics, the research report.
In progress at hand-over: normalising film loudness to -14 LUFS (the Act 1 cut measures -21.1 LUFS); character and location bank with per-beat timing.
Proven: Wan 2.2 plus LightX2V on 4090 pods; Fun Camera at 720p; shot-level re-cut; free quality checks on a real cut.
**Unproven (risks):**
- HIGH: the Python studio has never produced a real Short end to end on live Temporal. `run_shard.py` has never run on a pod.
- MEDIUM: VACE repair quality; chained-beat camera drift over many beats; the reaper on a real pod (API field names assumed); thresholds of the quality gate; E2E provider is parked.
- Known leftovers: 15 stuck old Java workflows in Temporal; the JDK is still installed.

## 11. Decisions already made (do not reopen without a reason)
- Wan 2.2 stays the main video model. Fp8 accepted. Last-frame chaining preferred over first-last.
- FLUX.2 klein 4B for keyframes (never the 9B). Scripts: OpenAI gpt-5.4-mini won the A/B.
- Music: Lyria preferred by ear; ACE-Step off. Chatterbox voice retired. ElevenLabs: the owner's plan is paid (Starter, bought 23 Nov 2026); confirm the commercial terms before the first publish.
- MiniMax H3: licence excludes the US, EU, UK and South Korea (use and displaying outputs); open weights only 768p: ignore for this channel.
- Ignore list and watch list: `reports/AI video studio landscape.md` (prioritised adoption plan).
- Dependency policy: vet third-party tools with `tools/vetting` before installing; study premium software as a black box only (no cracking, no extraction).

## 12. Documents to read, in order
1. `CLAUDE.md`: the working rules (short). 2. This file. 3. `docs/handover-25nov-2026.md`: owner-facing latest status and open items. 4. `docs/HANDOVER.md`: the full book (mind the Java caveat). 5. `studio/README.md`. 6. `docs/film4k.md`, `docs/quality-gate.md`, `docs/pod-reaper.md`, `docs/pod-tests-24nov.md`, `docs/vetting-lab.md`, `docs/java-removal.md`. 7. `reports/AI video studio landscape.md`. 8. `docs/plans/`, `docs/shorts/`, `docs/adr/`, `docs/runbooks/`.

## 13. Backlog (priority order)
1. Run one real Short through the Python studio on live Temporal (needs a spend figure from the owner); first real use of `run_shard.py` on a pod.
2. Blue-wing repair with VACE (about $0.5 of pod time).
3. Schedule the pod reaper (every 10 minutes) and confirm its field names against the first real pod.
4. Script-aligned captions (WhisperX alignment); publishing set-up (upload Private first, AI-disclosure flag, scheduled publish).
5. Audio: ElevenLabs Music/SFX while Lyria is blocked; Kokoro fallback.
6. Tests in the research report, each with the owner's yes and a figure: SeedVR2 vs Real-ESRGAN, interpolation order and GIMM-VFI, LongCat-Video, LTX-2.5, hosted hero-shot tier, narration blind A/B.
7. Clean up the stuck Java workflows; remove the JDK with the owner's OK.
8. Tune the quality gate thresholds on more clips; revisit the vision judge only with a stronger model.

## 14. Working with the owner
He is the CEO; the lead engineer acts as CTO: lead with one recommended way, explain in short plain words, and bring him only decisions that are his (spend with a figure, accounts, keys, publishing, push and merge). Amounts in rupees at Rs 94 per dollar; mark estimates as estimates. Report outcomes faithfully: if something failed or was not verified, say so.
