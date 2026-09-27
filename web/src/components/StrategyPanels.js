import React from "react";
import { html } from "../html.js";
import { money, num, pct } from "../format.js";

function signalPillClass(signal) {
  if (signal === "LONG") return "signal-long";
  if (signal === "SHORT") return "signal-short";
  return "signal-none";
}

export function StockPanel({ stocks }) {
  return html`
    <div class="card">
      <div class="card-title"><span>Stock strategy</span><span class="muted">EMA/ATR/ADX</span></div>
      <div class="grid-3">
        ${stocks.map((s) => {
          const ind = s.indicators || {};
          return html`
            <div class="card" key=${s.symbol} style=${{ boxShadow: "none" }}>
              <div class="card-title">
                <span>${s.symbol}</span>
                <span class="pill ${signalPillClass(ind.signal)}">${ind.signal || s.status}</span>
              </div>
              <div class="muted" style=${{ marginTop: "6px" }}>${s.direction} · ${s.status}</div>

              ${ind.available
                ? html`
                    <table style=${{ marginTop: "10px" }}>
                      <tbody>
                        <tr><td class="muted">Price</td><td class="mono">${money(ind.price)}</td></tr>
                        <tr><td class="muted">EMA fast/slow</td><td class="mono">${num(ind.ema_fast)} / ${num(ind.ema_slow)}</td></tr>
                        <tr><td class="muted">ATR</td><td class="mono">${num(ind.atr)}</td></tr>
                        <tr><td class="muted">ADX</td><td class="mono">${num(ind.adx, 1)} ${ind.trending ? "(trending)" : "(choppy)"}</td></tr>
                        <tr><td class="muted">Spread</td><td class="mono">${pct(ind.spread_pct, { digits: 3 })}</td></tr>
                      </tbody>
                    </table>
                  `
                : html`<div class="banner warning" style=${{ marginTop: "10px" }}>${ind.error || "Indicators unavailable."}</div>`}

              ${s.status !== "IDLE" &&
              html`<table style=${{ marginTop: "6px" }}>
                <tbody>
                  <tr><td class="muted">Entry</td><td class="mono">${money(s.entry_price)}</td></tr>
                  <tr><td class="muted">Trailing stop</td><td class="mono">${money(s.trailing_stop_price)}</td></tr>
                </tbody>
              </table>`}
            </div>
          `;
        })}
      </div>
    </div>
  `;
}

export function WheelPanel({ wheels }) {
  return html`
    <div class="card">
      <div class="card-title"><span>Wheel strategy</span><span class="muted">CSP ⇄ covered call</span></div>
      <div class="grid-3">
        ${wheels.map(
          (w) => html`
            <div class="card" key=${w.symbol} style=${{ boxShadow: "none" }}>
              <div class="card-title">
                <span>${w.symbol}</span>
                <span class="pill">${w.phase.replace("_", " ")}</span>
              </div>

              ${w.option_symbol
                ? html`
                    <table style=${{ marginTop: "10px" }}>
                      <tbody>
                        <tr><td class="muted">Leg</td><td class="mono">${w.option_symbol}</td></tr>
                        <tr><td class="muted">Entry premium</td><td class="mono">${money(w.entry_premium)}</td></tr>
                        ${w.quote &&
                        html`<tr><td class="muted">Bid / Ask</td><td class="mono">${money(w.quote.bid)} / ${money(w.quote.ask)}</td></tr>`}
                        ${w.unrealized_gain_pct !== null &&
                        html`<tr>
                          <td class="muted">Gain vs entry</td>
                          <td class="mono ${w.unrealized_gain_pct >= 0 ? "pos" : "neg"}">${pct(w.unrealized_gain_pct, { signed: true })}</td>
                        </tr>`}
                        ${w.assignment_basis &&
                        html`<tr><td class="muted">Assignment basis</td><td class="mono">${money(w.assignment_basis)}</td></tr>`}
                      </tbody>
                    </table>
                  `
                : html`<div class="muted" style=${{ marginTop: "10px" }}>No open leg.</div>`}
            </div>
          `
        )}
      </div>
    </div>
  `;
}
