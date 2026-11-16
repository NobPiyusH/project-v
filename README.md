# Project V Studio

A personal AI video studio: **text prompt → complete, publish-ready video**. Every production stage runs in
**Auto** (default) or **Manual** mode. See [docs/HANDOVER.md](docs/HANDOVER.md) (start with "Revision R1") and
[CLAUDE.md](CLAUDE.md).

**Current milestone: M1 (backbone).** The workflow engine, Auto/Manual modes on every stage, approvals, restart
safety, planner, provider registry, cost ledger, Python worker and dashboard are real. The stages after Plan are
placeholders that record what they will do; real research, script, music, video and editing arrive in M2.

## Run it (Windows, from `C:\dev\project-v`)

One-time: copy `.env.example` to `.env` (already done) and put your Gemini key in `GEMINI_API_KEY`.
Without a key the Planner uses a simple rule-based plan.

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
