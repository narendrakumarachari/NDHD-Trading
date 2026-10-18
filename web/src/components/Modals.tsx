import { useState, type ChangeEvent, type ReactNode } from "react";
import type { ManualOrderRequest, ManualOrderResult, Settings } from "../types.js";

function ModalShell({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">{children}</div>
    </div>
  );
}

interface KillSwitchProps {
  onConfirm: () => void;
  onClose: () => void;
  busy: boolean;
}

export function KillSwitchModal({ onConfirm, onClose, busy }: KillSwitchProps) {
  const [text, setText] = useState("");
  const ready = text.trim().toUpperCase() === "FLATTEN";

  return (
    <ModalShell onClose={onClose}>
      <h3>Trigger kill switch</h3>
      <p className="desc">
        This immediately market-closes every open stock position and buys back short option legs where the debit isn't
        unreasonable, then blocks new entries until the cooldown configured by KILL_SWITCH_COOLDOWN_DAYS expires. This
        cannot be undone from here.
      </p>
      <div className="field">
        <label>Type FLATTEN to confirm</label>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="FLATTEN" />
      </div>
      <div className="actions">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger" disabled={!ready || busy} onClick={onConfirm}>
          {busy ? "Flattening…" : "Flatten everything"}
        </button>
      </div>
    </ModalShell>
  );
}

interface ClosePositionProps {
  symbol: string;
  onConfirm: () => void;
  onClose: () => void;
  busy: boolean;
}

export function ClosePositionModal({ symbol, onConfirm, onClose, busy }: ClosePositionProps) {
  return (
    <ModalShell onClose={onClose}>
      <h3>Close {symbol}</h3>
      <p className="desc">Submits a market order to close this entire position at the current price.</p>
      <div className="actions">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger" disabled={busy} onClick={onConfirm}>
          {busy ? "Submitting…" : `Close ${symbol}`}
        </button>
      </div>
    </ModalShell>
  );
}

// Text inputs hold strings while editing; they become numbers only on submit.
interface OrderForm {
  symbol: string;
  asset_class: ManualOrderRequest["asset_class"];
  side: ManualOrderRequest["side"];
  qty: string;
  order_type: ManualOrderRequest["order_type"];
  limit_price: string;
  position_intent: string;
  confirm: boolean;
}

interface ManualOrderProps {
  onSubmit: (order: ManualOrderRequest) => void;
  onClose: () => void;
  busy: boolean;
  result: ManualOrderResult | null;
}

export function ManualOrderModal({ onSubmit, onClose, busy, result }: ManualOrderProps) {
  const [form, setForm] = useState<OrderForm>({
    symbol: "",
    asset_class: "equity",
    side: "buy",
    qty: "",
    order_type: "market",
    limit_price: "",
    position_intent: "buy_to_open",
    confirm: false,
  });

  const set =
    <K extends keyof OrderForm>(key: K) =>
    (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const target = e.target;
      const value = target instanceof HTMLInputElement && target.type === "checkbox" ? target.checked : target.value;
      setForm((f) => ({ ...f, [key]: value as OrderForm[K] }));
    };

  const canSubmit = Boolean(form.symbol && form.qty && form.confirm && !busy);
  const isOption = form.asset_class === "option";

  return (
    <ModalShell onClose={onClose}>
      <h3>Manual order</h3>
      <p className="desc">
        Bypasses the automated strategies but still runs through the same RiskEngine checks (position size,
        total/sector exposure, drawdown halt) for anything that opens or increases exposure. Closing/reducing orders are
        never blocked.
      </p>

      <div className="field">
        <label>Symbol</label>
        <input value={form.symbol} onChange={set("symbol")} placeholder="AAPL or full OCC option symbol" />
      </div>

      <div className="row field">
        <div>
          <label>Asset class</label>
          <select value={form.asset_class} onChange={set("asset_class")}>
            <option value="equity">Equity</option>
            <option value="option">Option</option>
          </select>
        </div>
        <div>
          <label>Side</label>
          <select value={form.side} onChange={set("side")}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </div>
      </div>

      {isOption && (
        <div className="field">
          <label>Position intent</label>
          <select value={form.position_intent} onChange={set("position_intent")}>
            <option value="buy_to_open">buy_to_open</option>
            <option value="sell_to_open">sell_to_open</option>
            <option value="buy_to_close">buy_to_close</option>
            <option value="sell_to_close">sell_to_close</option>
          </select>
        </div>
      )}

      <div className="row field">
        <div>
          <label>Qty</label>
          <input type="number" value={form.qty} onChange={set("qty")} />
        </div>
        <div>
          <label>Order type</label>
          <select value={form.order_type} onChange={set("order_type")} disabled={isOption}>
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </div>
      </div>

      {(form.order_type === "limit" || isOption) && (
        <div className="field">
          <label>Limit price {isOption && "(required for options)"}</label>
          <input type="number" step="0.01" value={form.limit_price} onChange={set("limit_price")} />
        </div>
      )}

      <div className="field">
        <label style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <input type="checkbox" checked={form.confirm} onChange={set("confirm")} style={{ width: "auto" }} />
          I understand this submits a real order (paper or live, per current config).
        </label>
      </div>

      {result && (
        <div className={`banner ${result.accepted ? "reconnect" : "danger"}`}>
          <div>
            <strong>{result.accepted ? "Order accepted" : "Rejected"}</strong>
            {result.accepted
              ? `${result.dry_run ? "DRY RUN — " : ""}order ${result.order_id} (${result.status})`
              : result.reason}
          </div>
        </div>
      )}

      <div className="actions">
        <button className="btn" onClick={onClose}>Close</button>
        <button
          className="btn primary"
          disabled={!canSubmit}
          onClick={() =>
            onSubmit({
              symbol: form.symbol,
              asset_class: form.asset_class,
              side: form.side,
              qty: Number(form.qty),
              order_type: form.order_type,
              limit_price: form.limit_price ? Number(form.limit_price) : null,
              position_intent: isOption ? form.position_intent : null,
              confirm: form.confirm,
            })
          }
        >
          {busy ? "Submitting…" : "Submit order"}
        </button>
      </div>
    </ModalShell>
  );
}

interface SettingsProps {
  settings: Settings;
  onSave: (next: Settings) => void;
  onClose: () => void;
}

export function SettingsModal({ settings, onSave, onClose }: SettingsProps) {
  const [apiBase, setApiBase] = useState(settings.apiBase);
  const [apiKey, setApiKey] = useState(settings.apiKey);

  return (
    <ModalShell onClose={onClose}>
      <h3>Settings</h3>
      <p className="desc">
        Leave API base empty to use this same origin (recommended - the dashboard is normally served by the same FastAPI
        process it talks to). Set an API key only if the server has DASHBOARD_API_KEY set.
      </p>
      <div className="field">
        <label>API base URL (optional)</label>
        <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://127.0.0.1:8000" />
      </div>
      <div className="field">
        <label>API key (X-API-Key)</label>
        <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="only if DASHBOARD_API_KEY is set" />
      </div>
      <div className="actions">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => onSave({ apiBase, apiKey })}>Save</button>
      </div>
    </ModalShell>
  );
}
