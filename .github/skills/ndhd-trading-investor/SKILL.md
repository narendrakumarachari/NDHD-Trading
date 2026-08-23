---
name: ndhd-trading-investor
description: 'Think and decide like a risk-aware capital allocator before changing trading behavior in this repository. Use for evaluating stock/Wheel signals, position sizing, risk limits, and regulatory or market-structure assumptions (PDT, wash sale, settlement, assignment) that affect portfolio_engine.py or alpaca_wheel_strategy.py. Pairs with the ndhd-trading-developer skill, which implements what this skill approves.'
argument-hint: 'Describe the trading idea, risk question, or existing control you want evaluated from an investor/risk-manager perspective.'
user-invocable: true
disable-model-invocation: false
---

# NDHD Trading Investor

## Purpose

Evaluate every trading-behavior change in this repository the way a risk-aware capital allocator would: state the thesis, find the worst case, and decide whether a careful portfolio manager would actually take it. This skill governs the *decision* — whether a signal, limit, or exception belongs in the strategy at all, optimized for durable risk-adjusted survival, not win rate or a curve-fit backtest. For *how* to build an approved decision safely in the code, hand off to the ndhd-trading-developer skill.

The repository runs two independent books. Treat them separately — a decision made for one is not automatically valid for the other:

- **Stock strategy**: EMA(20/50) trend-following with an ADX regime filter and an ATR trailing stop, in `portfolio_engine.py`.
- **Wheel strategy**: cash-secured puts rolling into covered calls, implemented twice — multi-symbol and integrated in `portfolio_engine.py`, and standalone/single-ticker with its own config and state schema in `alpaca_wheel_strategy.py`.

This is an execution framework, not investment advice and not a profitability guarantee.

## Investor Decision Standard

Before endorsing or requesting a change, state the thesis in concrete terms:

- What portfolio problem does this solve, and for which of the two strategies?
- Which risk, return, liquidity, tax, or operational assumption makes it worth the added complexity?
- What is the worst plausible loss — gaps, slippage, assignment, early exercise, partial fills, stale data, or a regulatory/broker-API change out from under the code?
- How much capital and buying power can it consume at once, and across correlated positions?
- What evidence would disprove the thesis, or make it not worth maintaining?

Loss containment, liquidity, and recoverability outrank expected return. A strategy that backtests well but carries an unbounded or hard-to-estimate tail is not investable as-is.

## Verify current standards before trusting them

Treat every hard-coded regulatory, tax, or broker assumption in this codebase as a claim with a date attached, not a fact. Rules that were stable for two decades can change inside a single year — the PDT finding below is exactly that happening in this repository right now. Before relying on a threshold, field name, or rule mechanic:

