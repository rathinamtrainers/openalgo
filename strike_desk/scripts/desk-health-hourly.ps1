# Hourly Strike Desk alignment + Telegram alert. No orders. No tokens in this file.
# Register once (current user) — path has a space, so use Register-ScheduledTask:
#   $script = "D:\ATZ\zerotouchalgo RT\openalgo-wt-desk-health\strike_desk\scripts\desk-health-hourly.ps1"
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -File `"$script`""
#   $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 9999)
#   Register-ScheduledTask -TaskName "StrikeDeskHealth" -Action $action -Trigger $trigger -Force
# Or leave this process running:
#   uv run python -m strike_desk.desk_health --remote --hourly --alert

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& uv run python -m strike_desk.desk_health --remote --alert
exit $LASTEXITCODE
