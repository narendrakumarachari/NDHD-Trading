---
name: ndhd-trading-developer
description: 'Implement, test, and safely ship changes to the NDHD Alpaca trading codebase (portfolio_engine.py, alpaca_wheel_strategy.py) once the investor/risk case is settled. Use for entry/exit logic, state transitions, order submission, persistence, reconciliation, tests, and paper-trading validation. Pairs with the ndhd-trading-investor skill, which decides whether/what to build.'
argument-hint: 'Describe the feature, bug, or investor-approved change to implement, and which runtime (portfolio_engine.py or alpaca_wheel_strategy.py) it belongs to.'
user-invocable: true
disable-model-invocation: false
---

# NDHD Trading Developer

## Purpose

Implement changes to this repository's trading engines correctly, idempotently, and testably. This skill assumes the investor case — thesis, worst case, guardrails — is already settled; use the ndhd-trading-investor skill first for "should we build this," and this skill for "how do we build it safely." A well-engineered implementation of a bad trading idea is still a bad idea; engineering polish is not a substitute for the risk review.

## Repository map

Two independent runtimes — do not conflate them, and do not silently implement a feature in only one without saying so:

- `portfolio_engine.py` — the primary, integrated engine (~4,500 lines). Owns multi-symbol stock signals, multi-symbol Wheel logic, portfolio-level risk (`RiskEngine`), persistence (`StateStore`), reconciliation, alerting (`EmailAlerter`), and the poll loop (`PortfolioEngine.run_forever`).
- `alpaca_wheel_strategy.py` — a standalone, single-ticker Wheel engine (~2,600 lines) with its own `Config`, `AlpacaClient`, `WheelState`, `StateStore`, and `WheelStrategy`. It has no stock strategy and no PDT-related code.

Both persist to a `STATE_FILE`-configured JSON path, but the schemas are **not** interchangeable — confirm which runtime a given state file belongs to before pointing both at the same path.

