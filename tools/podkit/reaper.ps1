# Runs the pod reaper once. Dry-run unless -Apply. Exit code: 0 ok, 1 a terminate failed, 2 RunPod API unreachable / no key.
# Schedule it every 10 minutes: see docs/pod-reaper.md. Never prints the key (read from the environment or the repo .env).
param([switch]$Apply, [switch]$NoLeaseCheck, [double]$DailyCap = 3.50, [double]$MaxHours = 3)
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$argsList = @("run", "python", "tools/podkit/reaper.py", "--daily-cap", $DailyCap, "--max-hours", $MaxHours)
if ($Apply) { $argsList += "--apply" }
if ($NoLeaseCheck) { $argsList += "--no-lease-check" }
& uv @argsList
exit $LASTEXITCODE
