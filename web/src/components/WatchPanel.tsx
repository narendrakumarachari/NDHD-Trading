// Watch-only list (GET /api/watch): the stock strategy's own signals for
// WATCH_TICKERS (e.g. Robinhood holdings). The engine never trades these;
// nothing here can place an order.

import { money, pct, shortDate } from "../format.js";
import type { WatchRow, WatchView } from "../types.js";

function price(value: number | null): string {
  if (value === null) return "—";
  return value >= 10 ? money(value) : `$${value.toFixed(2)}`;
}

function Trend({ r }: { r: WatchRow }) {
  const up = r.trend === "up";
  const since = r.bars_since_cross === null ? "" : r.bars_since_cross === 0 ? " · crossed today" : ` · ${r.bars_since_cross} days`;
  return (
    <span title={`EMA 20 ${price(r.ema_fast)} vs EMA 50 ${price(r.ema_slow)}`}>
      <span className={`pill ${up ? "buy" : "sell"}`}>{up ? "uptrend" : "downtrend"}</span>
      <span className="muted small">{since}</span>
    </span>
  );
}

function Signal({ r }: { r: WatchRow }) {
  if (r.signal === "LONG" || r.signal === "SHORT") {
    return <span className="pill warn">{r.signal === "LONG" ? "buy signal today" : "sell signal today"}</span>;
  }
  if (r.crossover_ignored) return <span className="pill lots" title="Crossed, but ADX says no clear trend">crossed · weak trend</span>;
  return <span className="muted">none</span>;
}

function Earnings({ r }: { r: WatchRow }) {
  if (!r.next_earnings) return <span className="muted">—</span>;
  return (
    <span>
      {r.earnings_blackout ? <span className="pill warn">{shortDate(r.next_earnings)}</span> : shortDate(r.next_earnings)}
      {r.days_to_earnings !== null && <span className="muted small"> in {r.days_to_earnings}d</span>}
    </span>
  );
}

interface Props {
  view: WatchView | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
}

export function WatchPanel({ view, error, loading, onRefresh }: Props) {
  const computed = view?.computed_at ? new Date(view.computed_at) : null;
  return (
    <div className="card" id="watch">
      <div className="card-title">
        <span>Watch list · signals only, never traded</span>
        {computed && (
          <span className="pill lots">
            {computed.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
          </span>
        )}
      </div>
      <p className="muted congress-intro">
        The same trend signals the engine trades on (EMA 20/50 crossover, confirmed by ADX ≥ {view?.adx_min_strength ?? 20}),
        for the tickers in <code>WATCH_TICKERS</code>. <strong>The engine never trades these</strong>; this is research only.
      </p>
      <div className="pull-bar">
        <button className="btn" onClick={onRefresh} disabled={loading}>{loading ? "Working…" : "Refresh"}</button>
        <span className="muted small">Uses daily bars, so signals change at most once a day.</span>
      </div>
      {error && <div className="banner danger"><div><strong>Problem</strong>Could not load /api/watch: {error}</div></div>}
      {!view ? (
        !error && <div className="muted">Loading…</div>
      ) : view.rows.length === 0 ? (
        <div className="empty-row">{view.note}</div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th><th>Price</th><th>Trend</th><th>ADX</th><th>Signal today</th>
                <th>1-year range</th><th>Next earnings</th><th>You hold</th>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((r) =>
                r.error ? (
                  <tr key={r.symbol}>
                    <td><strong>{r.symbol}</strong></td>
                    <td colSpan={7} className="muted">No signals: {r.error}</td>
                  </tr>
                ) : (
                  <tr key={r.symbol}>
                    <td>
                      <strong>{r.symbol}</strong>
                      {r.below_min_price && (
                        <span className="pill lots" title={`The engine's minimum price is ${money(view.min_stock_price ?? 10)}`}>under min price</span>
                      )}
                      {r.also_traded && <span className="pill warn">also traded</span>}
                    </td>
                    <td className="mono">
                      {price(r.price)}{" "}
                      <span className={`small ${(r.change_pct ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(r.change_pct, { signed: true, digits: 1 })}</span>
                      <div className="muted small">close {shortDate(r.as_of)}</div>
                    </td>
                    <td><Trend r={r} /></td>
                    <td className="mono">
                      {r.adx ?? "—"} <span className="muted small">{r.trending ? "trending" : "weak"}</span>
                    </td>
                    <td><Signal r={r} /></td>
                    <td className="mono small">
                      {price(r.year_low)} – {price(r.year_high)}
                      <div className="muted">{pct(r.from_low_pct, { signed: true, digits: 0 })} from low</div>
                    </td>
                    <td><Earnings r={r} /></td>
                    <td className="mono">
                      {r.held_qty ? (
                        <>
                          {r.held_qty >= 10 ? Math.round(r.held_qty) : r.held_qty.toFixed(2)} sh
                          {r.vs_cost_pct !== null && (
                            <div className={`small ${r.vs_cost_pct >= 0 ? "pos" : "neg"}`}>
                              {pct(r.vs_cost_pct, { signed: true, digits: 0 })} vs cost {price(r.avg_cost)}
                            </div>
                          )}
                        </>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
      <div className="congress-footer muted"><span>{view?.note}</span></div>
    </div>
  );
}
