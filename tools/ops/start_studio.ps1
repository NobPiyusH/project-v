# Start the local studio after a reboot or a crash: Docker, the compose services, the Java control plane, the Python worker.
# Usage:  powershell -ExecutionPolicy Bypass -File tools\ops\start_studio.ps1 [-NoBackend] [-NoWorker]
# Safe to run twice: every step checks first. Does NOT start autopilot or any Short (autopilot has its own switch in the dashboard).
# STATUS: written 24 Nov 2026 from CLAUDE.md's commands; NOT yet run end to end.
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

# 3. Java control plane (port 8080)
if (-not $NoBackend) {
    if (PortOpen 8080) { Say 'backend already listening on 8080' }
    else {
        Say 'starting the Java control plane (takes about 10 seconds to answer)'
        $env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot'
        $env:GRADLE_OPTS = '-Djava.net.preferIPv4Stack=true'
        Start-Process -WindowStyle Hidden -FilePath "$repo\gradlew.bat" -ArgumentList ':control-plane:bootRun' -WorkingDirectory $repo `
            -RedirectStandardOutput "$repo\backend.log" -RedirectStandardError "$repo\backend.err.log"
        $end = (Get-Date).AddMinutes(4)
        while (-not (PortOpen 8080)) { if ((Get-Date) -gt $end) { throw 'backend did not open port 8080 in 4 minutes (see backend.err.log)' }; Start-Sleep -Seconds 5 }
        Say 'backend is listening on 8080'
    }
}

# 4. Python worker
if (-not $NoWorker) {
    $running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'workers\.main' }
    if ($running) { Say 'python worker already running' }
    else {
        Say 'starting the Python worker'
        Start-Process -WindowStyle Hidden -FilePath 'uv' -ArgumentList 'run', 'python', '-m', 'workers.main' -WorkingDirectory $repo
    }
}
Say 'done. Check http://localhost:8080/actuator/health and the dashboard.'
