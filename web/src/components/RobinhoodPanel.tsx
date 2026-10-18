// Robinhood snapshot (GET /api/robinhood): the operator's own Robinhood
// accounts, read by Claude Code through Robinhood's read-only MCP tools.
// Human research only - nothing here can place, change or cancel an order,
// and none of it reaches the trading engine.

import { useState } from "react";
import { money, pct, shortDate } from "../format.js";
import type { RobinhoodHolding, RobinhoodRuleCheck, RobinhoodSuggestion, RobinhoodView } from "../types.js";

const CRYPTO_KEY = "ndhd_robinhood_crypto_open_v1";
const TONE_CLASS: Record<RobinhoodSuggestion["tone"], string> = { bad: "critical", warn: "warning", info: "info" };

// Crypto prices can be millionths of a dollar; show enough digits to be useful.
function price(value: number): string {
  if (value >= 1) return money(value);
  if (value >= 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toPrecision(3)}`;
}

function qty(value: number): string {
  if (value >= 1000) return Math.round(value).toLocaleString("en-US");
  return Number.isInteger(value) ? String(value) : value.toFixed(value >= 10 ? 2 : 4);
}

function Pl({ pl, plPct }: { pl: number; plPct: number | null }) {
  const cls = pl >= 0 ? "pos" : "neg";
  return (
    <span className={cls}>
      {money(pl, { signed: true })} <span className="small">({pct(plPct, { signed: true, digits: 1 })})</span>
    </span>
  );
}

function Earnings({ h }: { h: RobinhoodHolding }) {
  if (!h.next_earnings) return <span className="muted">—</span>;
  const soon = h.days_to_earnings !== null && h.days_to_earnings >= 0 && h.days_to_earnings <= 14;
  const when = h.earnings_timing === "am" ? "before open" : h.earnings_timing === "pm" ? "after close" : "";
  return (
    <span title={h.earnings_confirmed ? "Confirmed by the company" : "Tentative date"}>
      {soon ? <span className="pill warn">{shortDate(h.next_earnings)}</span> : shortDate(h.next_earnings)}
      <span className="muted small"> {when}{h.earnings_confirmed ? "" : " · tentative"}</span>
    </span>
  );
}

function RuleMeter({ c }: { c: RobinhoodRuleCheck }) {
  const actual = c.actual ?? 0;
  const fraction = c.limit > 0 ? Math.min(1, actual / c.limit) : 0;
  const fmt = (v: number) => (c.unit === "%" ? `${v}%` : String(v));
  return (
    <div className="meter">
      <div className="meter-row">
        <span>
          {c.rule} <span className="muted small">· {c.detail}</span>
        </span>
        <span className={c.ok ? "" : "neg"}>
          {fmt(actual)} <span className="muted">/ limit {fmt(c.limit)}</span>
        </span>
      </div>
      <div className="meter-track">
        <div className={`meter-fill ${c.ok ? "" : "danger"}`} style={{ width: `${fraction * 100}%` }} />
      </div>
    </div>
  );
}

function HoldingRow({ h, showEarnings }: { h: RobinhoodHolding; showEarnings: boolean }) {
  return (
    <tr>
      <td><strong>{h.symbol}</strong></td>
      <td className="mono">{qty(h.quantity)}</td>
      <td className="mono">{h.avg_cost !== null ? price(h.avg_cost) : "—"}</td>
      <td className="mono">{price(h.price)}</td>
      <td className="mono">{money(h.value)}</td>
      <td className="mono"><Pl pl={h.pl} plPct={h.pl_pct} /></td>
      <td className="mono">{pct(h.weight_pct, { digits: 1 })}</td>
      {showEarnings && <td><Earnings h={h} /></td>}
    </tr>
  );
}

function loadCryptoOpen(): boolean {
  try {
    return localStorage.getItem(CRYPTO_KEY) === "1";
  } catch {
    return false;
  }
}

interface Props {
  view: RobinhoodView | null;
  error: string | null;
  onReload: () => void;
}

export function RobinhoodPanel({ view, error, onReload }: Props) {
  const [cryptoOpen, setCryptoOpen] = useState(loadCryptoOpen);
  const toggleCrypto = () => {
    const next = !cryptoOpen;
    setCryptoOpen(next);
    try {
      localStorage.setItem(CRYPTO_KEY, next ? "1" : "0");
    } catch {
      // storage unavailable: the toggle still works for this visit
    }
  };

  const refreshHint = (
    <span className="muted small">
      To refresh, ask Claude Code: <em>"refresh my Robinhood snapshot"</em>. Then click Reload.
    </span>
  );

  if (!view || view.status !== "ok") {
    return (
      <div className="card" id="robinhood">
        <div className="card-title">Robinhood · read-only snapshot</div>
        <div className={error || view ? "banner warning" : "muted"}>
          <div>{error ? `Could not load /api/robinhood: ${error}` : view ? view.message : "Loading…"}</div>
        </div>
        <div className="pull-bar">
          <button className="btn" onClick={onReload}>Reload</button>
          {refreshHint}
        </div>
      </div>
    );
  }

  const t = view.totals;
  const main = view.main;
  const agent = view.accounts.find((a) => a.agent_can_trade);
  const age = view.age_days ?? 0;
  const ageLabel = age === 0 ? "today" : `${age} day${age === 1 ? "" : "s"} old`;
  const takenAt = view.taken_at ? new Date(view.taken_at) : null;

  return (
    <div className="card robinhood" id="robinhood">
      <div className="card-title">
        <span>Robinhood · read-only snapshot</span>
        <span className={`pill age-${view.stale ? "bad" : age >= 2 ? "warn" : "ok"}`}>
          {takenAt ? `taken ${takenAt.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${ageLabel}` : ageLabel}
          {view.stale ? " · stale" : ""}
        </span>
      </div>
      <p className="muted congress-intro">
        Your Robinhood accounts, read through Robinhood's own connection for AI agents.{" "}
        <strong>Read-only: nothing here can trade, and none of it reaches the trading engine.</strong>{" "}
        {view.prices_note}
      </p>

      <div className="pull-bar">
        <button className="btn" onClick={onReload}>Reload</button>
        {refreshHint}
      </div>
      {error && <div className="banner danger"><div><strong>Problem</strong>{error}</div></div>}
      {view.stale && (
        <div className="banner warning">
          <div><strong>Snapshot is stale</strong>It is {ageLabel}. Prices and holdings may have changed.</div>
        </div>
      )}

      <div className="congress-tiles">
        <div className="stat-card">
          <div className="label">All accounts</div>
          <div className="value">{money(t.all_accounts)}</div>
          <div className="sub">{view.accounts.length} accounts</div>
        </div>
        <div className="stat-card">
          <div className="label">Stocks · {main?.label}</div>
          <div className="value">{money(t.stocks?.value)}</div>
          <div className="sub">{t.stocks && <Pl pl={t.stocks.pl} plPct={t.stocks.pl_pct} />} vs cost</div>
        </div>
        <div className="stat-card">
          <div className="label">Crypto · {main?.label}</div>
          <div className="value">{money(t.crypto?.value)}</div>
          <div className="sub">{t.crypto && <Pl pl={t.crypto.pl} plPct={t.crypto.pl_pct} />} vs cost</div>
        </div>
        <div className="stat-card">
          <div className="label">Agent trade approvals</div>
          {agent ? (
            <>
              <div className={`value ${agent.trade_approvals_required ? "pos" : "neg"}`}>
                {agent.trade_approvals_required ? "On" : "Off"}
              </div>
              <div className="sub">{agent.label} ••{agent.last4} · {money(agent.total_value)}</div>
            </>
          ) : (
            <div className="value muted">No agent account</div>
          )}
        </div>
      </div>

      <div className="grid-2">
        <div>
          <div className="congress-subtitle">Things to review</div>
          {view.suggestions.length === 0 ? (
            <div className="empty-row">Nothing to flag.</div>
          ) : (
            <div className="alerts-feed rh-suggestions">
              {view.suggestions.map((s, i) => (
                <div key={i} className={`alert-item ${TONE_CLASS[s.tone]}`}>
                  <div className={`alert-severity-dot ${TONE_CLASS[s.tone]}`} />
                  <div>
                    <div className="alert-subject">{s.title}</div>
                    <div className="alert-body">{s.detail}</div>
                    <div className="alert-ts">{s.who}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <div className="congress-subtitle">{main?.label} account vs your NDHD engine's limits</div>
          {view.rule_checks.map((c) => <RuleMeter key={c.rule} c={c} />)}
          <p className="muted congress-help">
            The limits come from this project's settings (MAX_SINGLE_POSITION_PCT, MAX_TOTAL_STOCK_EXPOSURE_PCT,
            STOCK_MAX_POSITIONS). They are shown for comparison only and block nothing in Robinhood.
          </p>
          <div className="congress-subtitle">Accounts</div>
          <table>
            <thead><tr><th>Account</th><th>Type</th><th>Value</th><th>Cash</th></tr></thead>
            <tbody>
              {view.accounts.map((a) => (
                <tr key={a.last4}>
                  <td>
                    <strong>{a.label}</strong> <span className="muted">••{a.last4}</span>
                    {a.default && <span className="pill lots">default</span>}
                    {a.managed && <span className="pill lots">managed</span>}
                    {a.agent_can_trade && <span className="pill warn">agent</span>}
                  </td>
                  <td className="muted">{(a.kind || "—").replace("_", " ")}</td>
                  <td className="mono">{money(a.total_value)}</td>
                  <td className="mono">{money(a.cash)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="congress-subtitle">Stocks · {main?.label} ••{main?.last4}</div>
      <table>
        <thead>
          <tr><th>Symbol</th><th>Shares</th><th>Avg cost</th><th>Price</th><th>Value</th><th>Gain / loss</th><th>Of account</th><th>Next earnings</th></tr>
        </thead>
        <tbody>{view.stocks.map((h) => <HoldingRow key={h.symbol} h={h} showEarnings />)}</tbody>
      </table>

      <div className="grid-2">
        <div>
          <div className="congress-subtitle">Covered calls (you sold)</div>
          {view.covered_calls.length === 0 ? (
            <div className="empty-row">None.</div>
          ) : (
            <table>
              <thead><tr><th>Symbol</th><th>Contracts</th><th>Strike</th><th>Expires</th><th>Received</th><th>Checks</th></tr></thead>
              <tbody>
                {view.covered_calls.map((c) => (
                  <tr key={`${c.symbol}-${c.expiration}`}>
                    <td><strong>{c.symbol}</strong></td>
                    <td className="mono">{c.contracts}</td>
                    <td className="mono">{money(c.strike)}</td>
                    <td className="mono">{shortDate(c.expiration)}</td>
                    <td className="mono">{money(c.premium)}</td>
                    <td>
                      <span className={`pill ${c.covered ? "buy" : "sell"}`}>{c.covered ? "covered" : "not covered"}</span>{" "}
                      <span className={`pill ${c.strike_above_cost ? "buy" : "sell"}`} title="Strike above your average cost">
                        {c.strike_above_cost ? "above cost" : "below cost"}
                      </span>
                      <div className="muted small">strike {pct(c.strike_above_price_pct, { signed: true, digits: 0 })} vs price</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted congress-help">Cost to buy them all back today: about {money(Math.abs(t.options_value ?? 0))}.</p>
        </div>
        <div>
          <div className="congress-subtitle">Open orders</div>
          {view.open_orders.length === 0 ? (
            <div className="empty-row">None.</div>
          ) : (
            <table>
              <thead><tr><th>Asset</th><th>Order</th><th>Limit</th><th>Now</th><th>Placed</th></tr></thead>
              <tbody>
                {view.open_orders.map((o) => (
                  <tr key={`${o.asset}-${o.placed}`}>
                    <td><strong>{o.asset}</strong> <span className="muted small">{o.kind}</span></td>
                    <td><span className={`pill ${o.side === "buy" ? "buy" : "sell"}`}>{o.side} {o.type}</span> <span className="mono">{qty(o.quantity)}</span></td>
                    <td className="mono">{price(o.limit_price)} <span className="muted small">({pct(o.limit_vs_price_pct, { signed: true, digits: 0 })})</span></td>
                    <td className="mono">{o.price !== null ? price(o.price) : "—"}</td>
                    <td className="mono">
                      {shortDate(o.placed)}
                      {o.age_days !== null && o.age_days > 14 && <span className="pill warn">{o.age_days}d</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="congress-actions">
        <button className="btn" onClick={toggleCrypto} aria-expanded={cryptoOpen}>
          {cryptoOpen ? `Hide crypto (${view.crypto.length}) ▴` : `Show crypto (${view.crypto.length}) ▾`}
        </button>
      </div>
      {cryptoOpen && (
        <div className="congress-details">
          <table>
            <thead><tr><th>Coin</th><th>Amount</th><th>Avg cost</th><th>Price</th><th>Value</th><th>Gain / loss</th><th>Of account</th></tr></thead>
            <tbody>{view.crypto.map((h) => <HoldingRow key={h.symbol} h={h} showEarnings={false} />)}</tbody>
          </table>
        </div>
      )}

      <div className="congress-footer muted">
        <span>{view.note}</span>
      </div>
    </div>
  );
}