Landmarks inside `portfolio_engine.py` (grep by name — the file changes, line numbers won't stay accurate):

- `Config` — env-driven, frozen dataclass. Every new tunable becomes an `env_bool`/`env_float`/`env_int`/`csv_env` field with a sane default; never a bare module-global read inline.
- `EmailAlerter.send(subject, body, key=...)` — pass `key` for anything that can repeat; it dedupes on a 15-minute cooldown per key. Alerts degrade to a log line when SMTP isn't configured — never make one a hard dependency for correctness.
- `StateStore` — atomic write via temp-file + `Path.replace`. New persisted fields go on the `StockState`/`WheelState`/`PortfolioState` dataclasses. There is no schema migration: `StateStore.load()` unpacks raw JSON straight into `**data`, so renaming or removing a field breaks loading of existing state files.
- `ProcessLock` — PID-file single-instance guard. Don't bypass it to test something against a state file another running instance owns.
- `RiskEngine` — all pre-trade checks (`allow_stock_entry`, `allow_wheel_entry`) plus post-trade enforcement (`kill_switch_active`/`trigger_kill_switch`, `enforce_exposure_limits`). New risk checks belong here, not scattered inline in the strategy classes.
- `WashSaleTracker` — informational only, by design. Do not turn this into a blocking control (see the investor skill for why).
- `StockStrategy` / `WheelStrategy` — `process_symbol()` is the per-symbol entry point the poll loop calls each cycle. It re-fetches `positions` after every mutating call, because assignment or fills can change them mid-cycle (see `WheelStrategy.process_symbol()`). Preserve that pattern in new code — acting on stale `positions` is a real bug class here.
- `PortfolioEngine.reconcile_with_broker()` — startup-only broker/local-state diff. It resets local state when the broker shows nothing, but never silently *adopts* an unrecognized broker position — it alerts and leaves it for manual review. Keep that asymmetry; guessing how to manage a position the engine didn't know about is worse than pausing on it.
- `EmailAlerter._log_event()` — appends every alert (regardless of email config) to `EVENTS_FILE` as JSONL. Added so an out-of-process reader has something durable to tail; if you add a new alert call site, nothing else is required — it's automatic inside `send()`.
- `RiskEngine.kill_switch_active()` — reloads `kill_switch_date` from disk on every call so an out-of-process trigger (e.g. `api/routers/control.py`'s kill-switch endpoint) is honored within one poll cycle. If you add other cross-process-triggerable state, follow this same pattern rather than assuming in-memory `self.state` is authoritative.

There is now a third component: `api/` (FastAPI dashboard/control service) and `web/` (the React UI it serves, written in TypeScript/TSX and compiled by `tsc` into `web/dist/`; build it with `npm ci --ignore-scripts && npm run build` using Node 24+, e.g. the portable `.tools/node`), documented in `web/README.md`. `web/src/types.ts` mirrors `api/schemas.py`; change both together. It imports `portfolio_engine` directly and reuses `Config`/`AlpacaClient`/`StateStore`/`RiskEngine` rather than re-implementing them — see `api/deps.py`. It runs as a **separate process** from the trading engine and is **not** covered by the trading loop's guardrail review the same way — a change there that adds a new way to submit orders (e.g. `api/routers/control.py`'s manual order endpoint) needs its own investor-skill sign-off on the risk checks it applies, same as a new strategy feature would.

A fourth component, `congress_trades/`, is a read-only research pipeline, documented in `congress_trades/README.md`. It uses the standard library, plus `pypdf` in `parse_ptr.py` only.

- `fetch_house.py` downloads the official House Clerk index and PTR PDFs into `data/house/`.
- `parse_ptr.py` turns electronic PTRs into `verified` rows. Paper filings and records that don't parse go to `needs_review`, never guessed.
- `normalize.py` parses, classifies, merges official and aggregator rows, applies amendments, deduplicates and summarises.
- `build_dashboard.py` writes `data/congress_trades.json` and `data/congress-trade-ledger.html`.
- `context.py` is the engine adapter (see below).
- `sectors.py` holds the ticker-to-sector map.

It imports nothing from the engines. The only engine import of it is `congress_trades.context`, loaded lazily by `portfolio_engine.py` when `CONGRESS_CONTEXT_ENABLED=true`. `data/` has its own `.gitignore`, so its output is never committed. What the engines may do with this data is decided in the investor skill's "Congressional disclosure data" section; currently only advisory use is approved.

## Integrating `congress_trades` into an engine

Build phase 1 (advisory) this way. Do not go further without investor sign-off.

1. **One adapter, one file.** Add a small reader (e.g. `congress_trades/context.py`) that loads `data/congress_trades.json` and answers `context_for(symbol, as_of) -> dict | None`. Never let strategy code parse the JSON directly.
2. **Optional by construction.** Gate it behind a `Config` field `congress_context_enabled = env_bool("CONGRESS_CONTEXT_ENABLED", False)`. A missing, malformed or stale file (`summary.as_of` older than 3 business days) returns `None` and logs one warning per day. It never raises into the poll loop and never blocks an entry.
3. **Time by `filing_date`.** Only rows whose `filing_date <= as_of` and `source_status == "verified"` count. Re-read the file at most once per poll cycle, keyed on its modification time.
4. **Advisory outputs only.** Valid outputs are a log line or an `EmailAlerter.send(..., key=f"congress:{symbol}")` when the engine holds or is about to enter a symbol with a recent cluster or committee-overlap trade, plus a read-only field on the API. Nothing it returns may feed `RiskEngine` decisions, sizing or stops.
5. **Tests.** Cover a missing file, a stale file, malformed JSON, a trade-date-only row that must be ignored (look-ahead), an unverified row that must be ignored, and the flag off by default.

## Procedure

### 1. Establish the change boundary

1. Read the owning class/function and its call sites before editing — most behavior already has a home; extend it rather than adding a parallel path.
2. Identify whether the change is an entry decision, position management, liquidation, reconciliation, persistence, data dependency, alert, or orchestration concern — that determines which class owns it.
3. Check whether the same behavior needs to exist (or intentionally not exist) in the other runtime, and state that explicitly rather than leaving it implicit.
4. Never copy `.env` values, credentials, lock files, or live account state into code, tests, or commit messages.

### 2. Make state transitions explicit

For any stateful feature, define: valid states and transitions, the broker facts that establish each transition, startup/restart recovery, and how unknown or externally created positions are quarantined rather than adopted. Persist intent before submission — e.g. `state.entry_order_id = f"PENDING:{client_order_id}"` — not just the hoped-for final state; that's what makes crash recovery possible via `AlpacaClient.get_order_by_client_id()`.

### 3. Treat orders as events, not fire-and-forget calls

1. Validate account status, market state, data freshness, exposure, and strategy ownership first.
2. Generate a deterministic `client_order_id` from strategy + symbol + action + attempt, not a timestamp alone.
3. Persist the pending order intent before submission for anything that can create or increase risk.
4. Submit with explicit side, quantity, order type, and time-in-force; check `get_order_by_client_id()` before resubmitting after a crash instead of blindly retrying.
5. Reconcile fills — `wait_fill()` polls order status; never treat submission as a fill.
6. Persist the observed result, then advance strategy state.
7. In dry-run (`LIVE_TRADING=false`), report the synthetic `DRYRUN-` result without mutating state as if a real fill occurred.

### 4. Test for this codebase's actual failure modes

The only test suite is for `congress_trades/` (`python -m unittest discover -s congress_trades -t .`, also run by CI). It includes a subprocess check of `PortfolioEngine.congress_advisory()` and the `CONGRESS_CONTEXT_ENABLED` default, and nothing else in the engines. The trading logic and `api/` still have **no** tests, so don't claim coverage that doesn't exist. When you add engine tests, prioritize in this order:

1. pure calculations (`ema`, `atr`, `adx`, position sizing, stop math) — no API mocking needed;
2. trailing-stop monotonicity (`StockStrategy.is_tighter`) — this must never regress;
3. state transitions (`WheelStrategy.reconcile`) under assignment / call-away / manual-position scenarios;
4. crash-recovery: interrupt between intent-persist and broker-submit, then restart;
5. malformed or legacy state files loading through `StateStore.load()`;
6. API errors, rate limits (the 429 backoff in `AlpacaClient.request`), and rejected/expired/partial orders.

Run the narrowest check first, then broaden:

```powershell
python -m py_compile portfolio_engine.py alpaca_wheel_strategy.py
```

then any targeted test, then a paper-trading observation window before calling anything live-ready.

## Known gaps ready to implement

These came directly out of the current investor-skill review — pick one up once the investor case says it's worth doing; don't implement risk-affecting behavior changes without that sign-off first.

1. **PDT protection is a silent no-op (highest priority) — now visible, still not fixed.** Alpaca removed `daytrade_count`/`pattern_day_trader`/`daytrading_buying_power` from the Account API on 2026-07-06, following FINRA's retirement of the PDT rule. `RiskEngine.allow_stock_entry()`'s PDT block still always evaluates `day_trade_count = 0` and never blocks — that has not been fixed. `api/schemas.py`'s `pdt_protection_status()` / `GET /api/risk` and the dashboard's risk panel now surface this as a "degraded" status so it's no longer silent, but the underlying gate is unchanged. Replace it with a check against Alpaca's Intraday Margin Framework (real-time `buying_power` plus Intraday Margin Deficit tracking), or explicitly retire `PDT_PROTECTION` and document why — don't leave a config flag that looks active but does nothing.
2. **T+1 cash-availability gap.** `RiskEngine.allow_wheel_entry()` compares Wheel collateral to `account.get("cash")`, which may include proceeds from a same-day sale that haven't settled yet under T+1. Check whether Alpaca's account payload already nets this out before assuming the full `cash` figure is usable today.
3. **No dividend-calendar check before selling covered calls.** `WheelStrategy.open_covered_call()` has no early-assignment/ex-dividend guard. If the Wheel runs on dividend payers, this is a current, live tactical risk, not a hypothetical — see the investor skill's research on early-assignment mechanics.
4. **Congressional context adapter (phase 1, advisory only) — built 2026-10-03, off by default.** `congress_trades/context.py` plus `PortfolioEngine.congress_advisory()`, called at the top of `run_once()` before the market-clock check. It uses config tickers and local state only, with no broker calls. `alpaca_wheel_strategy.py` intentionally gets nothing. The read-only API field is `GET /api/congress` (`congress_trades/view.py`), shown in the dashboard's Congress panel. Its only write route, `POST /api/congress/refresh` (`congress_trades/refresh.py`), pulls new public filings into `data/` on demand (incremental: conditional index request, new PDFs only, cached parses, cumulative store); nothing in it reaches order code. The engine also writes a heartbeat (`ENGINE_STATUS_FILE`, default `portfolio_engine_status.json`) each cycle via `PortfolioEngine.write_status()`, so the dashboard shows what the running engine is doing rather than what `.env` says; losing that file changes nothing about trading. Committee-overlap alerts fire only once rows carry a `committee_overlap` field, which nothing produces yet.

## Useful project commands

From the repository root, with the virtual environment activated (Python 3.14; the old 3.10 environment is kept as `.venv-py310` for rollback):

```powershell
# Environment: install exactly the pinned, hashed set
pip install --require-hashes -r requirements.lock.txt

# Engines
python -m py_compile portfolio_engine.py alpaca_wheel_strategy.py
python portfolio_engine.py
python alpaca_wheel_strategy.py

# Congressional data: tests, then JSON + dashboard into data\
python -m unittest discover -s congress_trades -t .
python -m congress_trades.build_dashboard <raw.csv> --house --as-of YYYY-MM-DD
```

Dependency rules:

- Add or upgrade a library in `requirements.txt` (engine) or `requirements-api.txt` (API), pinned with `==`.
- Then regenerate the lock: `uv pip compile requirements-api.txt --python-version 3.14 --python-platform x86_64-pc-windows-msvc --generate-hashes -o requirements.lock.txt`.
- Scan the lock for known vulnerabilities (e.g. `pip-audit -r requirements.lock.txt --require-hashes`) before committing.
- A major-version bump of any package, direct or transitive, needs a paper-trading window.
- Never hand-edit the lock file.

Confirm `STATE_FILE` and which runtime you're starting — their state schemas are incompatible even when pointed at the same filename. Use paper trading (`ALPACA_PAPER=true`, `LIVE_TRADING=false`) for all development.

## Completion checklist (developer sign-off)

Before calling an implementation task done, report:

- owning runtime and files changed;
- state transitions added or touched, and restart/reconciliation behavior;
- guardrails preserved or added, and which existing ones the change passes through;
- checks actually run (`py_compile`, targeted tests, paper observation) and their result — not aspirational coverage;
- known limitations and rollback path;
- whether the change is paper-tested, live-ready, or neither — hand this back to the investor skill for sign-off before recommending live enablement.
