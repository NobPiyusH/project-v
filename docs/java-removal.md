# Java removal (24 Nov 2026)

The owner decided the studio is Python only. The Java/Spring Boot control plane was removed from the repo on branch `claude/python-only`.

## What was removed
- `control-plane/` (Spring Boot 4.1 app, its Java tests, `application.properties`), `settings.gradle.kts`, `gradle/`, `gradlew`, `gradlew.bat`, `gradle.properties`, the Gradle/JDK parts of `scripts/start.ps1`, `scripts/stop.ps1`, `scripts/check.ps1`, `tools/ops/start_studio.ps1`, the `java` CI job, and the Gradle entries in `.gitignore` and `.gitattributes`.

## What replaced it
- The Python control plane in `studio/` on the same port (8080), so the dashboard, `workers/` and `tools/` needed no change. Start: `uv run --group studio python -m studio` and `python -m studio.worker`.
- SQL migrations moved to `db/migration/` and are applied at start by `studio/migrate.py`, reading the same `flyway_schema_history` table (versions 1 to 43 were applied by Flyway; their checksums match, checked against the live table).
- The GPU watchdog and the periodic jobs run inside the Python app, one process at a time (Postgres advisory lock).
- Test config files used by `studio/tests/test_pricing.py` moved to `studio/tests/fixtures/config/`.

## How to get the Java back
The last Java state is tagged `java-final` on main:

```powershell
git checkout java-final -- control-plane settings.gradle.kts gradle gradlew gradlew.bat gradle.properties
```

Java needs JDK 21 and `$env:GRADLE_OPTS = "-Djava.net.preferIPv4Stack=true"`; start it with `./gradlew :control-plane:bootRun`. Stop the Python app first: two control planes must never run together (two watchdogs would both terminate pods). The SQL files are also still in `control-plane/src/main/resources/db/migration` at that tag.

## Workflows the Java worker left open
Workflows started by the Java worker (types `VideoProductionWorkflow`, `DreamShortWorkflow`, ... on queue `projectv-control`) are not served by the Python worker (queue `projectv-control-py`, types with the suffix `Py`). On 24 Nov 2026 Temporal listed 15 open ones (9 `DreamShortWorkflow` from 20 Nov, 6 `VideoProductionWorkflow` from 16 Nov): old and stuck. They were left alone. Terminate them in the Temporal UI (http://localhost:8233) when you decide.

## Proposed CLAUDE.md update (not applied: left for the owner)
CLAUDE.md still describes Spring Boot and Gradle. Suggested replacements: the project line "Java 21 / Spring Boot 3.5 / Temporal / Embabel control plane, Python workers" becomes "Python control plane (FastAPI, Temporal Python SDK), Python workers"; rule 13 becomes "Build and run tool is **uv** (Python 3.11); the dashboard uses npm"; the Stack paragraph drops Spring Boot / Spring AI and keeps "plain Temporal SDK, Temporal dev server, SeaweedFS, Postgres on host port 5433, server timezone UTC"; the commands `./gradlew :control-plane:bootRun` and `:control-plane:test` become `uv run --group studio python -m studio` (plus `python -m studio.worker`) and `uv run --group dev --group studio python -m pytest`.

## Not proven yet (HIGH)
The Python path has never run a real video or Short end to end on live Temporal. It was proven on fakes, on a Temporal test server, by replaying 56 live read-only requests and 11 write routes against copies of the live data. The first real Short after the cut-over is the proof.
