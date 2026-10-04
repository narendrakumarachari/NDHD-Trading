import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { apiFetch, loadSettings, saveSettings, useLiveData, usePolled } from "./api.js";
import type { Alert, CongressView, ManualOrderRequest, ManualOrderResult, Settings } from "./types.js";

import { AlertsFeed, ToastStack } from "./components/Alerts.js";
import { CongressPanel } from "./components/CongressPanel.js";
import { Hero } from "./components/Hero.js";
import { LiveConsole } from "./components/LiveConsole.js";
import { ClosePositionModal, KillSwitchModal, ManualOrderModal, SettingsModal } from "./components/Modals.js";
import { RiskPanel } from "./components/RiskPanel.js";
import { StockPanel, WheelPanel } from "./components/StrategyPanels.js";
import { OrdersTable, PositionsTable } from "./components/Tables.js";
import { TopBar } from "./components/TopBar.js";

const MAX_EQUITY_POINTS = 180;
const CONGRESS_REFRESH_MS = 5 * 60 * 1000; // filings change daily; no need to push them over the socket

type Modal = null | "settings" | "kill-switch" | "manual-order" | { type: "close"; symbol: string };

function App() {
  const [settings, setSettings] = useState<Settings>(loadSettings());
  const { connected, lastError, data, newAlerts, consumeNewAlert, clearLogs } = useLiveData(settings);
  const congress = usePolled<CongressView>(settings, "/api/congress", CONGRESS_REFRESH_MS);

  const [equitySeries, setEquitySeries] = useState<number[]>([]);
  const [toasts, setToasts] = useState<Alert[]>([]);
  const [modal, setModal] = useState<Modal>(null);

  const [killSwitchBusy, setKillSwitchBusy] = useState(false);
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [manualOrderBusy, setManualOrderBusy] = useState(false);
  const [manualOrderResult, setManualOrderResult] = useState<ManualOrderResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Track equity over time for the hero sparkline.
  const equity = data.account?.equity;
  useEffect(() => {
    if (equity === undefined) return;
    setEquitySeries((prev) => [...prev, equity].slice(-MAX_EQUITY_POINTS));
  }, [equity]);

  // Promote newly-seen alerts into the toast stack.
  useEffect(() => {
    if (newAlerts.length === 0) return;
    setToasts((prev) => [...prev, ...newAlerts]);
    newAlerts.forEach(() => consumeNewAlert(0));
  }, [newAlerts.length]);

  function dismissToast(index: number) {
    setToasts((prev) => prev.filter((_, i) => i !== index));
  }

  async function runAction<T>(fn: () => Promise<T>): Promise<T | null> {
    setActionError(null);
    try {
      return await fn();
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : String(exc));
      return null;
    }
  }

  async function handleKillSwitch() {
    setKillSwitchBusy(true);
    await runAction(() => apiFetch(settings, "/api/kill-switch", { method: "POST" }));
    setKillSwitchBusy(false);
    setModal(null);
  }

  async function handleClosePosition(symbol: string) {
    setClosingSymbol(symbol);
    await runAction(() => apiFetch(settings, `/api/positions/${encodeURIComponent(symbol)}/close`, { method: "POST" }));
    setClosingSymbol(null);
    setModal(null);
  }

  async function handleCancelOrder(orderId: string) {
    setCancellingId(orderId);
    await runAction(() => apiFetch(settings, `/api/orders/${encodeURIComponent(orderId)}/cancel`, { method: "POST" }));
    setCancellingId(null);
  }

  async function handleManualOrder(order: ManualOrderRequest) {
    setManualOrderBusy(true);
    const res = await runAction(() =>
      apiFetch<ManualOrderResult>(settings, "/api/orders", { method: "POST", body: JSON.stringify(order) }),
    );
    setManualOrderResult(res);
    setManualOrderBusy(false);
  }

  function handleSaveSettings(next: Settings) {
    setSettings(next);
    saveSettings(next);
    setModal(null);
  }

  const risk = data.risk;
  const closeModal = () => setModal(null);

  return (
    <>
      <TopBar
        account={data.account}
        connected={connected}
        onOpenSettings={() => setModal("settings")}
        onOpenKillSwitch={() => setModal("kill-switch")}
        killSwitchBusy={killSwitchBusy}
      />

      <div className="app-shell">
        {!connected && (
          <div className="banner reconnect">
            <div>
              <strong>Reconnecting…</strong>Live updates paused. Last data from{" "}
              {data.lastUpdated ? new Date(data.lastUpdated).toLocaleTimeString() : "never"}.
            </div>
          </div>
        )}

        {risk && (risk.daily_risk_halted || risk.kill_switch_active) && (
          <div className="banner danger">
            <div>
              <strong>Trading halted</strong>
              {risk.daily_risk_halted ? "Daily drawdown limit reached. " : ""}
              {risk.kill_switch_active ? "Kill switch cooldown is active." : ""}
            </div>
          </div>
        )}

        {actionError && (
          <div className="banner danger">
            <div>
              <strong>Action failed</strong>
              {actionError}
            </div>
          </div>
        )}

        <Hero account={data.account} risk={risk} equitySeries={equitySeries} />

        <div className="section-title">Live engine console</div>
        <LiveConsole lines={data.logLines} connected={connected} onClear={clearLogs} />

        <div className="section-title">Risk & positions</div>
        <div className="grid-2">
          <RiskPanel risk={risk} />
          <PositionsTable
            positions={data.positions}
            onClose={(symbol) => setModal({ type: "close", symbol })}
            closingSymbol={closingSymbol}
          />
        </div>

        <div className="section-title">Strategies</div>
        <div className="grid-2">
          <StockPanel stocks={data.stocks} />
          <WheelPanel wheels={data.wheels} />
        </div>

        <div className="section-title">Congress trades (research · advisory only)</div>
        <CongressPanel view={congress.value} error={congress.error} settings={settings} />

        <div className="section-title">Orders & activity</div>
        <div className="grid-2">
          <OrdersTable orders={data.orders} onCancel={handleCancelOrder} cancellingId={cancellingId} />
          <AlertsFeed alerts={data.alerts} />
        </div>

        <div className="section-title">Manual control</div>
        <div className="card">
          <div className="card-title">
            <span>Submit a manual order</span>
          </div>
          <p className="muted" style={{ margin: "6px 0 12px" }}>
            Opens the manual order form. Orders that open/increase exposure still pass the account's configured risk
            limits.
          </p>
          <button
            className="btn primary"
            onClick={() => {
              setManualOrderResult(null);
              setModal("manual-order");
            }}
          >
            New manual order
          </button>
        </div>

        <footer className="hint">NDHD Trading Dashboard{lastError ? ` · Last error: ${lastError}` : ""}</footer>
      </div>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      {modal === "settings" && <SettingsModal settings={settings} onSave={handleSaveSettings} onClose={closeModal} />}
      {modal === "kill-switch" && <KillSwitchModal busy={killSwitchBusy} onConfirm={handleKillSwitch} onClose={closeModal} />}
      {modal !== null && typeof modal === "object" && (
        <ClosePositionModal
          symbol={modal.symbol}
          busy={closingSymbol === modal.symbol}
          onConfirm={() => handleClosePosition(modal.symbol)}
          onClose={closeModal}
        />
      )}
      {modal === "manual-order" && (
        <ManualOrderModal busy={manualOrderBusy} result={manualOrderResult} onSubmit={handleManualOrder} onClose={closeModal} />
      )}
    </>
  );
}

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("index.html is missing #root");
createRoot(rootElement).render(<App />);
