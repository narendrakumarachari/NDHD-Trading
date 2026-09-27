import React from "react";
import { html } from "../html.js";

function ModalShell({ onClose, children }) {
  return html`
    <div class="modal-backdrop" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="modal">${children}</div>
    </div>
  `;
}

export function KillSwitchModal({ onConfirm, onClose, busy }) {
  const [text, setText] = React.useState("");
  const ready = text.trim().toUpperCase() === "FLATTEN";

  return html`
    <${ModalShell} onClose=${onClose}>
      <h3>Trigger kill switch</h3>
      <p class="desc">
        This immediately market-closes every open stock position and buys
        back short option legs where the debit isn't unreasonable, then
        blocks new entries until the cooldown configured by
        KILL_SWITCH_COOLDOWN_DAYS expires. This cannot be undone from here.
      </p>
      <div class="field">
        <label>Type FLATTEN to confirm</label>
        <input value=${text} onInput=${(e) => setText(e.target.value)} placeholder="FLATTEN" />
      </div>
      <div class="actions">
        <button class="btn" onClick=${onClose}>Cancel</button>
        <button class="btn danger" disabled=${!ready || busy} onClick=${onConfirm}>
          ${busy ? "Flattening…" : "Flatten everything"}
        </button>
      </div>
    <//>
  `;
}

export function ClosePositionModal({ symbol, onConfirm, onClose, busy }) {
  return html`
    <${ModalShell} onClose=${onClose}>
      <h3>Close ${symbol}</h3>
      <p class="desc">Submits a market order to close this entire position at the current price.</p>
      <div class="actions">
        <button class="btn" onClick=${onClose}>Cancel</button>
        <button class="btn danger" disabled=${busy} onClick=${onConfirm}>
          ${busy ? "Submitting…" : `Close ${symbol}`}
        </button>
      </div>
    <//>
  `;
}

export function ManualOrderModal({ onSubmit, onClose, busy, result }) {
  const [form, setForm] = React.useState({
    symbol: "",
    asset_class: "equity",
    side: "buy",
    qty: "",
    order_type: "market",
    limit_price: "",
    position_intent: "buy_to_open",
    confirm: false,
  });

  const set = (key) => (e) =>
    setForm((f) => ({ ...f, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  const canSubmit = form.symbol && form.qty && form.confirm && !busy;

  return html`
    <${ModalShell} onClose=${onClose}>
      <h3>Manual order</h3>
      <p class="desc">
        Bypasses the automated strategies but still runs through the same
        RiskEngine checks (position size, total/sector exposure, drawdown
        halt) for anything that opens or increases exposure. Closing/reducing
        orders are never blocked.
      </p>

      <div class="field">
        <label>Symbol</label>
        <input value=${form.symbol} onInput=${set("symbol")} placeholder="AAPL or full OCC option symbol" />
      </div>

      <div class="row field">
        <div>
          <label>Asset class</label>
          <select value=${form.asset_class} onInput=${set("asset_class")}>
            <option value="equity">Equity</option>
            <option value="option">Option</option>
          </select>
        </div>
        <div>
          <label>Side</label>
          <select value=${form.side} onInput=${set("side")}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </div>
      </div>

      ${form.asset_class === "option" &&
      html`<div class="field">
        <label>Position intent</label>
        <select value=${form.position_intent} onInput=${set("position_intent")}>
          <option value="buy_to_open">buy_to_open</option>
          <option value="sell_to_open">sell_to_open</option>
          <option value="buy_to_close">buy_to_close</option>
          <option value="sell_to_close">sell_to_close</option>
        </select>
      </div>`}

      <div class="row field">
        <div>
          <label>Qty</label>
          <input type="number" value=${form.qty} onInput=${set("qty")} />
        </div>
        <div>
          <label>Order type</label>
          <select value=${form.order_type} onInput=${set("order_type")} disabled=${form.asset_class === "option"}>
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </div>
      </div>

      ${(form.order_type === "limit" || form.asset_class === "option") &&
      html`<div class="field">
        <label>Limit price ${form.asset_class === "option" && "(required for options)"}</label>
        <input type="number" step="0.01" value=${form.limit_price} onInput=${set("limit_price")} />
      </div>`}

      <div class="field">
        <label style=${{ display: "flex", alignItems: "center", gap: "8px" }}>
          <input type="checkbox" checked=${form.confirm} onInput=${set("confirm")} style=${{ width: "auto" }} />
          I understand this submits a real order (paper or live, per current config).
        </label>
      </div>

      ${result &&
      html`<div class="banner ${result.accepted ? "reconnect" : "danger"}">
        <div>
          <strong>${result.accepted ? "Order accepted" : "Rejected"}</strong>
          ${result.accepted
            ? `${result.dry_run ? "DRY RUN — " : ""}order ${result.order_id} (${result.status})`
            : result.reason}
        </div>
      </div>`}

      <div class="actions">
        <button class="btn" onClick=${onClose}>Close</button>
        <button
          class="btn primary"
          disabled=${!canSubmit}
          onClick=${() =>
            onSubmit({
              ...form,
              qty: Number(form.qty),
              limit_price: form.limit_price ? Number(form.limit_price) : null,
            })}
        >
          ${busy ? "Submitting…" : "Submit order"}
        </button>
      </div>
    <//>
  `;
}

export function SettingsModal({ settings, onSave, onClose }) {
  const [apiBase, setApiBase] = React.useState(settings.apiBase);
  const [apiKey, setApiKey] = React.useState(settings.apiKey);

  return html`
    <${ModalShell} onClose=${onClose}>
      <h3>Settings</h3>
      <p class="desc">
        Leave API base empty to use this same origin (recommended - the
        dashboard is normally served by the same FastAPI process it talks
        to). Set an API key only if the server has DASHBOARD_API_KEY set.
      </p>
      <div class="field">
        <label>API base URL (optional)</label>
        <input value=${apiBase} onInput=${(e) => setApiBase(e.target.value)} placeholder="http://127.0.0.1:8000" />
      </div>
      <div class="field">
        <label>API key (X-API-Key)</label>
        <input value=${apiKey} onInput=${(e) => setApiKey(e.target.value)} placeholder="only if DASHBOARD_API_KEY is set" />
      </div>
      <div class="actions">
        <button class="btn" onClick=${onClose}>Cancel</button>
        <button class="btn primary" onClick=${() => onSave({ apiBase, apiKey })}>Save</button>
      </div>
    <//>
  `;
}
