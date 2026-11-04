# 15-min Strike Desk alignment + Telegram status ping. No orders. No tokens in this file.
# LIVE path for StrikeDeskHealth. Severity is LATEST Analyzer tick only.
# Registered as task StrikeDeskHealth (15-minute repetition).
#   $script = "D:\ATZ\zerotouchalgo RT\openalgo-wt-tick-status\strike_desk\scripts\desk-health-hourly.ps1"
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -File `"$script`""
#   $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 9999)
#   Register-ScheduledTask -TaskName "StrikeDeskHealth" -Action $action -Trigger $trigger -Force
# Or leave this process running:
#   uv run python -m strike_desk.desk_health --remote --every-15 --alert

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& uv run python -m strike_desk.desk_health --remote --alert --write-ticks
exit $LASTEXITCODE
