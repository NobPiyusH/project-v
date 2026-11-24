# Wi-Fi keep-alive for remote days. Runs every 2 minutes as a scheduled task (SYSTEM).
# If the internet is unreachable twice in a row, it restarts the Wi-Fi adapter and asks Windows to reconnect.
# It never touches passwords, profiles or any other setting. Log: C:\ProgramData\projectv-wifi\keepalive.log
$dir = 'C:\ProgramData\projectv-wifi'
New-Item -ItemType Directory -Force $dir | Out-Null
$log = Join-Path $dir 'keepalive.log'
$flag = Join-Path $dir 'down.flag'

function Up { foreach ($h in '1.1.1.1','8.8.8.8') { if (Test-Connection -ComputerName $h -Count 1 -Quiet -ErrorAction SilentlyContinue) { return $true } } return $false }

if (Up) {
    if (Test-Path $flag) { "$(Get-Date -Format s) back online" | Add-Content $log; Remove-Item $flag -Force }
    exit 0
}
if (-not (Test-Path $flag)) { New-Item $flag -ItemType File | Out-Null; exit 0 }   # first miss: wait for the next run

"$(Get-Date -Format s) offline twice: starting recovery" | Add-Content $log
$wifi = Get-NetAdapter -Physical | Where-Object { $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802\.11' } | Select-Object -First 1
# Networks to try, in order. No passwords are used or stored here: only profiles Windows already saved.
# Put your preferred network names (one per line) in C:\ProgramData\projectv-wifi\profiles.txt (not in git); without that file the
# first 6 saved profiles are tried.
$pfile = Join-Path $dir 'profiles.txt'
if (Test-Path $pfile) { $profiles = @(Get-Content $pfile | Where-Object { $_.Trim() }) }
else { $profiles = @(netsh wlan show profiles | Select-String 'All User Profile\s*:\s*(.+)$' | ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() } | Select-Object -First 6) }

function Try-Step($name, [scriptblock]$act) {
    "$(Get-Date -Format s) step: $name" | Add-Content $log
    try { & $act } catch { "  step error: $($_.Exception.Message)" | Add-Content $log }
    Start-Sleep -Seconds 12
    return (Up)
}

if ($wifi) {
    $n = $wifi.Name
    $ok = Try-Step 'reconnect saved profiles (5G then 4G)' {
        netsh wlan disconnect interface="$n" | Out-Null; Start-Sleep -Seconds 3
        foreach ($p in $profiles) { netsh wlan connect name="$p" interface="$n" | Out-Null; Start-Sleep -Seconds 10; if (Up) { break } }
    }
    if (-not $ok) { $ok = Try-Step 'restart Wi-Fi adapter' { Restart-NetAdapter -Name $n -Confirm:$false -ErrorAction Stop; Start-Sleep -Seconds 10; foreach ($p in $profiles) { netsh wlan connect name="$p" interface="$n" | Out-Null; Start-Sleep -Seconds 8; if (Up) { break } } } }
    if (-not $ok) { $ok = Try-Step 'restart WLAN AutoConfig service' { Restart-Service WlanSvc -Force -ErrorAction Stop; Start-Sleep -Seconds 10; foreach ($p in $profiles) { netsh wlan connect name="$p" interface="$n" | Out-Null; Start-Sleep -Seconds 8; if (Up) { break } } } }
    if (-not $ok) { $ok = Try-Step 'renew IP address' { ipconfig /release "$n" | Out-Null; ipconfig /renew "$n" | Out-Null; ipconfig /flushdns | Out-Null } }
}
if (Up) { "$(Get-Date -Format s) recovered" | Add-Content $log; Remove-Item $flag -Force }
else    { "$(Get-Date -Format s) still offline after all steps (router may be down); will retry in 2 min" | Add-Content $log }
