import React from "react";
import { createRoot } from "react-dom/client";
import { html } from "./html.js";
import { apiFetch, loadSettings, saveSettings, useLiveData } from "./api.js";

import { TopBar } from "./components/TopBar.js";
import { Hero } from "./components/Hero.js";
import { RiskPanel } from "./components/RiskPanel.js";
import { PositionsTable, OrdersTable } from "./components/Tables.js";
import { StockPanel, WheelPanel } from "./components/StrategyPanels.js";
import { AlertsFeed, ToastStack } from "./components/Alerts.js";
import { LiveConsole } from "./components/LiveConsole.js";
import {
  KillSwitchModal,
  ClosePositionModal,
  ManualOrderModal,
  SettingsModal,
} from "./components/Modals.js";

const MAX_EQUITY_POINTS = 180;

function App() {
  const [settings, setSettings] = React.useState(loadSettings());
  const { connected, lastError, data, newAlerts, consumeNewAlert, clearLogs } = useLiveData(settings);

  const [equitySeries, setEquitySeries] = React.useState([]);
  const [toasts, setToasts] = React.useState([]);
  const [modal, setModal] = React.useState(null); // null | "settings" | "kill-switch" | "manual-order" | {type:"close", symbol}

  const [killSwitchBusy, setKillSwitchBusy] = React.useState(false);
  const [closingSymbol, setClosingSymbol] = React.useState(null);
  const [cancellingId, setCancellingId] = React.useState(null);
  const [manualOrderBusy, setManualOrderBusy] = React.useState(false);
  const [manualOrderResult, setManualOrderResult] = React.useState(null);
  const [actionError, setActionError] = React.useState(null);

  // Track equity over time for the hero sparkline.
  React.useEffect(() => {
    if (!data.account) return;
    setEquitySeries((prev) => {
      const next = [...prev, data.account.equity];
      return next.length > MAX_EQUITY_POINTS ? next.slice(next.length - MAX_EQUITY_POINTS) : next;
    });
  }, [data.account && data.account.equity]);

  // Promote newly-seen alerts into the toast stack.
  React.useEffect(() => {
    if (newAlerts.length === 0) return;
    setToasts((prev) => [...prev, ...newAlerts]);
    newAlerts.forEach((_, i) => consumeNewAlert(0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newAlerts.length]);

  function dismissToast(index) {
    setToasts((prev) => prev.filter((_, i) => i !== index));
  }

  async function runAction(fn) {
    setActionError(null);
    try {
      return await fn();
    } catch (exc) {
      setActionError(exc.message || String(exc));
      return null;
    }
  }

  async function handleKillSwitch() {
    setKillSwitchBusy(true);
    await runAction(() => apiFetch(settings, "/api/kill-switch", { method: "POST" }));
    setKillSwitchBusy(false);
    setModal(null);
  }

  async function handleClosePosition(symbol) {
    setClosingSymbol(symbol);
    await runAction(() => apiFetch(settings, `/api/positions/${encodeURIComponent(symbol)}/close`, { method: "POST" }));
    setClosingSymbol(null);
    setModal(null);
  }

  async function handleCancelOrder(orderId) {
    setCancellingId(orderId);
    await runAction(() => apiFetch(settings, `/api/orders/${encodeURIComponent(orderId)}/cancel`, { method: "POST" }));
    setCancellingId(null);
  }

  async function handleManualOrder(form) {
    setManualOrderBusy(true);
    const res = await runAction(() =>
      apiFetch(settings, "/api/orders", { method: "POST", body: JSON.stringify(form) })
    );
    setManualOrderResult(res);
    setManualOrderBusy(false);
  }

  function handleSaveSettings(next) {
    setSettings(next);
    saveSettings(next);
    setModal(null);
  }

  const showReconnectBanner = !connected;
  const showHaltBanner = data.risk && (data.risk.daily_risk_halted || data.risk.kill_switch_active);

  return html`
    <${React.Fragment}>
    <${TopBar}
      account=${data.account}
      connected=${connected}
      onOpenSettings=${() => setModal("settings")}
      onOpenKillSwitch=${() => setModal("kill-switch")}
      killSwitchBusy=${killSwitchBusy}
    />

    <div class="app-shell">
      ${showReconnectBanner &&
      html`<div class="banner reconnect">
        <div><strong>Reconnecting…</strong>Live updates paused. Last data from ${data.lastUpdated ? new Date(data.lastUpdated).toLocaleTimeString() : "never"}.</div>
      </div>`}

      ${showHaltBanner &&
      html`<div class="banner danger">
        <div>
          <strong>Trading halted</strong>
          ${data.risk.daily_risk_halted ? "Daily drawdown limit reached. " : ""}
          ${data.risk.kill_switch_active ? "Kill switch cooldown is active." : ""}
        </div>
      </div>`}

      ${actionError &&
      html`<div class="banner danger">
        <div><strong>Action failed</strong>${actionError}</div>
      </div>`}

      <${Hero} account=${data.account} risk=${data.risk} equitySeries=${equitySeries} />

      <div class="section-title">Live engine console</div>
      <${LiveConsole} lines=${data.logLines} connected=${connected} onClear=${clearLogs} />

      <div class="section-title">Risk &amp; positions</div>
      <div class="grid-2">
        <${RiskPanel} risk=${data.risk} />
        <${PositionsTable} positions=${data.positions} onClose=${(symbol) => setModal({ type: "close", symbol })} closingSymbol=${closingSymbol} />
      </div>

      <div class="section-title">Strategies</div>
      <div class="grid-2">
        <${StockPanel} stocks=${data.stocks} />
        <${WheelPanel} wheels=${data.wheels} />
      </div>

      <div class="section-title">Orders &amp; activity</div>
      <div class="grid-2">
        <${OrdersTable} orders=${data.orders} onCancel=${handleCancelOrder} cancellingId=${cancellingId} />
        <${AlertsFeed} alerts=${data.alerts} />
      </div>

      <div class="section-title">Manual control</div>
      <div class="card">
        <div class="card-title"><span>Submit a manual order</span></div>
        <p class="muted" style=${{ margin: "6px 0 12px" }}>
          Opens the manual order form. Orders that open/increase exposure
          still pass the account's configured risk limits.
        </p>
        <button class="btn primary" onClick=${() => { setManualOrderResult(null); setModal("manual-order"); }}>
          New manual order
        </button>
      </div>

      <footer class="hint">
        NDHD Trading Dashboard${lastError ? ` · Last error: ${lastError}` : ""}
      </footer>
    </div>

    <${ToastStack} toasts=${toasts} onDismiss=${dismissToast} />

    ${modal === "settings" && html`<${SettingsModal} settings=${settings} onSave=${handleSaveSettings} onClose=${() => setModal(null)} />`}
    ${modal === "kill-switch" && html`<${KillSwitchModal} busy=${killSwitchBusy} onConfirm=${handleKillSwitch} onClose=${() => setModal(null)} />`}
    ${modal && modal.type === "close" && html`<${ClosePositionModal} symbol=${modal.symbol} busy=${closingSymbol === modal.symbol} onConfirm=${() => handleClosePosition(modal.symbol)} onClose=${() => setModal(null)} />`}
    ${modal === "manual-order" && html`<${ManualOrderModal} busy=${manualOrderBusy} result=${manualOrderResult} onSubmit=${handleManualOrder} onClose=${() => setModal(null)} />`}
    <//>
  `;
}

const root = createRoot(document.getElementById("root"));
root.render(html`<${App} />`);
