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

  - Only when nothing is running yet (e.g. after a restart), it first does
    upkeep: renames the engine's state file away from the old engine's
    default name (once), and updates .venv to match requirements.lock.txt.

  Each part runs in its own minimized window titled "NDHD dashboard" or
  "NDHD engine". To stop one, open its window and press Ctrl+C.
  scripts\install-autostart.ps1 runs this at every Windows sign-in.

.EXAMPLE
  .\scripts\start-ndhd.ps1
.EXAMPLE
  .\scripts\start-ndhd.ps1 -NoEngine      # dashboard only
#>
param(
    [switch]$NoEngine,      # start only the dashboard
    [switch]$NoTailscale,   # skip private remote access
    [switch]$NoBrowser,     # don't open the dashboard in the browser
    [switch]$AtLogon,       # run by the sign-in task: log to logs\start-ndhd.log, wait for Tailscale
    [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($AtLogon) {
    New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
    Start-Transcript -Append -Path (Join-Path $root 'logs\start-ndhd.log') | Out-Null
}
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
if ($envState.paper_orders) {
    Say "Safety check: paper account only, live trading off. PAPER_ORDERS=true: orders go to the Alpaca paper account (simulated money)." Green
} else {
    Say "Safety check: paper account only, live trading off. Dry run: orders are only logged." Green
}

function Test-Listening { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function Get-Engine {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*portfolio_engine.py*' }
}

# --- 1b. Upkeep, only while nothing is running (files and packages aren't in use)
# The engine's state file (positions, wheel phases, kill switch date) used the
# old engine's default name, wheel_state.json, so running alpaca_wheel_strategy.py
# could overwrite it. Move it to portfolio_engine_state.json, once.
function Rename-StateFile {
    $envFile = Join-Path $root '.env'
    $lines = @(Get-Content $envFile)
    $oldLine = '^\s*STATE_FILE\s*=\s*["'']?wheel_state\.json["'']?\s*$'
    if (-not ($lines -match $oldLine)) { return }
    $old = Join-Path $root 'wheel_state.json'
    $new = Join-Path $root 'portfolio_engine_state.json'
    if (Test-Path $new) {
        Say "State file: not renamed, because portfolio_engine_state.json already exists. Compare it with wheel_state.json by hand." Yellow
        return
    }
    if (Test-Path $old) {
        $state = Get-Content $old -Raw | ConvertFrom-Json
        if (-not ($state.PSObject.Properties.Name -contains 'stocks')) {
            Say "State file: not renamed, because wheel_state.json isn't in the portfolio engine's format." Yellow
            return
        }
        Copy-Item $old $new
        if ((Get-FileHash $old).Hash -ne (Get-FileHash $new).Hash) { Remove-Item $new; throw "copy of wheel_state.json didn't match" }
    }
    $out = $lines | ForEach-Object { if ($_ -match $oldLine) { 'STATE_FILE=portfolio_engine_state.json' } else { $_ } }
    [IO.File]::WriteAllLines($envFile, [string[]]$out, (New-Object Text.UTF8Encoding($false)))
    if (Test-Path $old) {
        $backup = "wheel_state.json.renamed-$(Get-Date -Format yyyyMMdd)"
        Rename-Item $old $backup
        Say "State file: now portfolio_engine_state.json (same contents, kill switch date kept; old copy: $backup)." Green
    } else {
        Say "State file: .env now uses portfolio_engine_state.json." Green
    }
}

function Update-Packages {
    $stale = @(& $python (Join-Path $root 'scripts\lock_check.py'))
    if ($LASTEXITCODE -eq 0) { Say "Packages: match the lock."; return }
    Say "Packages: updating $($stale.Count) to match the lock: $($stale -join '; ')"
    & $python -m pip install --quiet --disable-pip-version-check --require-hashes -r (Join-Path $root 'requirements.lock.txt')
    if ($LASTEXITCODE -eq 0) { Say "Packages: updated." Green }
    else { Say "Packages: update failed (exit $LASTEXITCODE); starting with the installed ones. See docs\RUNNING.md, step 1.1." Yellow }
}

if ((Test-Listening) -or (Get-Engine)) {
    Say "Upkeep: skipped while NDHD is running; it runs at the next start from a stopped state."
} else {
    try { Rename-StateFile } catch { Say "State file: not renamed ($($_.Exception.Message)); the engine keeps using the old name." Yellow }
    try { Update-Packages } catch { Say "Packages: check failed ($($_.Exception.Message)); starting with the installed ones." Yellow }
}

# --- 2. Dashboard (backend + frontend), localhost only
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
$engine = Get-Engine
if ($NoEngine) {
    Say "Engine: skipped (-NoEngine)."
} elseif ($engine) {
    Say "Engine: already running."
} else {
    $cmd = "`$host.UI.RawUI.WindowTitle = 'NDHD engine'; & '$python' portfolio_engine.py"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $cmd -WorkingDirectory $root -WindowStyle Minimized
    if ($envState.paper_orders) {
        Say "Engine: started. While the market is open it places orders in the Alpaca paper account on its own." Green
    } else {
        Say "Engine: started in dry run. It only logs the orders it would place; nothing is sent, not even to the paper account." Green
    }
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
        # Right after sign-in Tailscale may still be connecting; give it a minute.
        for ($i = 0; $AtLogon -and $status.BackendState -ne 'Running' -and $i -lt 12; $i++) {
            Start-Sleep -Seconds 5
            $status = & $ts status --json 2>$null | ConvertFrom-Json
        }
        if ($status.BackendState -ne 'Running') {
            Say "Tailscale: not signed in. Click the Tailscale icon by the clock and log in, then run this again." Yellow
        } elseif (-not $status.CertDomains) {
            # Without HTTPS certificates, 'tailscale serve' waits until they're enabled; don't hang here.
            Say "Tailscale: turn on HTTPS certificates at https://login.tailscale.com/admin/dns, then run this again." Yellow
        } else {
            & $ts serve --bg $port | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "tailscale serve failed (exit $LASTEXITCODE). Run 'tailscale serve --bg $port' to see why." }
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
