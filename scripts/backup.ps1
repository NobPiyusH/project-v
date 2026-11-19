# Daily backup: Postgres databases (pg_dump, custom format), the git-ignored .env, and config/.
# Keeps the last $KeepDays days; older backup folders are deleted.
# Never prints file contents (the .env holds keys); it only logs names and sizes.
#
# Run by hand:  powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\project-v\scripts\backup.ps1
param(
    [string]$RepoRoot = 'C:\dev\project-v',
    [string]$Dest = 'C:\dev\backups',
    [int]$KeepDays = 14,
    [string]$Container = 'projectv-postgres-1',
    [string]$DbUser = 'projectv'
)
$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Force $Dest | Out-Null
$log = Join-Path $Dest 'backup.log'
function Write-Log([string]$msg) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Output $line
}

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$target = Join-Path $Dest $stamp
$ok = $true
try {
    New-Item -ItemType Directory -Force $target | Out-Null

    # 1. Databases: every non-template database in the container (projectv, projectv_dream, ...).
    $state = docker inspect -f '{{.State.Running}}' $Container 2>$null
    if ($state -ne 'true') { throw "container $Container is not running (start Docker and the studio services)" }
    $dbs = docker exec $Container psql -U $DbUser -d postgres -At -c "select datname from pg_database where not datistemplate and datname <> 'postgres'"
    if ($LASTEXITCODE -ne 0) { throw 'could not list databases' }
    foreach ($db in ($dbs | Where-Object { $_ -match '^[A-Za-z0-9_]+$' })) {
        # Dump inside the container and copy the file out: piping binary through PowerShell 5.1 corrupts it.
        docker exec $Container pg_dump -U $DbUser -Fc -f "/tmp/$db.dump" $db
        if ($LASTEXITCODE -ne 0) { throw "pg_dump failed for $db" }
        docker cp "${Container}:/tmp/$db.dump" (Join-Path $target "$db.dump") | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "docker cp failed for $db" }
        docker exec $Container rm -f "/tmp/$db.dump" | Out-Null
        $size = (Get-Item (Join-Path $target "$db.dump")).Length
        Write-Log ("db {0}: {1:N0} KB" -f $db, ($size / 1KB))
    }

    # 2. .env (secrets: copied, never read or printed) and config/.
    $envFile = Join-Path $RepoRoot '.env'
    if (Test-Path $envFile) { Copy-Item $envFile (Join-Path $target 'env.backup'); Write-Log '.env copied' }
    else { Write-Log "WARNING: no .env at $envFile" }
    $cfg = Join-Path $RepoRoot 'config'
    if (Test-Path $cfg) { Copy-Item $cfg (Join-Path $target 'config') -Recurse; Write-Log 'config/ copied' }

    Write-Log "OK backup $target"
} catch {
    $ok = $false
    Write-Log "FAILED backup ${stamp}: $($_.Exception.Message)"
}

# 3. Retention: delete backup folders older than $KeepDays (only folders named like our stamps).
$cutoff = (Get-Date).Date.AddDays(-$KeepDays)
Get-ChildItem $Dest -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_\d{6}$' } | ForEach-Object {
    $day = [datetime]::ParseExact($_.Name.Substring(0, 10), 'yyyy-MM-dd', $null)
    if ($day -lt $cutoff) { Remove-Item $_.FullName -Recurse -Force; Write-Log "removed old backup $($_.Name)" }
}

if (-not $ok) { exit 1 }
