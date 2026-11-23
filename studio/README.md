# studio: Python control plane (migration in progress)

Plan: `docs/plans/java-to-python-migration.md`. The Java control plane is untouched and stays live.

- Done: S0 skeleton + contract harness, S1 ledger (`studio/ledger`, `GET /api/ledger`).
- Install once (needs network): `uv sync --group studio`
- Tests: `uv run --no-sync pytest studio -q` (API and Postgres tests skip until the `studio` group is installed)
- Run beside Java: `uv run uvicorn --factory studio.api.app:create_app --port 8081`
- Compare with Java: `uv run python -m studio.contract record http://localhost:8080 paths.txt rec.json`, then `... compare http://localhost:8081 rec.json`
