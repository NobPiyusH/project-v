# CLAUDE.md: working rules for Cinestack

Read `docs/HANDOVER.md` fully before any work, starting with "Revision R1" at the top. It's the source of truth for requirements, architecture and decisions.

## Project in one line
A personal AI video studio: text prompt → complete, fully moving 4K video (narration, music, sound, visuals, edit, captions, publishing kit) + Shorts. Every stage runs in Auto (default) or Manual mode; the owner (Gendu) can direct any step. MelodySheep-style documentary is one style preset, not the only target. Java 21 / Spring Boot 3.5 / Temporal / Embabel control plane, Python workers, ComfyUI on rented or free GPUs, Postgres+pgvector, MinIO + Google Drive, React dashboard, OTIO → FCP7 XML for Premiere.

## Rules
1. Work milestone by milestone (HANDOVER §23). Demo each one and get the owner's approval before starting the next. **Order (R1): M1 backbone first, GPU benchmarks (M0) later** when the owner approves spending.
2. Ask the owner before anything irreversible, anything that spends money, or anything that touches his accounts. **Don't assume: ask when a decision is the owner's.** Remaining open decisions are listed in HANDOVER §26.
2a. Every stage supports **Auto and Manual** modes, switchable per video and mid-run. Never build a stage that can only run one way.
3. Secrets only in `.env` (git-ignored). Commit `.env.example`. Never log or print keys.
4. Respect every provider's ToS: one account per provider, no free-tier farming, **no unofficial automation of Google Flow** (it's a manual provider via drop folder).
5. Temporal workflow code must be deterministic; all I/O in activities; pass asset IDs, never media bytes, through workflows.
6. Every generated asset gets a Provenance row; every paid job gets a CostEntry; every routing choice gets a RoutingDecision.
7. Never hard-code market GPU prices; read them live or from config. Re-verify API prices and model IDs before integrating (the book's numbers date from Nov 2026).
8. GPU pods must auto-shutdown when idle. Treat a forgotten running pod as a critical bug.
9. Model weights come from Hugging Face (or a cached volume), never from Google Drive.
10. Quality first, cost second, automation third. Panned-still slideshows are never acceptable output.
11. Code style: minimal, targeted changes; don't restructure existing code without reason; explain non-obvious choices in PR descriptions.
12. Target the owner's environment: Windows 11 Home laptop (Asus TUF A17 FA706IC, RTX 3050 4 GB, 32 GB RAM); services via WSL2/Docker. Repo lives at `C:\dev\project-v` (outside OneDrive). C: has little free space: never put media, model weights or Docker volumes there once the media SSD is installed.
13. Build tool is **Gradle** (Kotlin DSL) with a Java 21 toolchain. The owner runs installs himself; give exact commands.

## Stack as built (see docs/adr/0001)
Spring Boot **4.1** / Spring AI **2.0** (not 3.5/1.1 as the book says), plain Temporal SDK 1.40, Temporal dev server, **SeaweedFS** instead of MinIO, Postgres on host port **5433**, server timezone UTC.

## Commands
- `docker compose -f infra/docker-compose.yml up -d`: local services (Postgres 5433, Temporal 7233 + UI 8233, SeaweedFS S3 8333)
- `./gradlew :control-plane:bootRun`: control plane on :8080 (set `JAVA_HOME` to the JDK 21 at `C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot`; Gradle needs `GRADLE_OPTS=-Djava.net.preferIPv4Stack=true` on this machine)
- `./gradlew :control-plane:test`: workflow tests
- `uv run python -m workers.main`: local Python worker
- `npm --prefix dashboard run dev`: dashboard on 127.0.0.1:5173
