# CLAUDE.md: working rules for Cinestack

Read `docs/HANDOVER.md` fully before any work, starting with "Revision R1" at the top. It's the source of truth for requirements, architecture and decisions.

## Project in one line
A personal AI video studio: text prompt → complete, fully moving 4K video (narration, music, sound, visuals, edit, captions, publishing kit) + Shorts. Every stage runs in Auto (default) or Manual mode; the owner (Gendu) can direct any step. MelodySheep-style documentary is one style preset, not the only target. Python control plane (FastAPI, Temporal Python SDK; the Java control plane was removed on 24 Nov 2026, tag `java-final`), Python workers, ComfyUI on rented or free GPUs, Postgres+pgvector, MinIO + Google Drive, React dashboard, OTIO → FCP7 XML for Premiere.

## Positioning (owner, 16 Nov 2026)
A serious, premium, privately owned studio: not a hobby prototype and not a product for sale. Build it to production-grade engineering standards (durability, tests, observability, clean architecture) and premium output quality.

## Rules
1. Work milestone by milestone (HANDOVER §23). Demo each one and get the owner's approval before starting the next. **Order (R1): M1 backbone first, GPU benchmarks (M0) later** when the owner approves spending.
2. Ask the owner before anything irreversible, anything that spends money, or anything that touches his accounts. **Don't assume: ask when a decision is the owner's.** Remaining open decisions are listed in HANDOVER §26.
2a. Every stage supports **Auto, Review and Manual** modes, switchable per video and mid-run. Review = semi-auto: the stage waits a review window (default 10 min, per video) for the owner, then continues by itself; switching to Manual holds it. Never build a stage that can only run one way.
2b. A failed stage never kills a video: it waits for the owner (Retry / Continue without it). Finished stages can be redone with `REDO_WITH` dependencies (VideoService) so outputs never go stale.
3. Secrets only in `.env` (git-ignored). Commit `.env.example`. Never log or print keys.
5. Temporal workflow code must be deterministic; all I/O in activities; pass asset IDs, never media bytes, through workflows.
6. Every generated asset gets a Provenance row; every paid job gets a CostEntry; every routing choice gets a RoutingDecision.
7. Never hard-code market GPU prices; read them live or from config. Re-verify API prices and model IDs before integrating (the book's numbers date from Nov 2026).
8. GPU pods must auto-shutdown when idle. Treat a forgotten running pod as a critical bug.
9. Model weights come from Hugging Face (or a cached volume), never from Google Drive.
10. Quality first, cost second, automation third. Panned-still slideshows are never acceptable output.
11. Code style: minimal, targeted changes; don't restructure existing code without reason; explain non-obvious choices in PR descriptions.
12. Target the owner's environment: Windows 11 Home laptop (Asus TUF A17 FA706IC, RTX 3050 4 GB, 32 GB RAM); services via WSL2/Docker. Repo lives at `C:\dev\project-v` (outside OneDrive). C: has little free space: never put media, model weights or Docker volumes there once the media SSD is installed.
13. Build and run tool is **uv** (Python 3.11); the dashboard uses npm. The owner runs installs himself; give exact commands.

## Stack as built (see docs/adr/0001)
Python control plane `studio/` (see docs/java-removal.md), plain Temporal SDK, Temporal dev server, **SeaweedFS** instead of MinIO, Postgres on host port **5433**, server timezone UTC.

## Commands
- `docker compose -f infra/docker-compose.yml up -d`: local services (Postgres 5433, Temporal 7233 + UI 8233, SeaweedFS S3 8333)
- `uv run --group studio python -m studio`: control plane on :8080 (applies the database migrations at start; `tools/ops/start_studio.ps1` starts everything after a reboot)
- `uv run --group studio python -m studio.worker`: Temporal worker for the control plane
- `uv run --group dev --group studio python -m pytest`: all tests (studio, workers, tools)
- `uv run --group lint ruff check .`: lint
- `uv run python -m workers.main`: local Python worker (needs FFmpeg on PATH)
- `uv run modal deploy workers/modal_apps/zimage.py`: deploy the Z-Image keyframe app to the owner's Modal account
- Models are set in `studio/config.py`, overridable via `.env`; prices and the ₹1000 Gemini cap live in `config/providers.yaml`. Gemini media models need the `v1beta` API.
- `npm --prefix dashboard run dev`: dashboard on 127.0.0.1:5173
