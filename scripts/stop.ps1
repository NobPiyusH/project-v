<#
  Stops the studio's app processes. Docker services keep running (they hold your data and start in seconds);
  add -All to stop them too; -Backend keeps the dashboard running (its Restart button uses this).
  Usage:  powershell -ExecutionPolicy Bypass -File C:\dev\project-v\scripts\stop.ps1 [-All] [-Backend]
#>
param([switch]$All, [switch]$Backend)
$root = Split-Path -Parent $PSScriptRoot

function Stop-Port($port, $name) {
    $ids = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    foreach ($id in $ids) { Stop-Process -Id $id -Force -Confirm:$false -ErrorAction SilentlyContinue }
    if ($ids) { Write-Host "  stopped $name" } else { Write-Host "  $name was not running" }
}

Stop-Port 8080 'control plane'
# Only this folder's worker (its .venv): a second copy of the studio (e.g. the dream-shorts test) keeps running.
$pyWorkers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*workers.main*' }
$mine = @($pyWorkers | Where-Object { $_.ExecutablePath -like "$root\.venv\*" })          # the .venv launcher
$workers = $pyWorkers | Where-Object { $mine.ProcessId -contains $_.ProcessId -or $mine.ProcessId -contains $_.ParentProcessId }
foreach ($w in $workers) { Stop-Process -Id $w.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
if ($workers) { Write-Host '  stopped media worker' } else { Write-Host '  media worker was not running' }
if (-not $Backend) { Stop-Port 5173 'dashboard' }

# No 'gradlew --stop': it stops every Gradle daemon on the PC, including another copy's control plane.
# Idle daemons exit by themselves after 3 hours.
Remove-Item "$root\.run\*.pid" -ErrorAction SilentlyContinue

if ($All) {
    Push-Location $root
    docker compose -f infra/docker-compose.yml stop | Out-Null
    Pop-Location
    Write-Host '  stopped docker services (data kept)'
}
Write-Host 'Done.'
