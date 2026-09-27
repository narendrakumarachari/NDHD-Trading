# NDHD Trading Dashboard

A live-updating dashboard for the trading engine: account/positions/orders,
per-symbol stock and Wheel strategy state with indicators, the portfolio
risk panel (drawdown, exposure caps, sector caps, the PDT-protection status
finding from the ndhd-trading-investor skill), an alert feed with toast
notifications, and manual controls (close a position, cancel an order,
trigger the kill switch, submit a manual order) gated by the same
`RiskEngine` checks the automated strategies use.

It talks to `api/main.py` (FastAPI) over REST + a WebSocket for live pushes.

## Why there's no build step

This machine's Node.js is v10.16.3 (from 2019) with npm 6.9 - too old for
Vite, Create React App, or any current bundler. Rather than depend on a
toolchain that doesn't run here, this app loads React 18 straight from a
CDN (esm.sh) via an [import map](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/script/type/importmap)
in `index.html`, and uses [htm](https://github.com/developit/htm) (tagged
template literals bound to `React.createElement`) instead of JSX, so no
compile step is needed at all - the browser runs the `.js` files directly.
If you later have Node 18+ available, migrating this to a Vite + real JSX
setup is a natural upgrade; the component structure under `src/` would
carry over largely as-is.

## Running it

The dashboard is served as static files by the API process itself - there
is nothing separate to start.

```powershell
# from the repository root, with the project venv active
pip install -r requirements-api.txt
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000/** in a browser. Swagger is at `/docs`.

If you set `DASHBOARD_API_KEY` in the environment the API process reads
(see below), open the dashboard's ⚙ Settings panel and paste the same key
in - it's stored in `localStorage` and sent as `X-API-Key` on every
request and as a `?api_key=` query param on the WebSocket connection.

## Relevant environment variables (read by `api/main.py`)

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_API_KEY` | unset | Shared secret required in `X-API-Key`. **Unset = no auth.** Only safe on 127.0.0.1. |
| `CORS_ORIGINS` | localhost:8000/5173 | Comma list of allowed origins, only matters if you serve `web/` from somewhere other than this API. |
| `WS_FAST_INTERVAL_SECONDS` | `4` | Account/positions/orders/alerts push interval. |
| `WS_SLOW_EVERY_N_TICKS` | `8` | Strategy/indicator/risk push interval, in units of the fast interval (default ≈30s, matching the engine's own `POLL_SECONDS`). |

Everything else (Alpaca credentials, `STATE_FILE`, risk limits, ...) comes
from the same `.env` the trading engine (`portfolio_engine.py`) reads -
this API process shares that configuration and that state file.

## What this can and can't do

- **Read-only data** (account, positions, orders, strategy state,
  indicators, risk, alerts) reflects the same ground truth the trading
  engine acts on - the fast tier is a few seconds stale at most, the
  indicator/risk tier is ~engine-poll-cycle stale.
- **Closing a position / cancelling an order / the kill switch** always
  work regardless of risk limits (reducing risk is never blocked).
- **Manual order submission** can open new exposure. It runs through
  `RiskEngine.allow_stock_entry` / `allow_wheel_entry` first - same limits
  the automated strategies respect - but it is still a second, human-
  operated order-entry surface into a live-money-capable system. Treat the
  confirm checkbox and the `FLATTEN` typed-confirmation on the kill switch
  as real friction, not decoration.
- **Config edits** write to `.env` only. `portfolio_engine.py`'s `Config`
  is a frozen dataclass read once at process start - nothing here changes
  a running trading engine's behavior without restarting that process.
- **This process and the trading engine are separate.** They share a
  state file; `RiskEngine.kill_switch_active()` was patched to reload
  `kill_switch_date` from disk so a kill switch triggered here is honored
  by the running engine within one poll cycle. Nothing else about the
  engine's in-memory state is synchronized live - see
  `.github/skills/ndhd-trading-developer/SKILL.md` for the full picture.