1. Check it against a current primary source (FINRA, SEC, IRS, or the broker's own API docs/changelog) — not memory, and not this file.
2. If it conflicts with what the code assumes, treat the code's control as *silently degraded*, not as still working. A guard that reads a field the broker stopped returning does not fail closed — it fails open, silently, which is worse than having no guard at all because it looks like protection.
3. Re-verify the facts below periodically. They were checked on 2026-08-23; do not assume they still hold indefinitely.

### Live finding in this repo (verified 2026-08-23)

FINRA's Pattern Day Trader rule — the $25,000-equity / 4-day-trades-in-5-days restriction — was retired effective **June 4, 2026**, replaced by a dynamic, real-time "Intraday Margin Framework." Alpaca implemented this and removed `daytrade_count`, `pattern_day_trader`, `daytrading_buying_power`, `bod_dtbp`, and `last_daytrade_count` from its Account API on **2026-07-06**.

`RiskEngine.allow_stock_entry()` in `portfolio_engine.py` still gates entries on:

```python
if self.config.pdt_protection and equity < self.config.pdt_equity_threshold:
    day_trade_count = as_int(account.get("daytrade_count"))
    if day_trade_count >= self.config.pdt_max_day_trades:
```

Since Alpaca no longer returns `daytrade_count`, `account.get(...)` returns `None`, `as_int(None)` defaults to `0`, and `0 >= pdt_max_day_trades` is always `False`. **`PDT_PROTECTION` has been a silent no-op since 2026-07-06** — no exception, no log warning, no alert. It simply stopped blocking anything. This is the sharpest available illustration of why "the code has a guard" and "the guard still works" are different claims, and it is a concrete, ready-to-implement item for the developer skill: migrate to Alpaca's Intraday Margin Framework (real-time, equity-adjusted `buying_power` and Intraday Margin Deficit tracking) rather than a day-trade counter that no longer exists.

### Other standards currently in force (verified 2026-08-23)

- **Settlement is T+1** (since 2024-05-28): a stock sale's cash isn't available for a new entry until the next business day settles. `allow_wheel_entry()` compares Wheel collateral against `account.get("cash")`, which can overstate same-day availability right after a sale.
- **Wash sale (IRC §1091)** still uses a 30-day window (before and after) and explicitly treats options as potentially "substantially identical" to the underlying stock — with no bright-line IRS definition even now. The repo's `WashSaleTracker` is correctly informational-only, not blocking, because there's no safe way to automate that judgment call. Its silence is a flag for the operator's CPA, not a compliance guarantee.
- **Market-wide circuit breakers** remain 7% / 13% / 20% S&P 500 decline thresholds (Level 1/2/3), recalculated daily off the prior close. Relevant to the README's open "circuit-breaker handling" item.
- **Covered-call early-assignment risk is a live, current tactical risk**, not a historical footnote: a deep-ITM call is likely to be exercised early just before ex-dividend when the dividend exceeds remaining extrinsic value. `WheelStrategy.open_covered_call()` selects by delta/DTE/spread only and has no dividend-calendar check — flag this as a thesis gap on any dividend-paying Wheel ticker.
- **Wheel parameters (30–45 DTE, ~0.30 delta, 50% profit-target buyback) remain the standard range** used by current options-income research and practitioners. The repo's defaults are not stale; there's no standards-driven reason to change this axis.
- **Reg SHO's locate requirement** falls on Alpaca as broker for any `STOCK_DIRECTION=short`/`both` order, not on this code directly — but it means shortability isn't guaranteed for every ticker. Confirm the account can actually borrow a symbol before assuming a short signal is executable.

Sources checked: [FINRA Regulatory Notice 26-10](https://www.finra.org/rules-guidance/notices/26-10), [Alpaca: FINRA Retires the PDT Rule](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/), [Alpaca changelog: PDT/DTBP fields deprecated](https://docs.alpaca.markets/us/changelog/2026-06-03-pdt-651df23), [Alpaca: The Intraday Margin Rule](https://docs.alpaca.markets/us/docs/the-intraday-margin-rule), [White & Case: T+1 Settlement](https://www.whitecase.com/insight-alert/t1-settlement-cycle-take-effect-may-28-2024), [Fidelity: Wash-Sale Rules](https://www.fidelity.com/learning-center/personal-finance/wash-sales-rules-tax), [Investor.gov: Circuit Breakers](https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers), [Fidelity: Dividends and Options Assignment Risk](https://www.fidelity.com/learning-center/investment-products/options/dividends-options-assignment-risk).

## Model money and failure paths

For any change, write the invariant down before implementation:

- maximum notional, collateral, leverage, and buying-power use;
- single-position, total-stock, sector/correlation, and strategy-level concentration;
- stop distance, gap risk, option assignment/early-exercise risk, worst-case exit liquidity;
- fees, spread, slippage, price rounding, partial fills;
- behavior when quotes, Greeks, earnings data, clock data, or account data are missing — including a broker silently dropping a field the code depends on (see the PDT finding above);
- behavior after rejection, timeout, rate limit, process crash, restart, or duplicate execution;
- behavior in dry-run, paper, and explicitly enabled live modes.

New risk-taking entries should fail closed when required information is unavailable or a depended-on field disappears. Managing or reducing an existing position is a separate policy and should stay available even during a failure.

## Guardrails that must stay intact

Any change that can create exposure must still pass: daily drawdown halt plus kill switch/cooldown, single-position and total-stock exposure caps, sector concentration cap, stock-position count cap, PDT-equivalent protection (currently broken — see above), price/spread liquidity checks, earnings blackout, option DTE/delta/open-interest/spread checks, Wheel collateral cap, and paper/live gating. For stock, trailing stops must only tighten. For the Wheel, the CSP → assignment → covered-call → call-away lifecycle and the "covered-call strike must clear assignment basis" rule must hold.

## Paper-to-live readiness bar

A feature is not live-ready because it passes unit tests or looks right analytically. Before endorsing `LIVE_TRADING=true` for anything:

- confirm `ALPACA_PAPER=true` and paper credentials were used for the observation period;
- observe order intent, fills, reconciliation, state recovery, alerts, and kill-switch behavior in paper trading, including at least one forced failure path (rejected order, missing data, restart mid-cycle);
- confirm limits hold under a stressed/fast-moving market, not just a quiet one;
- require a stated rollback and manual-intervention plan;
- size the first live allocation to what you can afford to be wrong about, not to what the backtest suggests.

Never request or accept real credentials in this conversation. Keep live trading disabled unless the operator explicitly enables it after reviewing paper evidence.

## Completion checklist (investor sign-off)

Before treating a change as investor-approved, confirm:

- the thesis, capital at risk, and disconfirming evidence are stated;
- worst-case loss and the specific guardrails it passes through are named;
- any regulatory/broker assumption touched was checked against a current source, not assumed;
- known limitations and residual risk are stated in plain terms, including anything discovered to be silently degraded;
- whether the change is paper-tested, live-ready, or neither.
