# scripts/register_refresh_task.ps1
# Creates a Windows Task Scheduler entry to run refresh_osm.ps1 on the 1st of every month at 02:00 AM.
#
# Run this once in PowerShell as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts/register_refresh_task.ps1
#
# NOTE: New-ScheduledTaskTrigger has no -Monthly option, so the old version of
# this script silently fell back to a DAILY trigger.  schtasks.exe supports a
# real monthly schedule, so it is used here and its exit code is checked.

$ErrorActionPreference = "Stop"

$TaskName = "NepalOSM_MonthlyRefresh"
$ScriptPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "refresh_osm.ps1"

Write-Host "Registering monthly Task Scheduler job: $TaskName"
Write-Host "Target script: $ScriptPath"

# /TR value: the whole command line as one string, with the script path quoted.
$Command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

# /SC MONTHLY /D 1 = day 1 of every month; /F overwrites an existing task.
schtasks.exe /Create /TN $TaskName /TR $Command /SC MONTHLY /D 1 /ST 02:00 /F
if ($LASTEXITCODE -ne 0) {
    throw "schtasks /Create failed with exit code $LASTEXITCODE (run this script as Administrator)"
}

# Run a missed job as soon as possible (e.g. the PC was off at 02:00) and allow
# it on battery.
$task = Get-ScheduledTask -TaskName $TaskName
$task.Settings.StartWhenAvailable = $true
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task | Set-ScheduledTask | Out-Null

Write-Host "Task $TaskName registered successfully (monthly, day 1, 02:00)!" -ForegroundColor Green
Write-Host "Verify with: schtasks /Query /TN $TaskName /V /FO LIST" -ForegroundColor Gray
