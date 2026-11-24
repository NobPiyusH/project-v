# Start the local studio after a reboot or a crash: Docker, the compose services, the Python control plane, its Temporal worker, the Python media worker.
# Usage:  powershell -ExecutionPolicy Bypass -File tools\ops\start_studio.ps1 [-NoBackend] [-NoWorker]
# Safe to run twice: every step checks first. Does NOT start autopilot or any Short (autopilot has its own switch in the dashboard).
# STATUS: Java removed 24 Nov 2026 (docs/java-removal.md); the control plane is `python -m studio`, run end to end only on the first start after the cut-over.
param([switch]$NoBackend, [switch]$NoWorker)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

function Say($m) { Write-Host ("{0:HH:mm:ss} {1}" -f (Get-Date), $m) }
function DockerUp { try { docker info *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function PortOpen($p) { return [bool](Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) }

# 1. Docker Desktop
if (-not (DockerUp)) {
    $exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $exe)) { throw "Docker Desktop not found at $exe" }
    Say 'starting Docker Desktop'
    Start-Process $exe
    $end = (Get-Date).AddMinutes(4)
    while (-not (DockerUp)) { if ((Get-Date) -gt $end) { throw 'Docker did not come up in 4 minutes' }; Start-Sleep -Seconds 5 }
}
Say 'Docker is up'

# 2. Postgres, Temporal, SeaweedFS
docker compose -f infra/docker-compose.yml up -d | Out-Null
$end = (Get-Date).AddMinutes(2)
do {
    $h = docker inspect --format '{{.State.Health.Status}}' projectv-postgres-1 2>$null
    if ($h -eq 'healthy') { break }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $end)
Say "postgres: $h"
if ($h -ne 'healthy') { throw 'Postgres is not healthy: the GPU watchdog lease and the studio need it' }

# 3. Python control plane (port 8080): applies db/migration at start; also starts the GPU watchdog and the periodic jobs (one process only: a lock
#    in Postgres stops a second copy from running them). It rents no pod and starts no Short by itself.
if (-not $NoBackend) {
    if (PortOpen 8080) { Say 'backend already listening on 8080' }
    else {
        Say 'starting the Python control plane'
        Start-Process -WindowStyle Hidden -FilePath 'uv' -ArgumentList 'run', '--group', 'studio', 'python', '-m', 'studio' -WorkingDirectory $repo `
            -RedirectStandardOutput "$repoackend.log" -RedirectStandardError "$repoackend.err.log"
        $end = (Get-Date).AddMinutes(2)
        while (-not (PortOpen 8080)) { if ((Get-Date) -gt $end) { throw 'backend did not open port 8080 in 2 minutes (see backend.err.log)' }; Start-Sleep -Seconds 3 }
        Say 'backend is listening on 8080'
    }
    $tw = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'studio\.worker' }
    if ($tw) { Say 'control-plane Temporal worker already running' }
    else {
        Say 'starting the control-plane Temporal worker (queue projectv-control-py)'
        Start-Process -WindowStyle Hidden -FilePath 'uv' -ArgumentList 'run', '--group', 'studio', 'python', '-m', 'studio.worker' -WorkingDirectory $repo `
            -RedirectStandardOutput "$repo\studio-worker.log" -RedirectStandardError "$repo\studio-worker.err.log"
    }
}

# 4. Python media worker
if (-not $NoWorker) {
    $running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'workers\.main' }
    if ($running) { Say 'python worker already running' }
    else {
        Say 'starting the Python media worker'
        Start-Process -WindowStyle Hidden -FilePath 'uv' -ArgumentList 'run', 'python', '-m', 'workers.main' -WorkingDirectory $repo
    }
}
Say 'done. Check http://localhost:8080/api/system and the dashboard.'
