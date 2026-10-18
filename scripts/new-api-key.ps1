<#
.SYNOPSIS
  Creates the dashboard password (DASHBOARD_API_KEY) in .env and copies it to
  the clipboard. The key is never printed.

.DESCRIPTION
  32 random bytes from the operating system's secure random generator,
  written as a 43-character URL-safe string. If .env already has a key, this
  does nothing unless you pass -Replace (then every device needs the new key).
  Restart the dashboard afterwards so it reads the key.

.EXAMPLE
  .\scripts\new-api-key.ps1
.EXAMPLE
  .\scripts\new-api-key.ps1 -Replace     # new key, e.g. after losing a device
#>
param([switch]$Replace)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) { throw "No .env file in $root. Create it first: docs\RUNNING.md, step 1.4." }

$lines = @(Get-Content $envFile)
$hasKey = @($lines | Where-Object { $_ -match '^\s*DASHBOARD_API_KEY\s*=\s*[^\s#]' }).Count -gt 0
if ($hasKey -and -not $Replace) {
    Write-Host "A dashboard key already exists in .env; nothing changed." -ForegroundColor Yellow
    Write-Host "To make a new one (every device must then use it): .\scripts\new-api-key.ps1 -Replace"
    exit 0
}

$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$key = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

# Keep every other line as it is; replace any old (or empty) key line.
$kept = @($lines | Where-Object { $_ -notmatch '^\s*DASHBOARD_API_KEY\s*=' })
$kept += "DASHBOARD_API_KEY=$key"
[IO.File]::WriteAllLines($envFile, [string[]]$kept, (New-Object Text.UTF8Encoding($false)))
Set-Clipboard -Value $key

Write-Host "Done: the key is saved in .env and copied to your clipboard (not shown here)." -ForegroundColor Green
Write-Host "Next: restart the dashboard, then paste the key in the dashboard's Settings (gear icon, top right)."
