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

There is no test suite or dependency lock file yet — don't claim coverage that doesn't exist. When you add tests, prioritize in this order:

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

1. **PDT protection is a silent no-op (highest priority).** Alpaca removed `daytrade_count`/`pattern_day_trader`/`daytrading_buying_power` from the Account API on 2026-07-06, following FINRA's retirement of the PDT rule. `RiskEngine.allow_stock_entry()`'s PDT block now always evaluates `day_trade_count = 0` and never blocks. Replace it with a check against Alpaca's Intraday Margin Framework (real-time `buying_power` plus Intraday Margin Deficit tracking), or explicitly retire `PDT_PROTECTION` and document why — don't leave a config flag that looks active but does nothing.
2. **T+1 cash-availability gap.** `RiskEngine.allow_wheel_entry()` compares Wheel collateral to `account.get("cash")`, which may include proceeds from a same-day sale that haven't settled yet under T+1. Check whether Alpaca's account payload already nets this out before assuming the full `cash` figure is usable today.
3. **No dividend-calendar check before selling covered calls.** `WheelStrategy.open_covered_call()` has no early-assignment/ex-dividend guard. If the Wheel runs on dividend payers, this is a current, live tactical risk, not a hypothetical — see the investor skill's research on early-assignment mechanics.

## Useful project commands

From the repository root, with the virtual environment activated:

```powershell
python -m py_compile portfolio_engine.py alpaca_wheel_strategy.py
python portfolio_engine.py
python alpaca_wheel_strategy.py
```

Confirm `STATE_FILE` and which runtime you're starting — their state schemas are incompatible even when pointed at the same filename. Use paper trading (`ALPACA_PAPER=true`, `LIVE_TRADING=false`) for all development.

## Completion checklist (developer sign-off)

Before calling an implementation task done, report:

- owning runtime and files changed;
- state transitions added or touched, and restart/reconciliation behavior;
- guardrails preserved or added, and which existing ones the change passes through;
- checks actually run (`py_compile`, targeted tests, paper observation) and their result — not aspirational coverage;
- known limitations and rollback path;
- whether the change is paper-tested, live-ready, or neither — hand this back to the investor skill for sign-off before recommending live enablement.
