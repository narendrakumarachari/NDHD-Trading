# Running NDHD locally

How to set up, start, check and stop the whole app on Windows: the trading
engine, the dashboard backend, and the dashboard frontend.

All commands are **PowerShell**, run from the repository root
(`F:\Ex_Data\projects\NDHD-Trading`) unless a step says otherwise.

## What runs

| Part | What it is | Command | Where you see it |
| --- | --- | --- | --- |
| **Backend** | FastAPI service: REST API, live WebSocket, and it serves the frontend | `uvicorn api.main:app` | http://127.0.0.1:8000/docs |
| **Frontend** | The dashboard page (TypeScript + React), compiled into `web/dist/` and served by the backend | `npm run build` once, then nothing extra | http://127.0.0.1:8000/ |
| **Trading engine** | `portfolio_engine.py`: the strategies, risk checks and orders (paper by default) | `python portfolio_engine.py` | Dashboard "Live engine console" |
| Congress data | Official House filings, pulled only when you click **Pull latest filings** | (from the dashboard) | Dashboard "Congress trades" panel |

The backend and the engine are **separate processes**. The dashboard works
without the engine (you just won't see its log or heartbeat), and the engine
works without the dashboard.

```text
 browser ──► backend :8000 ──► Alpaca (paper)       engine ──► Alpaca (paper)
              │  serves web/dist (frontend)            │
              └── reads state, logs, data/ ◄───────────┘ writes state, logs, heartbeat
```

## 1. One-time setup

### 1.1 Python 3.14 environment (backend and engine)

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock.txt
python -m pip check
```

If PowerShell refuses to run `Activate.ps1` (execution policy), skip
activation and call the venv's Python directly everywhere below:
`.\.venv\Scripts\python.exe ...`.

`requirements.lock.txt` pins every package with hashes; never `pip install`
packages by name into this environment. Developer tools (the lock generator
and the vulnerability scanner) live in a separate venv so they never mix into
it:

```powershell
py -3.14 -m venv .venv-tools
.\.venv-tools\Scripts\python.exe -m pip install uv==0.12.22 pip-audit==2.10.1
``` To change a dependency, see
"Dependency rules" in `.github/skills/ndhd-trading-developer/SKILL.md`.

### 1.2 Node.js 24 (frontend build only)

The frontend needs Node 24 or newer to compile. If your system Node is older
(this machine's is v10), use a portable copy in `.tools\node`. It is
git-ignored and affects nothing else on the machine.

```powershell
$v = "v24.21.0"; $name = "node-$v-win-x64"
New-Item -ItemType Directory -Force .tools | Out-Null
Set-Content .tools\.gitignore "*" -Encoding utf8
curl.exe -sSfL -o ".tools\$name.zip" "https://nodejs.org/dist/$v/$name.zip"
curl.exe -sSfL -o .tools\SHASUMS256.txt "https://nodejs.org/dist/$v/SHASUMS256.txt"
$expected = ((Get-Content .tools\SHASUMS256.txt) | Where-Object { $_ -match "  $name.zip$" }).Split(" ")[0]
$actual = (Get-FileHash ".tools\$name.zip" -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) { throw "Checksum mismatch - do not use this download" }
Expand-Archive ".tools\$name.zip" -DestinationPath .tools -Force
Rename-Item ".tools\$name" node
Remove-Item ".tools\$name.zip"
.\.tools\node\node.exe --version     # v24.21.0
```

In any new PowerShell window where you want `node`/`npm`, put it on the PATH
first:

```powershell
$env:PATH = "$PWD\.tools\node;$env:PATH"
```

### 1.3 Build the frontend

```powershell
$env:PATH = "$PWD\.tools\node;$env:PATH"
cd web
npm ci --ignore-scripts      # exact versions from package-lock.json; no install scripts run
npm run build                # strict type-check + compile src/*.tsx -> dist/
cd ..
```

Rebuild after changing anything in `web/src/`. If `web/dist/` is missing, the
page is blank and the backend logs a warning at startup.

### 1.4 Configuration (`.env`)

Create `.env` in the repository root. **Never commit it, and use paper
credentials.** The values below are placeholders.

```dotenv
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_PAPER=true
LIVE_TRADING=false

STOCK_TICKERS=AAPL,MSFT
WHEEL_TICKERS=IBM
STATE_FILE=portfolio_engine_state.json

# Optional
CONGRESS_CONTEXT_ENABLED=false   # true = engine logs/alerts congress clusters (advisory only)
DASHBOARD_API_KEY=               # set a long random value if the dashboard is ever reachable beyond 127.0.0.1
```

Both the engine and the backend read this file. See `README.md` for every
tunable. Keep `STATE_FILE` different from the one `alpaca_wheel_strategy.py`
uses (its default is `wheel_state.json`); the two engines' state files are
not compatible.

## 2. Start it

Use **two PowerShell windows**, both at the repository root with the venv
activated (`.\.venv\Scripts\Activate.ps1`).

### Window 1: backend and frontend

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/** for the dashboard and
**http://127.0.0.1:8000/docs** for the API reference. Keep `--host 127.0.0.1`:
without `DASHBOARD_API_KEY` the API has no authentication, so it must not be
reachable from other machines.

### Window 2: trading engine (paper)

```powershell
python portfolio_engine.py
```

To turn on congress alerts for this run only (without editing `.env`):

```powershell
$env:CONGRESS_CONTEXT_ENABLED = "true"; python portfolio_engine.py
```

Check the first lines say `Paper=True LiveTrading=False`. **While the
market is open, the engine places paper orders on its own.** Stop it if you
only want to look at the dashboard.

## 3. Check that it's working

```powershell
# Backend up? (expects "ok": true, "paper": true, "live_trading": false)
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health).Content

