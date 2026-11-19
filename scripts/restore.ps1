# Restore one database dump from a backup folder into a database.
# Default is safe: restores into a NEW scratch database and refuses to touch an existing one.
#
#   Test restore:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restore.ps1 -Backup C:\dev\backups\2026-11-19_030000 -Database projectv -Target projectv_restore_test
#   Real restore:  stop the control plane and worker first, then add -Replace with -Target projectv.
#
# .env and config/ are plain copies in the backup folder (env.backup, config\); copy them back by hand if needed.
param(
    [Parameter(Mandatory)][string]$Backup,
    [string]$Database = 'projectv',
    [string]$Target = 'projectv_restore_test',
    [switch]$Replace,
    [string]$Container = 'projectv-postgres-1',
    [string]$DbUser = 'projectv'
)
$ErrorActionPreference = 'Stop'
if ($Target -notmatch '^[A-Za-z0-9_]+$') { throw "bad target name $Target" }
$dump = Join-Path $Backup "$Database.dump"
if (-not (Test-Path $dump)) { throw "no dump at $dump" }

$exists = docker exec $Container psql -U $DbUser -d postgres -At -c "select 1 from pg_database where datname = '$Target'"
if ($exists -eq '1') {
    if (-not $Replace) { throw "database $Target already exists; use -Replace to overwrite it (stop the studio first)" }
    docker exec $Container dropdb -U $DbUser --force $Target
    if ($LASTEXITCODE -ne 0) { throw "could not drop $Target" }
}
docker exec $Container createdb -U $DbUser $Target
if ($LASTEXITCODE -ne 0) { throw "could not create $Target" }

docker cp $dump "${Container}:/tmp/restore.dump" | Out-Null
docker exec $Container pg_restore -U $DbUser --no-owner -d $Target /tmp/restore.dump
$rc = $LASTEXITCODE
docker exec $Container rm -f /tmp/restore.dump | Out-Null
if ($rc -ne 0) { throw "pg_restore reported errors (exit $rc)" }

$tables = docker exec $Container psql -U $DbUser -d $Target -At -c "select count(*) from information_schema.tables where table_schema = 'public'"
Write-Output "Restored $Database -> $Target ($tables tables)"
