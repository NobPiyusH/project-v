# Project V Studio

A personal AI video studio: **text prompt → complete, publish-ready video**. Python only (the Java control plane was removed on 24 Nov 2026). Every production stage runs in
**Auto** (default) or **Manual** mode. New here? Read [docs/DEVELOPER-TAKEOVER.md](docs/DEVELOPER-TAKEOVER.md) first, then [CLAUDE.md](CLAUDE.md)
(rules and commands) and [docs/README.md](docs/README.md) (index of all docs). The requirements book is
[docs/HANDOVER.md](docs/HANDOVER.md) (stale on the stack; the takeover guide wins).

**Status:** see the takeover guide section 10. Pipeline summary (M2a): Plan → Research (Google Search, sourced facts) → Concept → Script →
Music (Lyria, beat-mapped) → Narration (Gemini TTS) + Shot plan (beat-snapped) → Keyframes (Nano Banana for hero
shots, Z-Image on Modal for the rest) → Animatic. Every stage runs Auto or Manual; failed stages wait for you
(Retry / Continue without it). Finished stages can be redone with only their dependents; single shots can be
redone with notes (Auto / Premium / Free image model), their prompts edited, and any earlier version restored for free.
Script lines can be edited directly and re-voiced one by one (optionally in a different reading style); every voice
take is kept and can be switched back for free.
Video generation, upscaling and the final edit arrive in M2b/M2c.

Paid Gemini spend is capped by the app at ₹400/month (`config/providers.yaml → pricing.gemini.monthlyCapInr`).

## Run it (Windows, from `C:\dev\project-v`)

One-time setup:
- copy `.env.example` to `.env` (already done) and put your Gemini key in `GEMINI_API_KEY` (billing enabled);
- link Modal (`uv run modal token new`) and deploy the keyframe model: `uv run modal deploy workers/modal_apps/zimage.py`.

Open five terminals (or run `scripts\start.ps1`, which starts them all):

```powershell
# 1. Local services: Postgres (5433), Temporal (7233, UI on http://localhost:8233), SeaweedFS S3 (8333)
docker compose -f infra/docker-compose.yml up -d

# 2. Control plane (Python API on http://localhost:8080; applies db/migration first)
uv run --group studio python -m studio

# 3. The control plane's Temporal worker (queue projectv-control-py)
uv run --group studio python -m studio.worker

# 4. Python media worker
uv run python -m workers.main

# 5. Dashboard (http://127.0.0.1:5173)
npm --prefix dashboard run dev
```

Tests: `uv run --group dev --group studio python -m pytest` (studio, workers, tools). Lint: `uvx ruff@0.17.5 check .`. Secret scan and lint on commit: see [docs/dev-setup.md](docs/dev-setup.md).

## Layout

| Path | What |
|---|---|
| `studio/` | Python control plane (FastAPI, Temporal workflows, planner, GPU broker and watchdog, REST API) |
| `db/migration/` | SQL schema, applied at start by `studio/migrate.py` (Flyway-compatible history) |
| `workers/` | Python Temporal worker for media/ML activities |
| `dashboard/` | React + TypeScript (Vite) studio UI |
| `config/` | `providers.yaml`, `style-presets.yaml` |
| `infra/` | docker compose for local services |
| `docs/` | handover book, ADRs |

The Java control plane was removed on 24 Nov 2026: see [docs/java-removal.md](docs/java-removal.md).

Stack changes from the handover book are recorded in [docs/adr/0001-m1-stack-updates.md](docs/adr/0001-m1-stack-updates.md).
