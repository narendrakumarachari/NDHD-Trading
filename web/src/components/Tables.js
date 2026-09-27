import React from "react";
import { html } from "../html.js";
import { money, pct, num } from "../format.js";

export function PositionsTable({ positions, onClose, closingSymbol }) {
  return html`
    <div class="card">
      <div class="card-title"><span>Positions</span><span class="muted">${positions.length}</span></div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th><th>Side</th><th>Qty</th><th>Avg entry</th><th>Current</th>
            <th>Market value</th><th>Unrealized P&amp;L</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${positions.length === 0 &&
          html`<tr><td colSpan="8"><div class="empty-row">No open positions.</div></td></tr>`}
          ${positions.map((p) => {
            const gain = p.unrealized_pl >= 0;
            return html`
              <tr key=${p.symbol}>
                <td><strong>${p.symbol}</strong>${p.is_option && html` <span class="pill">OPT</span>`}</td>
                <td><span class="pill ${p.side}">${p.side}</span></td>
                <td class="mono">${num(p.qty, 0)}</td>
                <td class="mono">${money(p.avg_entry_price)}</td>
                <td class="mono">${money(p.current_price)}</td>
                <td class="mono">${money(p.market_value)}</td>
                <td class="mono ${gain ? "pos" : "neg"}">
                  ${money(p.unrealized_pl, { signed: true })} (${pct(p.unrealized_plpc, { signed: true })})
                </td>
                <td>
                  <button
                    class="btn danger small"
                    disabled=${closingSymbol === p.symbol}
                    onClick=${() => onClose(p.symbol)}
                  >
                    ${closingSymbol === p.symbol ? "Closing…" : "Close"}
                  </button>
                </td>
              </tr>
            `;
          })}
        </tbody>
      </table>
    </div>
  `;
}

export function OrdersTable({ orders, onCancel, cancellingId }) {
  return html`
    <div class="card">
      <div class="card-title"><span>Open orders</span><span class="muted">${orders.length}</span></div>
      <table>
        <thead>
          <tr><th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Limit</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          ${orders.length === 0 &&
          html`<tr><td colSpan="7"><div class="empty-row">No open orders.</div></td></tr>`}
          ${orders.map(
            (o) => html`
              <tr key=${o.id}>
                <td><strong>${o.symbol}</strong></td>
                <td><span class="pill ${o.side}">${o.side}</span></td>
                <td>${o.type}</td>
                <td class="mono">${o.qty ?? "—"}</td>
                <td class="mono">${o.limit_price ? money(o.limit_price) : "—"}</td>
                <td><span class="pill">${o.status}</span></td>
                <td>
                  <button
                    class="btn small"
                    disabled=${cancellingId === o.id}
                    onClick=${() => onCancel(o.id)}
                  >
                    ${cancellingId === o.id ? "Cancelling…" : "Cancel"}
                  </button>
                </td>
              </tr>
            `
          )}
        </tbody>
      </table>
    </div>
  `;
}
