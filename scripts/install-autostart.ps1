<#
.SYNOPSIS
  Starts NDHD automatically every time you sign in to Windows.

.DESCRIPTION
  Adds a Task Scheduler task, "NDHD start at sign-in", for your user only. 30
  seconds after you sign in it runs scripts\start-ndhd.ps1 -AtLogon, which
  does the usual safety check (paper trading only), any pending upkeep, and
  starts the dashboard, the engine and the private Tailscale link. Its output
  goes to logs\start-ndhd.log.

  No administrator rights needed, and nothing system-wide changes. Running it
  again replaces the task. -Remove deletes it.

.EXAMPLE
  .\scripts\install-autostart.ps1
.EXAMPLE
  .\scripts\install-autostart.ps1 -Remove
#>
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$name = 'NDHD start at sign-in'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed: NDHD no longer starts at sign-in." -ForegroundColor Green
    exit 0
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$script = Join-Path $root 'scripts\start-ndhd.ps1'
# -ExecutionPolicy Bypass applies to this one PowerShell process only.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $root `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`" -AtLogon"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT30S'   # let Wi-Fi and Tailscale come up first
# No time limit, so Task Scheduler never stops the dashboard or engine it started.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Starts the NDHD dashboard, paper engine and Tailscale link (scripts\start-ndhd.ps1).' -Force | Out-Null
Write-Host "Done: NDHD starts 30 seconds after you sign in to Windows." -ForegroundColor Green
Write-Host "Log: $root\logs\start-ndhd.log.   Undo: .\scripts\install-autostart.ps1 -Remove"
