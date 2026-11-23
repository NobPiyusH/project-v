# studio: Python control plane (migration in progress)

Plan: `docs/plans/java-to-python-migration.md`. The Java control plane is untouched and stays live.

- Done: S0 skeleton + contract harness, S1 ledger (`GET /api/ledger`), S2 media types/assets/provider registry (`GET /api/providers`), S3 routing/planner/LLM stack/agents (`/api/routing/*`, `/api/styles`), S4a pod watchdog family (`/api/gpu/watchdog`, `/api/gpu/pods`).
- The Python GPU watchdog loop is OFF unless `STUDIO_GPU_WATCHDOG=true`; the Java watchdog stays in charge until you approve the switch (two live watchdogs would both terminate pods).
- Tests that change data run on a throwaway database made from the real migrations (never the live `projectv` DB).
- Install once (needs network): `uv sync --group studio`
- Tests: `uv run --no-sync pytest studio -q` (API and Postgres tests skip until the `studio` group is installed)
- Run beside Java: `uv run uvicorn --factory studio.api.app:create_app --port 8081`
- Compare with Java: `uv run python -m studio.contract record http://localhost:8080 paths.txt rec.json`, then `... compare http://localhost:8081 rec.json`

## Dream, Dream GPU and Autopilot routes (S7, all OFF by default)

- `STUDIO_DREAM_ROUTES=true` mounts `/api/dream`, `/api/autopilot` and the Dream GPU controls; `STUDIO_JOBS=true` runs the periodic jobs; `STUDIO_TEMPORAL_WORKER=true uv run python -m studio.worker` starts the worker (own queue `projectv-control-py`). With Java live, leave the jobs and the worker off.
- `DREAM_GPU_PROVIDER=modal` (default): no pod can be rented. `broker`: RunPod through the GPU broker (self-stop wrapper and watchdog lease on every pod).
- Dashboard, per area: `PROJECTV_PY_AREAS=/api/dream,/api/autopilot npm --prefix dashboard run dev` sends only those areas to the Python app on :8081; without the variable everything goes to Java as before.
