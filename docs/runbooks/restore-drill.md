# Restore drill (25 Nov 2026)

Goal: prove `scripts/backup.ps1` output restores into a fresh database, never touching live `projectv`.

## Commands

```powershell
# 1. Backup into a drill folder on E: (uses the live repo's .env/config; reads only, copies files)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 -Dest E:\project-data\backup-drill

# 2. Restore into a NEW database (restore.ps1 refuses to overwrite without -Replace; never pass -Replace)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restore.ps1 `
  -Backup E:\project-data\backup-drill\<stamp> -Database projectv -Target projectv_restore_drill

# 3. Compare exact row counts per public table (live side is read-only)
#    build "select 'tbl', count(*) from public.tbl union all ..." from information_schema.tables,
#    run it with `docker exec projectv-postgres-1 psql -U projectv -d <db> -At -c ...` on both, diff outputs.

# 4. Clean up
docker exec projectv-postgres-1 dropdb -U projectv --force projectv_restore_drill
# and delete the drill folder (it holds a copy of .env and .secrets: remove them at least)
```

## Result

| Step | Time |
| --- | --- |
| Backup (5 databases, .env, .secrets, config) | 12.9 s |
| Restore projectv (814 KB dump) into projectv_restore_drill | 4.1 s, 51 tables, pg_restore exit 0 |

Row counts: 51 public tables in live and restored, every count identical (for example asset 1024, provenance 1024,
cost_entry 823, dream_event 747, stage_run 260, routing_decision 193, shot 174, video 15, flyway_schema_history 43).
The throwaway database was dropped afterwards; the live `projectv` was only read.

## Notes

- No script bug found; no edits made.
- backup.ps1 dumps every non-template database, including leftover `projectv_test_*` databases from test runs. Harmless, but they could be dropped.
- Counts are exact only while the studio is quiet; a running worker can add rows between dump and compare.
- Not drilled: restoring `.env`, `.secrets`, `config/` (plain file copies) and Temporal/SeaweedFS data (not covered by backup.ps1).
