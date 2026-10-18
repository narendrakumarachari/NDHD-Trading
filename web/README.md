# NDHD Trading Dashboard

A live-updating dashboard for the trading engine: account/positions/orders,
per-symbol stock and Wheel strategy state with indicators, the portfolio
risk panel (drawdown, exposure caps, sector caps, the PDT-protection status
finding from the ndhd-trading-investor skill), an alert feed with toast
notifications, and manual controls (close a position, cancel an order,
trigger the kill switch, submit a manual order) gated by the same
`RiskEngine` checks the automated strategies use.

It talks to `api/main.py` (FastAPI) over REST + a WebSocket for live pushes.

## How it's built

The UI is **TypeScript + React 18 (TSX)** under `src/`, type-checked in
strict mode and compiled by `tsc` (TypeScript 7) into `dist/`. There is no
bundler: `tsc` emits one ES module per source file, and `index.html` loads
`dist/app.js`. React itself still comes from esm.sh through the
[import map](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/script/type/importmap)
in `index.html`, so `npm` is only a build-time tool.

| Path | What it is |
| --- | --- |
| `src/types.ts` | The API's response shapes, mirroring `api/schemas.py`. Update both together. |
| `src/api.ts` | REST client, the `/ws` live-data hook, and a polling hook for slow data |
| `src/app.tsx` | Page layout and actions |
| `src/components/*.tsx` | One file per panel, including `CongressPanel.tsx` |
| `package.json` / `package-lock.json` | Exact pins (`typescript`, `@types/react` 18, `@types/react-dom` 18) |
| `dist/`, `node_modules/` | Generated, git-ignored |

The machine's system Node.js is v10 (2019), too old for current
TypeScript, so the repo uses a portable Node 24 LTS in `.tools/node`
(git-ignored, downloaded from nodejs.org and SHA-256 checked). Any Node 24+
works.

## Running it

Full guide (setup, both services, checks, troubleshooting):
[`docs/RUNNING.md`](../docs/RUNNING.md). Short version:

The dashboard is served as static files by the API process itself. Build
it once (and again after changing anything in `src/`), then start the API:

```powershell
# from the repository root
$env:PATH = "$PWD\.tools\node;$env:PATH"      # or any Node 24+
cd web; npm ci --ignore-scripts; npm run build; cd ..

# with the project venv active
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`npm run watch` recompiles on save while you edit. If `dist/` is missing,
the API logs a warning and the page stays blank.

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

## Congress trades panel

`GET /api/congress` (`api/routers/congress.py` → `congress_trades/view.py`)
feeds the "Congress trades" section. It loads once when the page opens and
again after a pull, never on a timer. The compact view shows the data's age,
whether the engine's advisory rule would fire for each symbol it trades or
holds (and why not), and the running engine's own advisory setting (from its
heartbeat file). "Show research details" (remembered per browser) adds the
most-traded stocks, latest official filings and filings that need a human.
Strategy cards carry a one-line congress badge, and the top bar shows the
data's age.

**Pull latest filings** calls `POST /api/congress/refresh`, which runs one
incremental pull in the background (only new filings are downloaded and
read; everything pulled before is kept) and `GET /api/congress/refresh`
reports progress. It writes data files only. `/congress/ledger` serves the
full ledger page and `/congress/how-it-works` the picture in
`docs/congress-data-flow.svg`. Nothing in this panel can place, size, block
or change an order.

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
