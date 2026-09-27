import React from "react";
import { html } from "../html.js";
import { money, pct } from "../format.js";
import { Sparkline } from "./Sparkline.js";

export function Hero({ account, risk, equitySeries }) {
  const equity = account ? account.equity : null;
  const sessionStart = risk ? risk.session_start_equity : null;
  const dollarChange = equity !== null && sessionStart ? equity - sessionStart : null;
  const pctChange = dollarChange !== null && sessionStart ? (dollarChange / sessionStart) * 100 : null;
  const up = dollarChange === null || dollarChange >= 0;

  return html`
    <div class="hero">
      <div class="hero-equity">
        <div class="label">Portfolio equity</div>
        <div class="value">${equity !== null ? money(equity) : "—"}</div>
        <div class="delta ${up ? "up" : "down"}">
          ${dollarChange !== null
            ? `${money(dollarChange, { signed: true })} (${pct(pctChange, { signed: true })}) today`
            : "Waiting for session data…"}
        </div>
        <div class="sparkline">
          <${Sparkline} series=${equitySeries} />
        </div>
      </div>

      <div class="hero-side">
        <div class="stat-card">
          <div class="label">Buying power</div>
          <div class="value">${account ? money(account.buying_power) : "—"}</div>
          <div class="sub">Cash: ${account ? money(account.cash) : "—"}</div>
        </div>
        <div class="stat-card">
          <div class="label">Daily drawdown</div>
          <div class="value">${risk ? pct(risk.daily_drawdown_pct) : "—"}</div>
          <div class="sub">Halt at ${risk ? pct(risk.daily_drawdown_limit_pct) : "—"}</div>
        </div>
      </div>
    </div>
  `;
}
