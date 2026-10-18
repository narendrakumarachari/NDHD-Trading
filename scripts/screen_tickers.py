"""Screen candidate tickers for the PAPER engine. Read-only.

Two gates, reported separately so a human makes the final choice:

1. Company quality (public fundamentals via yfinance): market cap, profit
   margin, free cash flow, trading volume. Objective thresholds, not a view
   on future returns.
2. Engine rules (the engine's own code and the current .env, via Alpaca's
   read-only data API): minimum price, enough daily bars for the EMA/ATR/ADX
   indicators and, for the Wheel, a put that WheelStrategy.select_option()
   would actually pick (delta, DTE, bid, spread, open interest) whose
   collateral fits WHEEL_MAX_COLLATERAL_PCT of the paper account's equity.

The stock spread check needs a live quote, so it only runs while the
market is open. Nothing here places an order or writes engine state. This
is a tool for configuring a paper-trading simulator, not investment advice.

Usage:
    python scripts/screen_tickers.py [TICKER ...]   (default: built-in list)
Writes data/ticker_screen.json (git-ignored) and prints a table.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import portfolio_engine as pe  # noqa: E402

logging.getLogger("portfolio-engine").setLevel(logging.ERROR)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Large, heavily traded US companies across sectors: a starting list to
# screen, not a recommendation of any of them.
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "ORCL", "CSCO", "AMD",
    "QCOM", "TXN", "INTC", "ADBE", "CRM", "IBM",
    "JPM", "BAC", "WFC", "GS", "V", "MA", "SCHW",
    "JNJ", "PFE", "MRK", "ABBV", "UNH", "LLY", "BMY",
    "KO", "PEP", "PG", "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS", "TGT",
    "XOM", "CVX", "CAT", "GE", "RTX", "UPS", "T", "VZ",
]

MIN_MARKET_CAP = 50e9
MIN_AVG_VOLUME = 2_000_000


def quality(symbol: str) -> dict:
    import yfinance as yf

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as exc:
        return {"ok": False, "why": f"no fundamentals ({str(exc)[:60]})"}
    q = {
        "name": info.get("shortName"),
        "sector": info.get("sector"),
        "market_cap_b": round((info.get("marketCap") or 0) / 1e9, 1),
        "profit_margin": info.get("profitMargins"),
        "free_cash_flow_b": round((info.get("freeCashflow") or 0) / 1e9, 1),
        "avg_volume_m": round((info.get("averageVolume") or 0) / 1e6, 1),
        "dividend_yield": info.get("dividendYield"),
    }
    why = []
    notes = []
    if (info.get("marketCap") or 0) < MIN_MARKET_CAP:
        why.append("market cap < $50B")
    if (info.get("profitMargins") or 0) <= 0:
        why.append("not profitable")
    fcf = info.get("freeCashflow")
    if info.get("sector") == "Financial Services":
        # Free cash flow isn't a meaningful measure for banks and brokers
        # (deposits and loans run through operating cash flow); judge them
        # on return on equity instead.
        if (info.get("returnOnEquity") or 0) <= 0.08:
            why.append("bank ROE <= 8%")
    elif fcf is None:
        notes.append("free cash flow unknown")
    elif fcf <= 0:
        why.append("free cash flow <= 0")
    if (info.get("averageVolume") or 0) < MIN_AVG_VOLUME:
        why.append("avg volume < 2M")
    q["ok"] = not why
    q["why"] = "; ".join(why + notes)
    return q


def main(symbols: list[str]) -> None:
    cfg = pe.CONFIG
    api = pe.AlpacaClient(cfg)
    store = pe.StateStore(str(Path(tempfile.mkdtemp()) / "screen.json"))
    state = pe.PortfolioState()
    risk = pe.RiskEngine(cfg, state, store)
    earn = pe.EarningsFilter(True, cfg.earnings_blackout_days, config=cfg)
    stock = pe.StockStrategy(cfg, api, state, store, risk, earn)
    wheel = pe.WheelStrategy(cfg, api, state, store, risk, earn, alerter=pe.ALERTER)

    account = api.get_account()
    equity = pe.as_float(account.get("equity"))
    max_collateral = equity * cfg.wheel_max_collateral_pct / 100.0
    market_open = bool(api.get_clock().get("is_open"))

    rows = []
    for sym in symbols:
        row = {"symbol": sym, **quality(sym)}
        try:
            close, atr_v, _slow, signal = stock.indicators(sym)
            d = stock.last_diagnostics
            row.update(price=round(close, 2), atr_pct=round(100 * atr_v / close, 2),
                       adx=None if d["adx"] is None else round(d["adx"], 1),
                       trend="up" if d["ema_fast"] > d["ema_slow"] else "down", signal_today=signal)
            row["stock_rules_ok"] = close >= cfg.min_stock_price
            if market_open:
                _, spread = stock.market_price(sym)
                row["stock_spread_pct"] = round(spread, 3)
                row["stock_rules_ok"] &= spread <= cfg.max_stock_spread_pct
        except Exception as exc:
            row.update(stock_rules_ok=False, stock_why=str(exc)[:80])

        try:
            put = wheel.select_option(sym, "put", float(row["price"]))
            collateral = put.strike * cfg.wheel_contract_size
            row.update(put=put.symbol, put_strike=put.strike, put_dte=put.dte,
                       put_delta=round(put.delta, 3), put_bid=put.bid, put_spread_pct=round(put.spread_pct, 1),
                       put_oi=put.open_interest, collateral=round(collateral),
                       premium_yield_pct=round(100 * put.midpoint / put.strike, 2))
            row["wheel_rules_ok"] = collateral <= max_collateral
            if not row["wheel_rules_ok"]:
                row["wheel_why"] = f"collateral ${collateral:,.0f} > ${max_collateral:,.0f}"
        except Exception as exc:
            row.update(wheel_rules_ok=False, wheel_why=str(exc)[:80])

        try:
            cal = __import__("yfinance").Ticker(sym).calendar
            nxt = pe.next_earnings_date(cal, date.today())
            row["next_earnings"] = nxt.isoformat() if nxt else None
            exdiv = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
            row["ex_dividend"] = exdiv.isoformat() if hasattr(exdiv, "isoformat") else None
        except Exception:
            row["next_earnings"] = None
        rows.append(row)
        print(f"  screened {sym}", file=sys.stderr)

    out = ROOT / "data" / "ticker_screen.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"as_of": date.today().isoformat(), "equity": equity,
                               "market_open": market_open, "rows": rows}, indent=2, default=str))

    def fmt(v, spec=""):
        return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else format(v, spec)

    print(f"\nPaper equity ${equity:,.0f}; one put may use ${max_collateral:,.0f}. Market open: {market_open}.")
    print(f"{'sym':6} {'quality':8} {'stock':6} {'wheel':6} {'price':>8} {'trend':5} {'adx':>5} "
          f"{'put strike':>10} {'yield%':>6} {'OI':>6} {'earnings':10} sector / why")
    for r in sorted(rows, key=lambda r: (not r.get("ok"), not r.get("wheel_rules_ok"), r["symbol"])):
        why = r.get("why") or r.get("wheel_why") or ""
        print(f"{r['symbol']:6} {('yes' if r.get('ok') else 'no'):8} {('yes' if r.get('stock_rules_ok') else 'no'):6} "
              f"{('yes' if r.get('wheel_rules_ok') else 'no'):6} {fmt(r.get('price'), '8.2f')} {r.get('trend', '-'):5} "
              f"{fmt(r.get('adx'), '5.1f')} {fmt(r.get('put_strike'), '10.1f')} {fmt(r.get('premium_yield_pct'), '6.2f')} "
              f"{fmt(r.get('put_oi'), '6d')} {r.get('next_earnings') or '-':10} {r.get('sector') or ''} {('/ ' + why) if why else ''}")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main([s.upper() for s in sys.argv[1:]] or DEFAULT_UNIVERSE)
