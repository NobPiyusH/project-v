# studio: the Python control plane

The only control plane since 24 Nov 2026 (the Java one was removed: `docs/java-removal.md`; migration plan and history: `docs/plans/java-to-python-migration.md`).

- Start: `uv run --group studio python -m studio` (API on 127.0.0.1:8080, PORT or STUDIO_PORT to change) and `uv run --group studio python -m studio.worker` (Temporal worker, queue `projectv-control-py`). `scripts/start.ps1` starts both with the rest of the studio.
- At start the API applies `db/migration/V*.sql` (`studio/migrate.py`, Flyway-compatible: it reads and writes `flyway_schema_history`, never re-runs an applied version, and refuses to start if an applied file was edited). `python -m studio.migrate` does only that.
- On by default, each switched off with `=false`: `STUDIO_DREAM_ROUTES` (Dream, autopilot, Dream GPU), `STUDIO_VIDEO_ROUTES` (videos, shots, producer, budget, stages, assets), `STUDIO_JOBS` (pod release checks every 30 s, autopilot tick every minute), `STUDIO_GPU_WATCHDOG` (the GPU watchdog), `STUDIO_TEMPORAL_WORKER`, `STUDIO_MIGRATE`.
- One watchdog, one set of jobs: each takes a Postgres advisory lock (`studio/singleton.py`), so a second copy of the app serves requests but never runs a second watchdog or a second autopilot tick.
- `DREAM_GPU_PROVIDER=modal` (default): no pod can be rented. `broker`: RunPod through the GPU broker (self-stop wrapper and watchdog lease on every pod, `GpuBroker.acquire()` only).
- Tests: `uv run --group studio pytest studio -q`. Tests that change data run on a throwaway database made from the real migrations (never the live `projectv` DB); they skip when no local Postgres is up.
- Contract tools from the migration (`studio/contract`): `record` / `compare` replay GET exchanges between two servers; `python -m studio.contract.writes` fires the write routes at a throwaway COPY of a database (set `POSTGRES_DB` to the copy; it refuses `projectv`).
- Dashboard: `npm --prefix dashboard run dev` proxies `/api` to :8080 (`PROJECTV_API_URL` to change).
