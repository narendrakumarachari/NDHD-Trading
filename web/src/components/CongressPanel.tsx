// Congressional trade disclosures (GET /api/congress): research for the
// operator plus the engine's advisory check. Read-only - nothing here can
// place, size, block or change an order.

import { apiUrl } from "../api.js";
import { moneyShort, shortDate } from "../format.js";
import type { CongressActivity, CongressSymbol, CongressTrade, CongressView, Settings } from "../types.js";

interface Props {
  view: CongressView | null;
  error: string | null;
  settings: Settings;
}

const range = (t: CongressTrade) =>
  t.amount_high ? `${moneyShort(t.amount_low)}–${moneyShort(t.amount_high)}` : `over ${moneyShort(t.amount_low)}`;

function Direction({ direction }: { direction: string }) {
  if (direction === "Buy") return <span className="pill buy">Buy ↑</span>;
  if (direction === "Sell") return <span className="pill sell">Sell ↓</span>;
  return <span className="pill">{direction}</span>;
}

function Party({ party }: { party: string | null }) {
  return <span className={`pill party-${(party || "u").toLowerCase()}`}>{party || "?"}</span>;
}

function TradeRow({ t, showTicker }: { t: CongressTrade; showTicker?: boolean }) {
  return (
    <tr>
      <td className="mono">{shortDate(t.filing_date)}</td>
      {showTicker && <td><strong>{t.ticker}</strong></td>}
      <td>
        {t.filer} <Party party={t.party} />
      </td>
      <td><Direction direction={t.direction} /></td>
      <td className="mono">{range(t)}</td>
      <td className="mono muted">{shortDate(t.trade_date)} ({t.filing_lag_days}d late)</td>
      <td>
        {t.filing_url ? (
          <a href={t.filing_url} target="_blank" rel="noopener">filing ↗</a>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}

function SymbolCard({ s }: { s: CongressSymbol }) {
  return (
    <div className="card congress-symbol" style={{ boxShadow: "none" }}>
      <div className="card-title">
        <span>{s.symbol}</span>
        <span className={`pill ${s.engine_alert ? "warn" : "signal-none"}`}>{s.engine_alert ? "Engine alert" : "No alert"}</span>
      </div>
      <div className="muted" style={{ marginTop: 4 }}>{s.roles.join(" · ")}</div>
      <p className="congress-why">{s.reason}</p>
      {s.verified_trades.length > 0 && (
        <table>
          <tbody>
            {s.verified_trades.slice(0, 4).map((t, i) => (
              <tr key={i}>
                <td className="mono">{shortDate(t.filing_date)}</td>
                <td>{t.filer}</td>
                <td><Direction direction={t.direction} /></td>
                <td className="mono">{range(t)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {s.unverified_count > 0 && (
        <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
          +{s.unverified_count} not-checked row{s.unverified_count > 1 ? "s" : ""} (ignored by the engine)
        </div>
      )}
    </div>
  );
}

function ActivityRow({ a }: { a: CongressActivity }) {
  const lawmakers = new Set([...a.buyers, ...a.sellers]).size;
  const lean = a.buyers.length && a.sellers.length ? "Mixed" : a.buyers.length ? "Bought" : "Sold";
  return (
    <tr>
      <td><strong>{a.ticker}</strong></td>
      <td className="congress-name">{a.name}</td>
      <td className="mono">{lawmakers}</td>
      <td>
        <span className={`pill ${lean === "Bought" ? "buy" : lean === "Sold" ? "sell" : ""}`}>{lean}</span>
      </td>
      <td className="mono pos">{a.buy_est ? `~${moneyShort(a.buy_est)}` : "—"}</td>
      <td className="mono neg">{a.sell_est ? `~${moneyShort(a.sell_est)}` : "—"}</td>
      <td className="mono">{shortDate(a.latest_filing)}</td>
    </tr>
  );
}

export function CongressPanel({ view, error, settings }: Props) {
  if (!view) {
    return (
      <div className="card">
        <div className="card-title">Congress trades</div>
        <div className={error ? "banner danger" : "muted"}>{error ? `Could not load /api/congress: ${error}` : "Loading…"}</div>
      </div>
    );
  }

  const c = view.counts;
  const ledgerHref =
    apiUrl(settings.apiBase, "/congress/ledger") + (settings.apiKey ? `?api_key=${encodeURIComponent(settings.apiKey)}` : "");
  const statusBanner =
    view.status === "ok" ? null : (
      <div className={`banner ${view.status === "stale" ? "warning" : "danger"}`}>
        <div>
          <strong>
            {view.status === "stale" ? "Data is stale" : view.status === "missing" ? "No congress data yet" : "Data file unreadable"}
          </strong>
          {view.message} Build it with <code>python -m congress_trades.build_dashboard congress_trades\sample\raw_congressflow.csv --house</code>.
        </div>
      </div>
    );

  return (
    <div className="card congress">
      <div className="card-title">
        <span>Congress trades · STOCK Act disclosures</span>
        <span className="muted">
          {view.as_of ? `as of ${shortDate(view.as_of)} · last ${view.window_days} days of filings` : "no data"}
        </span>
      </div>
      <p className="muted congress-intro">
        What US lawmakers (both parties) report buying and selling. Research and advisory only:{" "}
        <strong>this never places, sizes, blocks or changes an order.</strong> Amounts are ranges, not share counts, and
        trades are filed up to 45 days after they happen.
      </p>

      {statusBanner}

      {view.status !== "missing" && view.status !== "unreadable" && (
        <>
          <div className="congress-tiles">
            <div className="stat-card"><div className="label">Official rows</div><div className="value">{c.verified ?? 0}</div><div className="sub">read from House filings</div></div>
            <div className="stat-card"><div className="label">Not checked</div><div className="value">{c.unverified ?? 0}</div><div className="sub">Senate + unmatched; engine ignores</div></div>
            <div className="stat-card"><div className="label">Filings to read by hand</div><div className="value">{view.needs_review.length}</div><div className="sub">scanned paper; never guessed</div></div>
            <div className="stat-card"><div className="label">Engine advisory</div><div className="value">{view.engine_flag_in_env ? "On" : "Off"}</div><div className="sub">CONGRESS_CONTEXT_ENABLED in .env</div></div>
          </div>

          <div className="congress-subtitle">Your symbols: would the engine alert?</div>
          <p className="muted congress-help">
            The engine alerts only when 2+ lawmakers trade a stock it holds or may enter, all the same way, in official
            filings from the last 14 days, with nobody trading the other way.
          </p>
          <div className="grid-3">
            {view.your_symbols.map((s) => <SymbolCard key={s.symbol} s={s} />)}
          </div>

          <div className="grid-2" style={{ marginTop: 18 }}>
            <div>
              <div className="congress-subtitle">Any stock meeting the engine's rule today</div>
              {view.engine_rule_clusters.length === 0 ? (
                <div className="empty-row">None right now. No stock has 2+ lawmakers on one side in the last 14 days of official filings.</div>
              ) : (
                <ul className="congress-clusters">
                  {view.engine_rule_clusters.map((k) => (
                    <li key={k.ticker}>
                      <strong>{k.ticker}</strong> <Direction direction={k.direction} /> {k.filers.join(", ")}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <div className="congress-subtitle">Party split (stock trades, whole window)</div>
              <table>
                <thead><tr><th>Party</th><th>Buys</th><th>Sells</th></tr></thead>
                <tbody>
                  {Object.entries(view.by_party).map(([p, v]) => (
                    <tr key={p}><td><Party party={p} /></td><td className="mono">{v.buys}</td><td className="mono">{v.sells}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="congress-subtitle">Most traded by lawmakers (official filings, {view.window_days} days)</div>
          <table>
            <thead>
              <tr><th>Ticker</th><th>Company</th><th>Lawmakers</th><th>Lean</th><th>Est. bought</th><th>Est. sold</th><th>Latest filing</th></tr>
            </thead>
            <tbody>
              {view.most_active.map((a) => <ActivityRow key={a.ticker} a={a} />)}
            </tbody>
          </table>

          <div className="congress-subtitle">Latest official filings</div>
          <table>
            <thead>
              <tr><th>Filed</th><th>Ticker</th><th>Lawmaker</th><th>Side</th><th>Range</th><th>Traded</th><th></th></tr>
            </thead>
            <tbody>
              {view.recent_verified.map((t, i) => <TradeRow key={i} t={t} showTicker />)}
            </tbody>
          </table>

          {view.needs_review.length > 0 && (
            <details className="congress-review">
              <summary>{view.needs_review.length} filings could not be read by computer: open the originals</summary>
              <ul>
                {view.needs_review.map((r) => (
                  <li key={`${r.filing_id}-${r.record ?? 0}`}>
                    {shortDate(r.filing_date)} · {r.filer} · {r.reason} ·{" "}
                    <a href={r.filing_url} target="_blank" rel="noopener">{r.filing_id} ↗</a>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="congress-footer">
            {view.ledger_available && (
              <a className="btn" href={ledgerHref} target="_blank" rel="noopener">Open the full Congress Trade Ledger ↗</a>
            )}
            <span className="muted">{view.note}</span>
          </div>
        </>
      )}
    </div>
  );
}
