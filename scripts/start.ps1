<#
  Starts the whole studio: local services (Docker), the Python control plane (API), its Temporal worker, the Python media worker and the dashboard.
  Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File C:\dev\project-v\scripts\start.ps1
  Logs:  C:\dev\project-v\logs\*.log      Stop:  scripts\stop.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "$root\logs", "$root\.run" | Out-Null

# Tools installed by winget are only on PATH in new shells; make sure this one sees them.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
foreach ($pattern in 'astral-sh.uv*', 'Gyan.FFmpeg*') {
    $pkg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg) {
        $bin = Get-ChildItem $pkg.FullName -Recurse -Include 'uv.exe', 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($bin -and ($env:Path -notlike "*$($bin.DirectoryName)*")) { $env:Path += ';' + $bin.DirectoryName }
    }
}
$env:PYTHONIOENCODING = 'utf-8'

# Children get an empty stdin file plus file-redirected stdout/stderr, so none inherits the caller's pipes
# (a caller waiting for pipe EOF, e.g. the dashboard Restart button or a tool shell, otherwise hangs).
$nullIn = "$root\.run\empty.in"
if (-not (Test-Path $nullIn)) { New-Item -ItemType File -Force -Path $nullIn | Out-Null }

function Wait-Url($url, $name, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $url | Out-Null; Write-Host "  ok  $name" -ForegroundColor Green; return $true } catch { Start-Sleep -Seconds 2 }
    }
    Write-Host "  !!  $name did not come up in ${seconds}s - see logs\" -ForegroundColor Red
    return $false
}

function Test-Port($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

Write-Host '1/5 Local services (Postgres 5433, Temporal 7233/8233, SeaweedFS 8333)'
docker compose -f infra/docker-compose.yml up -d | Out-Null
$deadline = (Get-Date).AddSeconds(60)
while ((docker inspect -f '{{.State.Health.Status}}' projectv-postgres-1 2>$null) -ne 'healthy' -and (Get-Date) -lt $deadline) { Start-Sleep 2 }
Write-Host '  ok  docker services' -ForegroundColor Green

Write-Host '2/5 Control plane (Python API :8080; applies db/migration at start)'
if (Test-Port 8080) { Write-Host '  ..  already running' } else {
    $p = Start-Process -FilePath 'uv' -ArgumentList 'run', '--group', 'studio', 'python', '-m', 'studio' -WorkingDirectory $root `
        -RedirectStandardInput $nullIn -RedirectStandardOutput "$root\logs\control-plane.log" -RedirectStandardError "$root\logs\control-plane.err.log" -WindowStyle Hidden -PassThru
    $p.Id | Set-Content "$root\.run\control-plane.pid"
}

Write-Host '3/5 Temporal worker of the control plane (queue projectv-control-py)'
$tw = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*studio.worker*' -and $_.ExecutablePath -like "$root\.venv\*" }
if ($tw) { Write-Host '  ..  already running' } else {
    $p = Start-Process -FilePath 'uv' -ArgumentList 'run', '--group', 'studio', 'python', '-m', 'studio.worker' -WorkingDirectory $root `
        -RedirectStandardInput $nullIn -RedirectStandardOutput "$root\logs\studio-worker.log" -RedirectStandardError "$root\logs\studio-worker.err.log" -WindowStyle Hidden -PassThru
    $p.Id | Set-Content "$root\.run\studio-worker.pid"
}

Write-Host '4/5 Python media worker'
$worker = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*workers.main*' -and $_.ExecutablePath -like "$root\.venv\*" }
if ($worker) { Write-Host '  ..  already running' } else {
    # Renders and downloads go to the media SSD when .env sets PROJECTV_TMP (docs/runbooks/ssd-move.md, rule 12).
    $tmpLine = Get-Content "$root\.env" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*PROJECTV_TMP\s*=' } | Select-Object -First 1
    if ($tmpLine) {
        $tmp = ($tmpLine -split '=', 2)[1].Trim().Trim('"')
        if (Test-Path $tmp) { $env:TEMP = $tmp; $env:TMP = $tmp; Write-Host "  ..  worker temp files on $tmp" }
        else { Write-Host "  !!  PROJECTV_TMP=$tmp does not exist; using the default temp folder" -ForegroundColor Yellow }
    }
    $p = Start-Process -FilePath 'uv' -ArgumentList 'run', 'python', '-m', 'workers.main' -WorkingDirectory $root `
        -RedirectStandardInput $nullIn -RedirectStandardOutput "$root\logs\worker.log" -RedirectStandardError "$root\logs\worker.err.log" -WindowStyle Hidden -PassThru
    $p.Id | Set-Content "$root\.run\worker.pid"
}

Write-Host '5/5 Dashboard (:5173)'
if (Test-Port 5173) { Write-Host '  ..  already running' } else {
    $p = Start-Process -FilePath 'npm.cmd' -ArgumentList '--prefix', 'dashboard', 'run', 'dev' -WorkingDirectory $root `
        -RedirectStandardInput $nullIn -RedirectStandardOutput "$root\logs\dashboard.log" -RedirectStandardError "$root\logs\dashboard.err.log" -WindowStyle Hidden -PassThru
    $p.Id | Set-Content "$root\.run\dashboard.pid"
}

Write-Host 'Health checks'
$ok = Wait-Url 'http://127.0.0.1:8080/api/system' 'control plane' 180
$ok = (Wait-Url 'http://127.0.0.1:5173/' 'dashboard' 60) -and $ok
$deadline = (Get-Date).AddSeconds(60)
while (-not (Select-String -Path "$root\logs\worker.err.log", "$root\logs\worker.log" -Pattern 'listening' -Quiet -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep 2 }
if (Select-String -Path "$root\logs\worker.err.log", "$root\logs\worker.log" -Pattern 'listening' -Quiet -ErrorAction SilentlyContinue) { Write-Host '  ok  media worker' -ForegroundColor Green } else { Write-Host '  !!  media worker not listening - see logs\worker*.log' -ForegroundColor Red; $ok = $false }

Write-Host ''
if ($ok) { Write-Host 'Studio is up:  http://127.0.0.1:5173   (Temporal UI: http://localhost:8233)' -ForegroundColor Cyan; exit 0 }
else { Write-Host 'Some parts failed to start - check the logs folder.' -ForegroundColor Yellow; exit 1 }
