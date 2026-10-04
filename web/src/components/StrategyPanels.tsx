import { money, num, pct } from "../format.js";
import type { CongressSymbol, MarketClock, StockStrategy, WheelStrategy } from "../types.js";
import { CongressBadge } from "./CongressPanel.js";

function signalPillClass(signal: string | null): string {
  if (signal === "LONG") return "signal-long";
  if (signal === "SHORT") return "signal-short";
  return "signal-none";
}

/** Shown when the market is closed: quotes and spreads are left over from the last session. */
export function MarketClosedNote({ market }: { market: MarketClock | null }) {
  if (!market || market.is_open) return null;
  const next = market.next_open ? new Date(market.next_open) : null;
  return (
    <div className="market-closed">
      Market closed · prices and spreads are stale until it reopens
      {next && !Number.isNaN(next.getTime())
        ? ` (${next.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short" })})`
        : ""}
    </div>
  );
}

interface StockProps {
  stocks: StockStrategy[];
  market: MarketClock | null;
  congress: Record<string, CongressSymbol>;
}

export function StockPanel({ stocks, market, congress }: StockProps) {
  const closed = market !== null && !market.is_open;
  return (
    <div className="card">
      <div className="card-title">
        <span>Stock strategy</span>
        <span className="muted">EMA/ATR/ADX</span>
      </div>
      <MarketClosedNote market={market} />
      <div className="grid-3">
        {stocks.map((s) => {
          const ind = s.indicators;
          return (
            <div className="card" key={s.symbol} style={{ boxShadow: "none" }}>
              <div className="card-title">
                <span>{s.symbol}</span>
                <span className={`pill ${signalPillClass(ind.signal)}`}>{ind.signal || s.status}</span>
              </div>
              <div className="muted" style={{ marginTop: "6px" }}>
                {s.direction} · {s.status}
              </div>

              {ind.available ? (
                <table style={{ marginTop: "10px" }}>
                  <tbody>
                    <tr><td className="muted">Price</td><td className="mono">{money(ind.price)}{closed && <span className="muted"> (last)</span>}</td></tr>
                    <tr><td className="muted">EMA fast/slow</td><td className="mono">{num(ind.ema_fast)} / {num(ind.ema_slow)}</td></tr>
                    <tr><td className="muted">ATR</td><td className="mono">{num(ind.atr)}</td></tr>
                    <tr><td className="muted">ADX</td><td className="mono">{num(ind.adx, 1)} {ind.trending ? "(trending)" : "(choppy)"}</td></tr>
                    <tr>
                      <td className="muted">Spread</td>
                      <td className={`mono ${closed ? "muted" : ""}`}>
                        {pct(ind.spread_pct, { digits: 3 })}
                        {closed && " (stale)"}
                      </td>
                    </tr>
                  </tbody>
                </table>
              ) : (
                <div className="banner warning" style={{ marginTop: "10px" }}>
                  {ind.error || "Indicators unavailable."}
                </div>
              )}

              {s.status !== "IDLE" && (
                <table style={{ marginTop: "6px" }}>
                  <tbody>
                    <tr><td className="muted">Entry</td><td className="mono">{money(s.entry_price)}</td></tr>
                    <tr><td className="muted">Trailing stop</td><td className="mono">{money(s.trailing_stop_price)}</td></tr>
                  </tbody>
                </table>
              )}
              <CongressBadge s={congress[s.symbol]} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface WheelProps {
  wheels: WheelStrategy[];
  market: MarketClock | null;
  congress: Record<string, CongressSymbol>;
}

export function WheelPanel({ wheels, market, congress }: WheelProps) {
  return (
    <div className="card">
      <div className="card-title">
        <span>Wheel strategy</span>
        <span className="muted">CSP ⇄ covered call</span>
      </div>
      <MarketClosedNote market={market} />
      <div className="grid-3">
        {wheels.map((w) => (
          <div className="card" key={w.symbol} style={{ boxShadow: "none" }}>
            <div className="card-title">
              <span>{w.symbol}</span>
              <span className="pill">{w.phase.replace("_", " ")}</span>
            </div>

            {w.option_symbol ? (
              <table style={{ marginTop: "10px" }}>
                <tbody>
                  <tr><td className="muted">Leg</td><td className="mono">{w.option_symbol}</td></tr>
                  <tr><td className="muted">Entry premium</td><td className="mono">{money(w.entry_premium)}</td></tr>
                  {w.quote && (
                    <tr><td className="muted">Bid / Ask</td><td className="mono">{money(w.quote.bid)} / {money(w.quote.ask)}</td></tr>
                  )}
                  {w.unrealized_gain_pct !== null && (
                    <tr>
                      <td className="muted">Gain vs entry</td>
                      <td className={`mono ${w.unrealized_gain_pct >= 0 ? "pos" : "neg"}`}>{pct(w.unrealized_gain_pct, { signed: true })}</td>
                    </tr>
                  )}
                  {w.assignment_basis !== null && (
                    <tr><td className="muted">Assignment basis</td><td className="mono">{money(w.assignment_basis)}</td></tr>
                  )}
                </tbody>
              </table>
            ) : (
              <div className="muted" style={{ marginTop: "10px" }}>
                No open leg.
              </div>
            )}
            <CongressBadge s={congress[w.symbol]} />
          </div>
        ))}
      </div>
    </div>
  );
}
