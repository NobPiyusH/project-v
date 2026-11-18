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
$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*workers.main*' }
foreach ($w in $workers) { Stop-Process -Id $w.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
if ($workers) { Write-Host '  stopped media worker' } else { Write-Host '  media worker was not running' }
if (-not $Backend) { Stop-Port 5173 'dashboard' }

# Gradle daemons started by bootRun keep memory; stop only this project's daemon.
if (Test-Path "$root\gradlew.bat") { & "$root\gradlew.bat" --stop *> $null }
Remove-Item "$root\.run\*.pid" -ErrorAction SilentlyContinue

if ($All) {
    Push-Location $root
    docker compose -f infra/docker-compose.yml stop | Out-Null
    Pop-Location
    Write-Host '  stopped docker services (data kept)'
}
Write-Host 'Done.'
