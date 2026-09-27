import React from "react";
import { html } from "../html.js";
import { money, pct } from "../format.js";

function Meter({ label, valueLabel, fraction }) {
  const clamped = Math.max(0, Math.min(1, fraction || 0));
  const cls = clamped > 0.95 ? "danger" : clamped > 0.75 ? "warn" : "";
  return html`
    <div class="meter">
      <div class="meter-row"><span>${label}</span><span>${valueLabel}</span></div>
      <div class="meter-track">
        <div class="meter-fill ${cls}" style=${{ width: `${clamped * 100}%` }} />
      </div>
    </div>
  `;
}

export function RiskPanel({ risk }) {
  if (!risk) {
    return html`<div class="card"><div class="card-title">Risk</div><div class="muted">Loading…</div></div>`;
  }

  const exposureFraction = risk.stock_exposure_cap > 0 ? risk.stock_exposure / risk.stock_exposure_cap : 0;
  const drawdownFraction =
    risk.daily_drawdown_limit_pct > 0 ? risk.daily_drawdown_pct / risk.daily_drawdown_limit_pct : 0;
  const positionsFraction =
    risk.stock_position_cap > 0 ? risk.stock_position_count / risk.stock_position_cap : 0;

  const sectorEntries = Object.entries(risk.sector_exposure_pct || {});

  return html`
    <div class="card">
      <div class="card-title">
        <span>Risk & guardrails</span>
        ${risk.kill_switch_active && html`<span class="pill" style=${{ background: "#fff0f5", color: "#d6336c", borderColor: "#ffc9dd" }}>Kill switch active</span>`}
      </div>

      <${Meter}
        label="Daily drawdown"
        valueLabel=${`${pct(risk.daily_drawdown_pct)} / ${pct(risk.daily_drawdown_limit_pct)}`}
        fraction=${drawdownFraction}
      />
      <${Meter}
        label="Stock exposure"
        valueLabel=${`${money(risk.stock_exposure)} / ${money(risk.stock_exposure_cap)}`}
        fraction=${exposureFraction}
      />
      <${Meter}
        label="Stock positions"
        valueLabel=${`${risk.stock_position_count} / ${risk.stock_position_cap}`}
        fraction=${positionsFraction}
      />

      ${sectorEntries.length > 0 &&
      html`<div style=${{ marginTop: "14px" }}>
        <div class="meter-row"><span>Sector exposure (cap ${pct(risk.sector_cap_pct)})</span></div>
        ${sectorEntries.map(
          ([sector, value]) => html`
            <${Meter}
              key=${sector}
              label=${sector}
              valueLabel=${pct(value)}
              fraction=${risk.sector_cap_pct > 0 ? value / risk.sector_cap_pct : 0}
            />
          `
        )}
      </div>`}

      <div
        class="banner ${risk.pdt_protection.effective ? "reconnect" : "danger"}"
        style=${{ marginTop: "16px", marginBottom: 0 }}
      >
        <div>
          <strong>PDT protection: ${risk.pdt_protection.effective ? "active" : "degraded"}</strong>
          ${risk.pdt_protection.note}
        </div>
      </div>
    </div>
  `;
}
