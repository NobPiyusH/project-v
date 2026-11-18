# Project V Studio

A personal AI video studio: **text prompt → complete, publish-ready video**. Every production stage runs in
**Auto** (default) or **Manual** mode. See [docs/HANDOVER.md](docs/HANDOVER.md) (start with "Revision R1") and
[CLAUDE.md](CLAUDE.md).

**Current milestone: M2a (story pipeline).** Plan → Research (Google Search, sourced facts) → Concept → Script →
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

Open four terminals:

```powershell
# 1. Local services: Postgres (5433), Temporal (7233, UI on http://localhost:8233), SeaweedFS S3 (8333)
docker compose -f infra/docker-compose.yml up -d

# 2. Control plane (API on http://localhost:8080)
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot"
.\gradlew.bat :control-plane:bootRun

# 3. Python media worker
uv run python -m workers.main

# 4. Dashboard (http://127.0.0.1:5173)
npm --prefix dashboard run dev
```

Tests: `.\gradlew.bat :control-plane:test`

## Layout

| Path | What |
|---|---|
| `control-plane/` | Java 21, Spring Boot 4.1, Temporal workflows, planner, REST API, Flyway schema |
| `workers/` | Python Temporal worker for media/ML activities |
| `dashboard/` | React + TypeScript (Vite) studio UI |
| `config/` | `providers.yaml`, `style-presets.yaml` |
| `infra/` | docker compose for local services |
| `docs/` | handover book, ADRs |

Stack changes from the handover book are recorded in [docs/adr/0001-m1-stack-updates.md](docs/adr/0001-m1-stack-updates.md).
