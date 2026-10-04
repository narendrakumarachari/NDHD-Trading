// Congressional trade disclosures (GET /api/congress): research for the
// operator plus the engine's advisory check. Read-only - nothing here can
// place, size, block or change an order.

import { useState } from "react";
import { apiUrl } from "../api.js";
import { moneyShort, shortDate } from "../format.js";
import type { CongressActivity, CongressPull, CongressSymbol, CongressTrade, CongressView, Settings } from "../types.js";

const DETAILS_KEY = "ndhd_congress_details_open_v1";

const range = (t: CongressTrade) =>
  t.amount_high ? `${moneyShort(t.amount_low)}–${moneyShort(t.amount_high)}` : `over ${moneyShort(t.amount_low)}`;

const OWNER_LABEL: Record<string, string> = { self: "self", spouse: "spouse", joint: "joint", "dependent child": "child" };

/** "today" / "1 business day old" / "missing" - shared with the top bar. */
export function congressAge(view: CongressView | null): { label: string; tone: "ok" | "warn" | "bad" } {
  if (!view) return { label: "loading…", tone: "ok" };
  if (view.status === "missing") return { label: "no data", tone: "bad" };
  if (view.status === "unreadable") return { label: "unreadable", tone: "bad" };
  const age = view.business_days_old ?? 0;
  const label = age === 0 ? "today" : `${age} business day${age === 1 ? "" : "s"} old`;
  return { label: view.stale ? `${label} · stale` : label, tone: view.stale ? "bad" : age >= 2 ? "warn" : "ok" };
}

/** One line for a strategy card: how many lawmakers traded it, and whether the engine would alert. */
export function CongressBadge({ s }: { s: CongressSymbol | undefined }) {
  if (!s) return null;
  const any = s.window_buyers + s.window_sellers > 0;
  return (
    <a className="congress-badge" href="#congress" title={s.reason}>
      <span className="label">Congress</span>
      {any ? (
        <>
          <span className="pos">↑{s.window_buyers}</span> <span className="neg">↓{s.window_sellers}</span>
          <span className="muted"> lawmakers</span>
        </>
      ) : (
        <span className="muted">no official trades</span>
      )}
      {s.engine_alert && <span className="pill warn">alert</span>}
    </a>
  );
}

function Direction({ direction }: { direction: string }) {
  if (direction === "Buy") return <span className="pill buy">Buy ↑</span>;
  if (direction === "Sell") return <span className="pill sell">Sell ↓</span>;
  return <span className="pill">{direction}</span>;
}

function Party({ party }: { party: string | null }) {
  return <span className={`pill party-${(party || "u").toLowerCase()}`}>{party || "?"}</span>;
}

function Lots({ t }: { t: CongressTrade }) {
  return (
    <>
      {t.owner && t.owner !== "self" && <span className="muted"> · {OWNER_LABEL[t.owner] ?? t.owner}</span>}
      {t.lots > 1 && (
        <span className="pill lots" title={`${t.lots} separate lots with the same details, filed separately`}>
          ×{t.lots}
        </span>
      )}
    </>
  );
}

function TradeRow({ t }: { t: CongressTrade }) {
  return (
    <tr>
      <td className="mono">{shortDate(t.filing_date)}</td>
      <td><strong>{t.ticker}</strong></td>
      <td>
        {t.filer} <Party party={t.party} />
        <Lots t={t} />
      </td>
      <td><Direction direction={t.direction} /></td>
      <td className="mono">{range(t)}</td>
      <td className="mono muted">{shortDate(t.trade_date)} ({t.filing_lag_days}d late)</td>
      <td>{t.filing_url ? <a href={t.filing_url} target="_blank" rel="noopener">filing ↗</a> : "—"}</td>
    </tr>
  );
}

function LeanBar({ a }: { a: CongressActivity }) {
  const total = a.buy_est + a.sell_est || 1;
  const buyPct = Math.round((a.buy_est / total) * 100);
  return (
    <div className="lean" title={`Estimated ~${moneyShort(a.buy_est)} bought vs ~${moneyShort(a.sell_est)} sold (range midpoints)`}>
      <div className="lean-track">
        <div className="lean-buy" style={{ width: `${buyPct}%` }} />
        <div className="lean-sell" style={{ width: `${100 - buyPct}%` }} />
      </div>
      <div className="lean-labels">
        <span className="pos">~{moneyShort(a.buy_est)}</span>
        <span className="neg">~{moneyShort(a.sell_est)}</span>
      </div>
    </div>
  );
}