# Frontend served?
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/dist/app.js).StatusCode    # 200

# Engine running? (reads the engine's heartbeat; expects running = True)
((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/congress).Content | ConvertFrom-Json).engine
```

In the browser you should see:

- the **PAPER** badge and a green **Live** dot in the top bar;
- equity, buying power and the risk panel filled in;
- the engine's log lines in **Live engine console** (if the engine is running);
- "Congress data: today" (or how old it is) in the top bar.

If `DASHBOARD_API_KEY` is set, add `-Headers @{ "X-API-Key" = "<key>" }` to
the `Invoke-WebRequest` calls, and paste the key into the dashboard's
⚙ Settings.

## 4. Congress trade data

- Click **Pull latest filings** in the "Congress trades" panel. The first pull
  downloads the last 60 days of House filings (a few minutes: one request
  every 2 seconds). Later pulls fetch only what's new; if nothing changed it is
  a single small request.
- Everything pulled is kept in `data\house\` (git-ignored, personal use only).
- The same pull from the command line:

  ```powershell
  python -m congress_trades.build_dashboard congress_trades\sample\raw_congressflow.csv --house
  ```

- The engine re-reads the data file when it changes. If the data is more than
  3 business days old, the engine ignores it and the panel says "stale".

How the data flows: [`docs/congress-data-flow.svg`](congress-data-flow.svg).

## 5. Stop it

Press **Ctrl+C** in each window. If the engine was closed some other way it
leaves `portfolio_engine.lock`; the next start sees that the process is gone
and reclaims it (log: `Stale lock file found ... Reclaiming`).

## 6. Working on the code

| Task | Command |
| --- | --- |
| Backend reloads on save | `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload` |
| Frontend recompiles on save | `cd web; npm run watch` (then refresh the browser) |
| Type-check the frontend only | `cd web; npm run typecheck` |
| Python tests | `python -m unittest discover -s congress_trades -t .` |
| Engine syntax check | `python -m py_compile portfolio_engine.py alpaca_wheel_strategy.py` |
| Vulnerability scans | `.\.venv-tools\Scripts\pip-audit.exe -r requirements.lock.txt --require-hashes --strict` and `cd web; npm audit` |

CI (`.github/workflows/ci.yml`) runs the tests, the strict frontend build and
both vulnerability scans on every push.

## 7. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Blank page | `web\dist\` not built. Run step 1.3. |
| "Reconnecting…" banner | Backend not running or restarted. Start window 1; the page reconnects by itself. |
| 503 "Alpaca credentials are not configured" | `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` missing from `.env`. Fix, then restart the backend. |
| 401 "Missing or invalid X-API-Key" | `DASHBOARD_API_KEY` is set. Paste it into ⚙ Settings or send the header. |
| Port 8000 already in use | Another backend is running: `Get-NetTCPConnection -LocalPort 8000` shows its process. Stop it, or use `--port 8001`. |
| Live engine console stays empty | The engine isn't running, or it writes a different `LOG_FILE` from the one the backend reads. |
| "Engine advisory: Not running" | No heartbeat in the last few minutes. Start window 2. |
| Congress panel says "stale" or "no data" | Click **Pull latest filings**. |
| `npm` / `node` not found, or "Unsupported engine" | Put the portable Node on PATH (step 1.2); system Node 10 is too old. |
| Prices show "(stale)" and a market-closed note | Normal outside market hours. |
| Services stopped by themselves | Check free memory; close other heavy apps and start them again. |

## Files and ports

| Item | Default |
| --- | --- |
| Dashboard and API | `127.0.0.1:8000` |
| Engine state | `STATE_FILE` from `.env` |
| Engine log (shown in the dashboard) | `portfolio_engine.log` |
| Alerts feed | `portfolio_engine_events.jsonl` |
| Engine heartbeat | `portfolio_engine_status.json` |
| Single-instance lock | `portfolio_engine.lock` |
| Congress data | `data\congress_trades.json`, `data\congress-trade-ledger.html`, `data\house\` |

All of these are git-ignored.
