# Handover, 22 Nov 2026 (night): CTO chat to the next Claude

Read `CLAUDE.md`, then this file, then `docs/handoffs/2026-11-21-cto-handover.md` (roles, rules, two-Claude rules, still true). No secrets here: values live in `.env` (git-ignored, never print). Memory index: `C:\Users\piyus\.claude\projects\C--dev-project-v\memory\MEMORY.md`.

## Roles and rules (unchanged)
Gendu = CEO (decides what and why, judges by eye and ear, approves every spend, irreversible step and account change). You = CTO: recommend ONE way with reason, risk and cost; start after his OK; report failures plainly. Coding sessions: `claude-opus-5-5`, LOW effort (Sonnet only for routine non-code work). No attribution lines in commits. Push only on his "push" (he said "push" for the image workflow on 22 Nov; later merges are local only). Never restart the backend while a Short, Video stage or pod is running (check first). One CTO at a time.

## Start here
1. `git status`, `git log --oneline -10` (local `main` is ahead of GitHub `origin/main` after `f65beee`).
2. Check health: `curl localhost:8080/actuator/health`; if Docker is down (it stopped twice on 22 Nov, laptop sleep or Docker Desktop crash) start Docker Desktop, `docker compose -f infra/docker-compose.yml up -d`, then the backend and worker (commands below).
3. Check running work: `curl localhost:8080/api/dream` and `/api/videos` (statuses), RunPod list-pods, Vast instances (must be empty unless a Short/Video is using the GPU).
4. The unfinished job: the television video (below).

## How to run things on this machine (Windows)
- Backend (detached, never a child of a shell): PowerShell `Start-Process gradlew.bat ':control-plane:bootRun'` with `JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot`, `GRADLE_OPTS=-Djava.net.preferIPv4Stack=true`, hidden, output to `C:\dev\bench\backend.log` / `backend.err.log`. Health: `/actuator/health`.
- Python media worker: `Start-Process uv 'run','python','-m','workers.main'` in `C:\dev\project-v`, logs `C:\dev\bench\worker.log` / `worker.err.log`. It loads `.env` at start. Restart it after changing worker env or code.
- A backend restart auto-RESUMES paused Videos (the old duplicate `b3989771` came back RUNNING once): re-pause with `POST /api/videos/{id}/pause`.
- Tests: `./gradlew :control-plane:test`, `uv run pytest workers/tests` (about 3 min), `npm --prefix dashboard run build`.

## What is built and live (all merged into local main, tested)
- **GPU broker**: RunPod and Vast proven, searched together in Auto (cheapest live offer); E2E (parked, unproven, needs ~24 h of plan price as balance, see memory `e2e-parked`) and Jarvislabs (SDK adapter built, never run, not recommended) excluded until a canary passes. Provider state + pre-check + owner-approved canary: `GET /api/gpu/broker/providers`, `POST .../{provider}/canary?approve=true&maxUsd=`. Manual pick = GPU type (not an offer id). Daily GPU cap `$10` (`config/providers.yaml`). Vast hands-free 2FA via `VAST_TOTP_SECRET` (rotate it: it was pasted in chat).
- **Warm pod image** `ghcr.io/nobpiyush/projectv-pod:comfy-v0.45.0` (public, no weights, no studio code in layers), `POD_WARM_IMAGE` in `.env`. Pod start about 8 min on RunPod, about 18 on Vast (was 29). Weights still download from Hugging Face (Vast hosts about 65 MB/s, RunPod 200-330). Next speed step (not done): RunPod network volume for weights (~$4.2/month, RunPod only). Boot timing is recorded (`boot` in `GET /api/dream/{id}/gpu`, table `gpu_boot_timing`).
- **Edit/music-sync upgrades** exist (`workers/dream/polish.py`) but ALL OFF: owner judged the original edit better on all 3 sailing pairs (memory `edit-sync-verdict`). Do not re-enable without a new A/B he prefers.
- **Video path lanes** (docs/video-lanes.md): diagram lane (Manim, 2560x1440), AI clips/pictures via the GPU broker (720p max on the pod), 2K master (`VIDEO_OUTPUT_HEIGHT=1440`), stock-footage lane (docs/stock-footage-lane.md: Pixabay, Internet Archive Prelinger, Wikimedia Commons; Pexels optional because new keys are paused; CLIP reranker and PySceneDetect NOT built). `.env` switches set: `VIDEO_DIAGRAM_LANE`, `VIDEO_PROVIDER_DRAFT/FINAL/KEYFRAMES=broker`, `VIDEO_OUTPUT_HEIGHT=1440`, `VIDEO_DIAGRAM_WIDTH/HEIGHT`, `VIDEO_STOCK_LANE/PIXABAY/ARCHIVE/COMMONS`, `ELEVENLABS_COMMERCIAL=1`, `PIXABAY_API_KEY`.
- **New dashboard (Phase 1)**: Create / Studio / Settings at `#/create`, `#/studio`, `#/p/short|video/{id}`, `#/settings`; classic dashboard still the default (`NEW_STUDIO_DEFAULT` in `App.tsx`); write actions (Approve/Redo/mode switches) were never clicked live. Plan and Phase 2 (shared production engine) in `docs/studio-redesign-plan.md`; owner decisions: layout approved, Story hidden until Phase 2, new projects default to Review (10 min), Phase 2 starts with the shared Producer, old pages retired only on his approval per page.
- **Refactors** merged (Java `DreamService` 1611 -> 1229 lines; audio mixing and record-builder extraction NOT done).