function ActivityRow({ a }: { a: CongressActivity }) {
  return (
    <tr>
      <td><strong>{a.ticker}</strong></td>
      <td className="congress-name">{a.name}</td>
      <td className="mono">
        <span className="pos">↑{a.buyers.length}</span> / <span className="neg">↓{a.sellers.length}</span>
      </td>
      <td style={{ minWidth: 180 }}><LeanBar a={a} /></td>
      <td className="mono">{shortDate(a.latest_filing)}</td>
    </tr>
  );
}

function EngineTile({ view }: { view: CongressView }) {
  const e = view.engine;
  if (e.running) {
    return (
      <div className="stat-card">
        <div className="label">Engine advisory</div>
        <div className="value">{e.congress_context_enabled ? "On" : "Off"}</div>
        <div className="sub">running engine · seen {e.seen_at ? new Date(e.seen_at).toLocaleTimeString() : "—"}</div>
      </div>
    );
  }
  return (
    <div className="stat-card">
      <div className="label">Engine advisory</div>
      <div className="value muted">Not running</div>
      <div className="sub">.env would start it {view.engine_flag_in_env ? "On" : "Off"}</div>
    </div>
  );
}

/** The on-demand pull: a button, what the local store holds, and live progress while a pull runs. */
function PullBar({ view, pullStatus, onPull }: { view: CongressView | null; pullStatus: CongressPull | null; onPull: () => void }) {
  const running = pullStatus?.state === "running";
  const store = view?.store;
  const lastPull = store?.last_pull_at ? new Date(store.last_pull_at) : null;
  return (
    <div className="pull-bar">
      <button className="btn primary" onClick={onPull} disabled={running}>
        {running ? "Pulling…" : "Pull latest filings"}
      </button>
      <div className="pull-info">
        {running ? (
          <span>{pullStatus?.message}</span>
        ) : pullStatus?.state === "error" ? (
          <span className="neg">{pullStatus.message}</span>
        ) : pullStatus?.state === "done" ? (
          <span>✓ {pullStatus.message}</span>
        ) : store ? (
          <span>
            Last pulled {lastPull ? lastPull.toLocaleString() : "—"} ({store.last_pull_new_filings} new). Only new
            filings are downloaded; nothing pulls automatically.
          </span>
        ) : view?.as_of ? (
          <span>Data from {shortDate(view.as_of)}. A pull downloads only filings we don't have yet.</span>
        ) : (
          <span>Nothing pulled yet. The first pull downloads the last 60 days of House filings.</span>
        )}
        {store && (
          <span className="muted small">
            Stored: {store.filings_total} filings ({shortDate(store.first_filing_date)} – {shortDate(store.last_filing_date)}),
            kept across pulls.
          </span>
        )}
      </div>
    </div>
  );
}

function loadDetailsOpen(): boolean {
  try {
    return localStorage.getItem(DETAILS_KEY) === "1";
  } catch {
    return false;
  }
}

interface Props {
  view: CongressView | null;
  error: string | null;
  settings: Settings;
  pullStatus: CongressPull | null;
  onPull: () => void;
}

