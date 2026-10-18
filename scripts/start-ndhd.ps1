<#
.SYNOPSIS
  Starts NDHD on this Windows PC with one command: the dashboard, the paper
  trading engine, and private access from your laptop and phone (Tailscale).

.DESCRIPTION
  Safe to run again at any time: anything already running is left alone.
  - Refuses to start the engine unless .env says paper trading and live
    trading is off.
  - The dashboard listens on 127.0.0.1 only. Your other devices reach it
    through Tailscale Serve, which is private to your own tailnet and needs
    DASHBOARD_API_KEY to be set. It never uses Tailscale Funnel (public).
  - Never touches the kill switch, risk limits or any .env value.

  Each part runs in its own minimized window titled "NDHD dashboard" or
  "NDHD engine". To stop one, open its window and press Ctrl+C.

.EXAMPLE
  .\scripts\start-ndhd.ps1
.EXAMPLE
  .\scripts\start-ndhd.ps1 -NoEngine      # dashboard only
#>
param(
    [switch]$NoEngine,      # start only the dashboard
    [switch]$NoTailscale,   # skip private remote access
    [switch]$NoBrowser,     # don't open the dashboard in the browser
    [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
$port = $Port
$local = "http://127.0.0.1:$port/"

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

if (-not (Test-Path $python)) { throw "No .venv found. Set it up first: docs\RUNNING.md, step 1.1." }
if (-not (Test-Path (Join-Path $root 'web\dist\app.js'))) {
    Say "web\dist is not built, so the page will be blank. Build it: docs\RUNNING.md, step 1.3." Yellow
}

# --- 1. Safety check: read .env the way the engine does (yes/no only, no values)
$envState = & $python (Join-Path $root 'scripts\env_check.py') | ConvertFrom-Json
if (-not $envState.env_file) { throw "No .env file. Create it first: docs\RUNNING.md, step 1.4." }
if ($envState.live_trading -or -not $envState.paper) {
    throw "Stopped: .env is not set for paper trading (ALPACA_PAPER=true, LIVE_TRADING=false). This script only starts paper trading."
}
Say "Safety check: paper trading on, live trading off." Green

# --- 2. Dashboard (backend + frontend), localhost only
function Test-Listening { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
if (Test-Listening) {
    Say "Dashboard: already running on port $port."
} else {
    $cmd = "`$host.UI.RawUI.WindowTitle = 'NDHD dashboard'; & '$python' -m uvicorn api.main:app --host 127.0.0.1 --port $port"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $cmd -WorkingDirectory $root -WindowStyle Minimized
    $up = $false
    foreach ($i in 1..40) {
        try { Invoke-WebRequest -UseBasicParsing "${local}api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Milliseconds 750 }
    }
    if ($up) { Say "Dashboard: started." Green } else { throw "The dashboard did not start. Open the 'NDHD dashboard' window to see why." }
}

# --- 3. Trading engine (paper)
$engine = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*portfolio_engine.py*' }
if ($NoEngine) {
    Say "Engine: skipped (-NoEngine)."
} elseif ($engine) {
    Say "Engine: already running."
} else {
    $cmd = "`$host.UI.RawUI.WindowTitle = 'NDHD engine'; & '$python' portfolio_engine.py"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $cmd -WorkingDirectory $root -WindowStyle Minimized
    Say "Engine: started in paper mode. While the market is open it places paper orders on its own." Green
}

# --- 4. Private access from your laptop and phone (Tailscale Serve, tailnet only)
$phoneUrl = $null
if (-not $NoTailscale) {
    $ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
    if (-not $ts -and (Test-Path 'C:\Program Files\Tailscale\tailscale.exe')) { $ts = 'C:\Program Files\Tailscale\tailscale.exe' }
    if (-not $ts) {
        Say "Tailscale: not installed, so only this PC can open the dashboard. See docs\REMOTE_ACCESS.md." Yellow
    } elseif (-not $envState.api_key_set) {
        Say "Tailscale: skipped. Set DASHBOARD_API_KEY in .env first (docs\REMOTE_ACCESS.md, step 2), then run this again." Yellow
    } else {
        $status = & $ts status --json 2>$null | ConvertFrom-Json
        if ($status.BackendState -ne 'Running') {
            Say "Tailscale: not signed in. Click the Tailscale icon by the clock and log in, then run this again." Yellow
        } else {
            & $ts serve --bg $port | Out-Null
            $phoneUrl = "https://$($status.Self.DNSName.TrimEnd('.'))/"
            $serve = & $ts serve status --json 2>$null | Out-String
            if ($serve -match '"AllowFunnel"\s*:\s*\{[^}]*true') {
                Say "WARNING: Tailscale Funnel is on, which makes the dashboard public on the internet. Turn it off: tailscale funnel reset" Red
            }
            Say "Tailscale: private link ready (your devices only)." Green
        }
    }
}

# --- 5. Summary
Say ""
Say "On this PC:          $local" Cyan
if ($phoneUrl) { Say "Laptop and phone:    $phoneUrl   (first time on a device: paste your API key in Settings)" Cyan }
Say "Stop a part: open its 'NDHD dashboard' or 'NDHD engine' window and press Ctrl+C."
if (-not $NoBrowser) { Start-Process $local }
