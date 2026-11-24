# Run ONCE in an Administrator PowerShell:  powershell -ExecutionPolicy Bypass -File C:\dev\project-v\tools\ops\install_wifi_keepalive.ps1
# Installs the scheduled task "projectv-wifi-keepalive" (every 2 min, SYSTEM) and stops the laptop sleeping on battery.
# Undo: Unregister-ScheduledTask projectv-wifi-keepalive -Confirm:$false
$ErrorActionPreference = 'Stop'
$script = 'C:\dev\project-v\tools\ops\wifi_keepalive.ps1'
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
Register-ScheduledTask -TaskName 'projectv-wifi-keepalive' -Action $act -Trigger $trg -Settings $set -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
# do not sleep on battery (lid already does nothing); hibernate still protects work at critical battery
powercfg /change standby-timeout-dc 0
powercfg /change standby-timeout-ac 0
# let Windows keep the Wi-Fi card powered
Get-NetAdapter -Physical | Where-Object { $_.InterfaceDescription -match 'Wi-?Fi|Wireless' } | ForEach-Object { try { Set-NetAdapterPowerManagement -Name $_.Name -AllowComputerToTurnOffDevice Disabled -ErrorAction Stop } catch { Write-Host "adapter power setting skipped: $($_.Exception.Message)" } }
Write-Host 'Installed. Log: C:\ProgramData\projectv-wifi\keepalive.log'