## Fixes made on 22 Nov that matter
- ElevenLabs free plan allows 2 concurrent requests: 3 workers got HTTP 429 and the whole video silently fell back to Gemini. Now 2 workers + retry on 429 (`workers/activities/voice.py`).
- Claude output ceiling raised 8,000 -> 16,000 tokens (`ClaudeChat.MAX_TOKENS`): the 26-shot SHOT_PLAN (with diagram specs) was cut off and cycled through weak fallback models. If it is cut off again at 16,000, split the plan into two calls.

## THE OPEN JOB: "How a television works" (2-5 min, 16:9, 2K, ElevenLabs voice)
- Owner approved: the 14-line script (kept verbatim; the writer shortens it, so restore the lines by edit at the Script review), voice (George, `eleven_v4_turbo`, good), music (Lyria, 117 BPM vs 90 brief, approved), hard cap **$3 for both videos together** (about $0.85 used), a story-driven non-PPT style, diagrams (samples approved in `reports/tv-explainer/diagram-samples/`), stock clips where they suit, AI video for the rest, 26-shot list in `reports/tv-explainer/PLAN.md` (Revision 2).
- Video `d3e53cd1-520e-4dd3-8ecb-b6ed0714aa93` is the live one (Shot plan running at the time of writing, KEYFRAMES set to MANUAL so no pod spend yet; to continue, set it back to the broker route and approve Keyframes). The old `b3989771-f2b0-4d7f-a7f4-623979341dbf` is PAUSED, never resume it.
- Rules for the run: message the owner before the first pod rental (estimate + provider); time the FIRST 720p final on the pod before queuing the rest and keep background shots at 480p if it takes more than 450 s; stop at $3; confirm the pod is released afterwards; keep the stock-clip licence list; final at `C:\dev\project-v\reports\tv-explainer\how-television-works.mp4` plus `reports/tv-explainer/REPORT.md`. A side session named `tv-explainer` (`local_93eb4fe7-1866-4e72-b8b9-026d39aa2148`) was driving it; it hit its 5-hour limit once.

## Owner to-do / decisions waiting
- Rotate pasted keys: Vast (3), E2E key+token, RunPod, ElevenLabs, Pixabay, and the Vast TOTP seed.
- Make a decision on: Prelinger items without a machine-readable licence (skipped today), the "Science" channel chip look (placeholder `lonely`), whether to build the CLIP reranker.
- His SSD arrived but is not installed: tell him to move media/weights off C: once it is in.
- Push: local main is ahead of `origin/main`; wait for his "push".
- Open ideas (not started): five Shorts in a row (reliability proof), speed-up LoRA A/B (~$0.30), continuity A/B, movie pipeline session (waits for his "start"), YouTube publishing, character consistency, RunPod network volume.

## Lessons from 22 Nov
- Check the real cause before accepting a peer's theory (the ElevenLabs "missing env var" was a 429).
- Test new providers with preflight + canary only; do not pin manual picks on unproven providers.
- Docker Desktop stops on this laptop: check it first when the backend is unreachable.
- Usage: this chat reached about 500k tokens; each new turn after a cache miss re-reads it. Start fresh chats from handovers like this one; archive finished sessions; avoid many parallel Opus sessions.

## Update, 22 Nov late evening
- Television video cost so far: Rs 187 (about $2.0) of the **$4 hard cap** (owner raised it from $3). About half was waste: the duplicate video b3989771 (Rs 57), the cut-off SHOT_PLAN loop (Rs ~33 extra), a second song. DIAGRAM_SPEC makes 2.3 Gemini calls per shot (23 calls for about 10 shots, Rs 61.6): being investigated by session `cost-cuts` (branch `claude/cost-cuts-*`, flags default OFF, merge only after the television video ends, never restart the backend while it runs). GPU part still ahead: estimate $1.0-1.7.
- Shot plan for d3e53cd1 needed a manual Retry once (a timeout left it FAILED awaiting the owner; a backend restart does NOT retry it).
- E: drive (WD_BLACK SN7100, 931 GB, empty) is installed. Owner approved moving things from C: to E: AFTER the television video: uv cache + model caches, Docker data, `reports\`, and `Downloads` (148 GB, copy then verify then junction). C: has 16 GB free. Worktree cleanup freed almost nothing (uv hardlinks). Real Real-ESRGAN test on E:	oolsealesrgan (7-9 s per 480x832 frame at 4x on the laptop, use `-g 1 -t 128` for the NVIDIA card); a rented GPU is the way for real upscaling; no upscale stage built yet.
- Owner asked about matching an `ai.dreamscape00` style Reel (AI video, not Blender); not started.
- Usage: this chat was about 500k tokens; owner asked to switch to a fresh chat after the television video. Read this note and the memory index first.