export function CongressPanel({ view, error, settings, pullStatus, onPull }: Props) {
  const [open, setOpen] = useState(loadDetailsOpen);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(DETAILS_KEY, next ? "1" : "0");
    } catch {
      // storage unavailable: the toggle still works for this visit
    }
  };

  if (!view) {
    return (
      <div className="card" id="congress">
        <div className="card-title">Congress trades</div>
        <div className={error ? "banner danger" : "muted"}>{error ? `Could not load /api/congress: ${error}` : "Loading…"}</div>
        <PullBar view={null} pullStatus={pullStatus} onPull={onPull} />
      </div>
    );
  }

  const c = view.counts;
  const age = congressAge(view);
  const withKey = (path: string) =>
    apiUrl(settings.apiBase, path) + (settings.apiKey ? `?api_key=${encodeURIComponent(settings.apiKey)}` : "");
  const usable = view.status === "ok" || view.status === "stale";
  const yourTrades = view.your_symbols.flatMap((s) => s.verified_trades);

  return (
    <div className="card congress" id="congress">
      <div className="card-title">
        <span>Congress trades · STOCK Act disclosures</span>
        <span className={`pill age-${age.tone}`}>
          {view.as_of ? `data from ${shortDate(view.as_of)} · ${age.label}` : age.label}
        </span>
      </div>
      <p className="muted congress-intro">
        What US lawmakers (both parties) report buying and selling, from official House filings.{" "}
        <strong>Research and advisory only: it never places, sizes, blocks or changes an order.</strong>
      </p>

      <PullBar view={view} pullStatus={pullStatus} onPull={onPull} />
      {error && <div className="banner danger"><div><strong>Problem</strong>{error}</div></div>}

      {!usable || view.stale ? (
        <div className={`banner ${view.stale ? "warning" : "danger"}`}>
          <div>
            <strong>{view.stale ? "Data is stale" : view.status === "missing" ? "No congress data yet" : "Data file unreadable"}</strong>
            {view.message} Click "Pull latest filings" above to update it.
          </div>
        </div>
      ) : null}

      {usable && (
        <>
          <div className="congress-tiles">
            <div className="stat-card"><div className="label">Official rows</div><div className="value">{c.verified ?? 0}</div><div className="sub">read from House filings</div></div>
            <div className="stat-card"><div className="label">Not checked</div><div className="value">{c.unverified ?? 0}</div><div className="sub">engine ignores these</div></div>
            <div className="stat-card"><div className="label">Read by hand</div><div className="value">{view.needs_review.length}</div><div className="sub">scanned filings; never guessed</div></div>
            <EngineTile view={view} />
          </div>

          <div className="congress-subtitle">Would the engine alert on your symbols?</div>
          <table className="congress-symbols">
            <tbody>
              {view.your_symbols.map((s) => (
                <tr key={s.symbol}>
                  <td><strong>{s.symbol}</strong><div className="muted small">{s.roles.join(" · ")}</div></td>
                  <td>
                    <span className={`pill ${s.engine_alert ? "warn" : "signal-none"}`}>{s.engine_alert ? "Alert" : "No alert"}</span>
                  </td>
                  <td>{s.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted congress-help">
            Rule: 2+ lawmakers trade the stock the same way in official filings from the last 14 days, and nobody the
            other way. {view.engine_rule_clusters.length === 0
              ? "No stock meets it today."
              : `Stocks meeting it today: ${view.engine_rule_clusters.map((k) => `${k.ticker} (${k.direction})`).join(", ")}.`}
          </p>

          <div className="congress-actions">
            <button className="btn" onClick={toggle} aria-expanded={open}>
              {open ? "Hide research details ▴" : "Show research details ▾"}
            </button>
            {view.ledger_available && (
              <a className="btn" href={withKey("/congress/ledger")} target="_blank" rel="noopener">Full ledger ↗</a>
            )}
            <a className="btn" href={withKey("/congress/how-it-works")} target="_blank" rel="noopener">How it works (picture) ↗</a>
          </div>

          {open && (
            <div className="congress-details">
              <div className="grid-2">
                <div>
                  <div className="congress-subtitle">Recent official trades in your symbols</div>
                  {yourTrades.length === 0 ? (
                    <div className="empty-row">None in the window.</div>
                  ) : (
                    <table><tbody>{yourTrades.slice(0, 10).map((t, i) => <TradeRow key={i} t={t} />)}</tbody></table>
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
                <thead><tr><th>Ticker</th><th>Company</th><th>Lawmakers ↑/↓</th><th>Bought vs sold (estimate)</th><th>Latest filing</th></tr></thead>
                <tbody>{view.most_active.map((a) => <ActivityRow key={a.ticker} a={a} />)}</tbody>
              </table>

              <div className="congress-subtitle">Latest official filings</div>
              <table>
                <thead><tr><th>Filed</th><th>Ticker</th><th>Lawmaker</th><th>Side</th><th>Range</th><th>Traded</th><th></th></tr></thead>
                <tbody>{view.recent_verified.map((t, i) => <TradeRow key={i} t={t} />)}</tbody>
              </table>

              {view.needs_review.length > 0 && (
                <>
                  <div className="congress-subtitle">Filings a person needs to read</div>
                  <ul className="congress-review">
                    {view.needs_review.map((r) => (
                      <li key={`${r.filing_id}-${r.record ?? 0}`}>
                        {shortDate(r.filing_date)} · {r.filer} · {r.reason} ·{" "}
                        <a href={r.filing_url} target="_blank" rel="noopener">{r.filing_id} ↗</a>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <p className="muted congress-help">{view.note}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
