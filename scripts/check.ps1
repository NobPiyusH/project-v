<#
  Code health check: finds copy-pasted code, unused code and likely bugs, and writes one report.
  Usage:  powershell -ExecutionPolicy Bypass -File C:\dev\project-v\scripts\check.ps1
  Report: C:\dev\project-v\logs\code-check.txt     (report only: nothing is changed)
  First run downloads the checkers (ruff, vulture, PMD), a few tens of MB.
#>
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null
$jdk = Get-ChildItem 'C:\Program Files\Eclipse Adoptium' -Directory -Filter 'jdk-21*' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
$env:GRADLE_OPTS = '-Djava.net.preferIPv4Stack=true'
$env:PYTHONIOENCODING = 'utf-8'
$report = "$root\logs\code-check.txt"

function Section($title, [scriptblock]$run) {
    Write-Host "== $title" -ForegroundColor Cyan
    "`r`n==================== $title ====================" | Add-Content $report -Encoding utf8
    $out = & $run 2>&1 | Out-String
    $out | Add-Content $report -Encoding utf8
    $lines = ($out -split "`n" | Where-Object { $_.Trim() }).Count
    Write-Host "   $lines line(s) of findings"
}

"Project V code check - $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Set-Content $report -Encoding utf8
Section 'Python: likely bugs and style (ruff)' { uv run --group lint ruff check workers --output-format concise }
Section 'Python: unused code (vulture)' { uv run --group lint vulture }
Section 'Java: copy-pasted code (PMD CPD)' { & "$root\gradlew.bat" -q :control-plane:cpd --console=plain }
Section 'Dashboard: lint (oxlint)' { npm.cmd --prefix dashboard run -s lint }
Write-Host ''
Write-Host "Report: $report" -ForegroundColor Green
