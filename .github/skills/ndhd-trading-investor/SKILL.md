---
name: ndhd-trading-investor
description: 'Develop and review features for the NDHD Alpaca trading project with an investor and production-risk mindset. Use for portfolio_engine.py, alpaca_wheel_strategy.py, stock signals, Wheel options, risk controls, order execution, persistence, reconciliation, paper trading, live-trading readiness, backtesting, or any feature that can change capital exposure.'
argument-hint: 'Describe the trading or portfolio feature, risk question, or operational change to implement.'
user-invocable: true
disable-model-invocation: false
---

# NDHD Trading Investor

## Purpose

Use this skill to add, review, or debug features in this repository as both a software engineer and a capital allocator. Optimize for durable risk-adjusted outcomes and operational survivability, not for feature count, theoretical win rate, or optimistic backtests.

The repository currently has two separate runtimes:

- `portfolio_engine.py` is the primary integrated engine. It owns stock signals, multi-symbol Wheel logic, portfolio risk controls, reconciliation, persistence, alerts, and the polling loop.
- `alpaca_wheel_strategy.py` is a standalone, single-ticker Wheel engine. It has its own configuration, API client, state schema, and lifecycle implementation.

Do not silently implement a feature in both runtimes. Choose the owner first and document whether the other runtime is intentionally unchanged, migrated, or deprecated.

## Investor Decision Standard

Before coding, state the feature thesis in concrete terms:

- What portfolio problem does this solve?
- Which risk, return, liquidity, tax, or operational assumption makes it worthwhile?
- What is the worst plausible loss, including gaps, slippage, assignment, early exercise, partial fills, stale data, and API failure?
- How much capital and buying power can it consume at once and across correlated positions?
- What evidence would disprove the thesis or make the feature not worth maintaining?

Treat expected return as secondary to loss containment, liquidity, drawdown behavior, and recoverability. This project is an execution framework, not investment advice or a profitability guarantee.

## Procedure

### 1. Establish the change boundary

1. Read the relevant implementation, nearby call sites, README configuration, and persisted state shape before editing.
2. Identify whether the behavior is an entry decision, position management, liquidation, reconciliation, persistence, data dependency, alert, or runtime orchestration concern.
3. Confirm the owning runtime and the controlling class/function. Prefer the smallest existing abstraction that already owns the behavior.
4. Check for duplicate behavior in the other runtime and record compatibility implications.
5. Do not treat `.env`, API credentials, lock files, or live account state as source material to copy into code or documentation.

### 2. Model money and failure paths

Write down the invariant before implementation. At minimum cover:

- maximum notional, collateral, leverage, and buying-power use;
- single-position, total-stock, sector/correlation, and strategy-level concentration;
- stop distance, gap risk, option assignment risk, and worst-case exit liquidity;
- fees, spread, slippage, price rounding, and partial fills;
- behavior when quotes, Greeks, earnings data, clock data, or account data are missing;
- behavior after rejection, timeout, rate limit, process crash, restart, or duplicate execution;
- behavior in dry-run, paper, and explicitly enabled live modes.

New risk-taking entries should fail closed when required information is unavailable. Managing or reducing an existing position is a separate policy and must remain available when safe to do so.

### 3. Make state transitions explicit

For any stateful feature, define:

- valid states and allowed transitions;
- the broker facts that establish each transition;
- startup and restart recovery;
- partial-fill, canceled, expired, rejected, assigned, called-away, and manually changed-position behavior;
- how unknown or externally created positions are quarantined rather than accidentally adopted;
- schema versioning, migration, corruption handling, and backup expectations.

Persist intent and durable identifiers, not just the hoped-for final state. Existing JSON persistence uses atomic temporary-file replacement, but there is no general schema migration or durable order/fill ledger; a new feature must not make those gaps worse.

### 4. Treat orders as events

Before submitting an order:

1. Validate account status, market state, data freshness, exposure, liquidity, and strategy ownership.
2. Generate a deterministic idempotency key derived from the strategy, symbol, action, position/state transition, and logical attempt. Do not use a timestamp alone.
3. Persist the pending order intent before submission when the operation can create or increase risk.
4. Submit with explicit side, position intent, quantity, order type, limit/stop constraints, and time-in-force.
5. Reconcile the broker order and fills. Never equate submission with a fill.
6. Persist the observed result and only then advance the strategy state.

For dry runs, report what would happen without mutating state as if a real fill occurred. Avoid simulated loops that repeatedly create entries or exits on every poll.

### 5. Preserve trading guardrails

Any feature that can create exposure must pass the relevant existing controls in `portfolio_engine.py`, including drawdown halts, kill switch and cooldown, position and total exposure, sector concentration, PDT protection, price and spread checks, earnings blackout, option DTE/delta/open-interest checks, collateral limits, and paper/live gating.

For stock features, preserve the distinction between long and short positions and ensure trailing stops only tighten. For Wheel features, preserve the CSP-to-assignment-to-covered-call lifecycle, contract sizing, basis protection, option liquidity checks, assignment and early-exercise uncertainty, and the possibility that shares or options changed outside the process.

Do not rely on symbol length to identify asset class when broker metadata is available. Do not use module-global configuration where injected configuration is required for testing or multiple environments.

### 6. Validate like an investor and an operator

Add focused automated tests for the changed behavior before broadening the scope. Prefer pure tests for calculations and transitions, then mocked API tests for order/reconciliation behavior. At minimum consider:

- normal path and boundary values;
- monotonic stops and sizing limits;
- spread widening, stale or missing quotes, missing Greeks, and unavailable earnings data;
- drawdown, sector, collateral, and buying-power limits;
- partial fills and all terminal order statuses;
- assignment, expiration, call-away, manual positions, and unknown positions;
- crash between intent persistence and broker submission, and restart recovery;
- malformed or old state files and migration behavior;
- dry-run behavior without false fills or false cooldowns;
- API errors, rate limits, rejected orders, and cancellation failure.

Run the narrowest relevant test or syntax check first, then the full available validation. The repository currently has no test suite or dependency lock file, so do not claim test coverage that does not exist. If a backtest is added, document data source, survivorship/look-ahead controls, commissions, spread/slippage assumptions, missing-data policy, and out-of-sample results.

### 7. Require paper-trading evidence before live enablement

A feature is not live-ready because it passes unit tests. Before recommending `LIVE_TRADING=true`:

- verify `ALPACA_PAPER=true` and credentials are paper credentials;
- observe order intent, fills, reconciliation, state recovery, alerts, and kill-switch behavior in paper trading;
- confirm limits under normal and stressed market conditions;
- document rollback and manual intervention steps;
- state residual risks and the smallest capital allocation appropriate for staged deployment.

Never print, commit, or request real credentials. Keep live trading disabled unless the operator explicitly enables it after reviewing the evidence.

## Completion Checklist

Before closing a feature task, report:

- owning runtime and files changed;
- investor thesis, capital at risk, key assumptions, and disconfirming evidence;
- state transitions and restart/reconciliation behavior;
- controls preserved or added;
- tests/checks run and their result;
- known limitations, operational alerts, and rollback path;
- whether the feature is paper-tested, live-ready, or neither.

## Useful Project Commands

From the repository root, with the virtual environment activated:

```powershell
python -m py_compile portfolio_engine.py alpaca_wheel_strategy.py
python portfolio_engine.py
python alpaca_wheel_strategy.py
```

The two scripts use incompatible state models even though they may point at the same filename. Confirm `STATE_FILE` and the intended runtime before starting either process. Keep `.env` out of source control and use paper trading for development.