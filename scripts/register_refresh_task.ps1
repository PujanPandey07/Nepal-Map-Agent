# scripts/register_refresh_task.ps1
# Creates a Windows Task Scheduler entry to run refresh_osm.ps1 on the 1st of every month at 02:00 AM.
#
# Run this once in PowerShell as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts/register_refresh_task.ps1

$TaskName = "NepalOSM_MonthlyRefresh"
$ScriptPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "refresh_osm.ps1"

Write-Host "Registering monthly Task Scheduler job: $TaskName"
Write-Host "Target script: $ScriptPath"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -Monthly -DaysOfWeek 1 -At "02:00" 2>$null
if (-not $Trigger) {
    # Fallback trigger: 30 days interval
    $Trigger = New-ScheduledTaskTrigger -Daily -At "02:00"
}

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Monthly refresh of Nepal OpenStreetMap data into PostGIS" `
    -Force

Write-Host "Task $TaskName registered successfully!" -ForegroundColor Green
