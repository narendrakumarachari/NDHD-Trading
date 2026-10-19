<#
.SYNOPSIS
  Moves trading from this PC to the NDHD server, so exactly one engine
  trades the Alpaca paper account.

.DESCRIPTION
  Order matters: two engines must never run at once, and the server
  engine must start with this PC's state file (positions it manages, Wheel
  phases, kill switch date).

    1. Checks the server's dashboard answers over Tailscale with your API
       key, and that the server's engine is NOT running yet (run
       setup-server.sh --no-engine first).
    2. Refuses while the market is open, unless -Force.
    3. Stops this PC's engine and dashboard, removes the start-at-sign-in
       task, and stops sharing this PC's dashboard over Tailscale. From
       now on every device uses the server's dashboard and kill switch.
    4. Copies the state file to the server (sync-to-server.ps1 -State).
    5. Tells you the one command that starts the server's engine.

  -Undo brings trading back to this PC: run it only after stopping the
  server's engine (sudo systemctl disable --now ndhd-engine), and copy the
  server's state file back first if it has positions.

.EXAMPLE
  .\scripts\switch-to-server.ps1 -Server ndhd-server -Key $HOME\.ssh\oracle-ndhd.key
.EXAMPLE
  .\scripts\switch-to-server.ps1 -Undo
#>
param(
    [string]$Server = 'ndhd-server',
    [string]$Key,
    [string]$User = 'ubuntu',
    [string]$ServerUrl,
    [switch]$Force,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

if ($Undo) {
    Say "Bringing trading back to this PC. The server's engine must already be stopped:" Yellow
    Say "  ssh to the server and run: sudo systemctl disable --now ndhd-engine" Yellow
    & (Join-Path $PSScriptRoot 'install-autostart.ps1')
    & (Join-Path $PSScriptRoot 'start-ndhd.ps1') -NoBrowser
    exit 0
}

if (-not $Key) { throw "Pass -Key: the path to the SSH private key for the server." }

# --- 1. The server's dashboard is up, and its engine is not running yet.
if (-not $ServerUrl) {
    $suffix = (& $ts status --json | ConvertFrom-Json).MagicDNSSuffix
    $ServerUrl = "https://$Server.$suffix/"
}
$apiKey = (Get-Content (Join-Path $root '.env') | Where-Object { $_ -match '^\s*DASHBOARD_API_KEY\s*=' } | Select-Object -First 1) -replace '^\s*DASHBOARD_API_KEY\s*=\s*', ''
$headers = @{ 'X-API-Key' = $apiKey.Trim().Trim('"') }
try {
    $health = Invoke-RestMethod -Uri "${ServerUrl}api/health" -TimeoutSec 20
    $congress = Invoke-RestMethod -Uri "${ServerUrl}api/congress" -Headers $headers -TimeoutSec 60
} catch {
    throw "The server's dashboard didn't answer at $ServerUrl ($($_.Exception.Message)). Is setup-server.sh done and the server in your tailnet?"
}
if (-not $health.authenticated_api) { throw "The server's dashboard has no API key set; fix its .env first." }
if ($congress.engine.running) {
    throw "The server's engine is already running. Stop it first (sudo systemctl stop ndhd-engine), so two engines never trade at once."
}
Say "Server dashboard OK at $ServerUrl; its engine is not running yet." Green

# --- 2. Not while the market is open (an order could be mid-flight).
$isOpen = (& $python -c "import logging, portfolio_engine as pe; logging.disable(logging.CRITICAL); print(pe.AlpacaClient(pe.CONFIG).get_clock().get('is_open'))" 2>$null | Select-Object -Last 1).Trim()
if ($isOpen -eq 'True' -and -not $Force) {
    throw "The market is open. Switch after the close, or pass -Force if you're sure no order is in flight."
}

# --- 3. Stop this PC's engine and dashboard; stop sharing it; no autostart.
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*portfolio_engine.py*' -or $_.CommandLine -like '*uvicorn api.main:app*' }
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
$owner = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess
if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue }
Say "Stopped this PC's engine and dashboard ($(@($procs).Count) processes)." Green
& (Join-Path $PSScriptRoot 'install-autostart.ps1') -Remove
& $ts serve reset | Out-Null
Say "This PC no longer shares a dashboard over Tailscale." Green

# --- 4. Hand the state file to the server.
& (Join-Path $PSScriptRoot 'sync-to-server.ps1') -Server $Server -Key $Key -User $User -State

# --- 5. Start the server's engine.
Say ""
Say "Last step, on the server:  sudo systemctl enable --now ndhd-engine" Cyan
Say "Then use $ServerUrl on every device (paste your API key in its Settings once)." Cyan
Say "To move back to this PC later: .\scripts\switch-to-server.ps1 -Undo" Gray
