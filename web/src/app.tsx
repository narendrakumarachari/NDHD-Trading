import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { apiFetch, loadSettings, saveSettings, useAuth, useCongress, useLiveData, useRobinhood, useWatch } from "./api.js";
import type { Alert, CongressSymbol, ManualOrderRequest, ManualOrderResult, Section, Settings } from "./types.js";

import { AlertsFeed, ToastStack } from "./components/Alerts.js";
import { CongressPanel } from "./components/CongressPanel.js";
import { Hero } from "./components/Hero.js";
import { LiveConsole } from "./components/LiveConsole.js";
import { ClosePositionModal, KillSwitchModal, ManualOrderModal, SettingsModal } from "./components/Modals.js";
import { RiskPanel } from "./components/RiskPanel.js";
import { RobinhoodPanel } from "./components/RobinhoodPanel.js";
import { SignInScreen } from "./components/SignIn.js";
import { StockPanel, WheelPanel } from "./components/StrategyPanels.js";
import { OrdersTable, PositionsTable } from "./components/Tables.js";
import { TopBar } from "./components/TopBar.js";
import { WatchPanel } from "./components/WatchPanel.js";

const MAX_EQUITY_POINTS = 180;

type Modal = null | "settings" | "kill-switch" | "manual-order" | { type: "close"; symbol: string };

function App() {
  const [settings, setSettings] = useState<Settings>(loadSettings());
  // Who's signed in decides what loads and shows. The server enforces the
  // same sections on every route and on /ws; hiding here just avoids
  // showing panels that would only say "forbidden".
  const auth = useAuth(settings);
  const signedIn = auth.me?.signed_in ?? false;
  const can = (section: Section) => signedIn && (auth.me?.sections ?? []).includes(section);
  const { connected, lastError, data, newAlerts, consumeNewAlert, clearLogs } = useLiveData(settings, signedIn);
  // Congress data loads once and after an on-demand pull; never on a timer.
  const congress = useCongress(settings, can("congress"));
  // Robinhood snapshot: read once, and again when "Reload" is clicked.
  const robinhood = useRobinhood(settings, can("robinhood"));
  // Watch-only signals: on open and on "Refresh" (the API caches 10 min).
  const watch = useWatch(settings, can("watch"));
  const congressBySymbol = useMemo(() => {
    const map: Record<string, CongressSymbol> = {};
    for (const s of congress.view?.your_symbols ?? []) map[s.symbol] = s;
    return map;
  }, [congress.view]);

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

  // Signing out leaves nothing of the last user's data on the page.
  useEffect(() => {
    if (signedIn) return;
    setEquitySeries([]);
    setToasts([]);
    setActionError(null);
  }, [signedIn]);

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
  const control = can("control");
  const settingsModal = modal === "settings" && (
    <SettingsModal settings={settings} onSave={handleSaveSettings} onClose={closeModal} />
  );

  if (auth.me === null) {
    return <div className="signin-wrap muted">Loading…</div>;
  }
  if (!signedIn) {
    return (
      <>
        <SignInScreen me={auth.me} loadError={auth.error} onSignIn={auth.signIn} onOpenSettings={() => setModal("settings")} />
        {settingsModal}
      </>
    );
  }

  return (
    <>
      <TopBar
        account={data.account}
        connected={connected}
        congress={congress.view}
        onOpenSettings={() => setModal("settings")}
        onOpenKillSwitch={control ? () => setModal("kill-switch") : undefined}
        killSwitchBusy={killSwitchBusy}
        showCongress={can("congress")}
        me={auth.me}
        onSignOut={() => void auth.signOut()}
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

        {can("account") && <Hero account={data.account} risk={risk} equitySeries={equitySeries} />}

        {can("alerts") && (
          <>
            <div className="section-title">Live engine console</div>
            <LiveConsole lines={data.logLines} connected={connected} onClear={clearLogs} />
          </>
        )}

        {(can("strategy") || can("account")) && (
          <>
            <div className="section-title">Risk & positions</div>
            <div className="grid-2">
              {can("strategy") && <RiskPanel risk={risk} />}
              {can("account") && (
                <PositionsTable
                  positions={data.positions}
                  onClose={control ? (symbol) => setModal({ type: "close", symbol }) : undefined}
                  closingSymbol={closingSymbol}
                />
              )}
            </div>
          </>
        )}

        {can("strategy") && (
          <>
            <div className="section-title">Strategies</div>
            <div className="grid-2">
              <StockPanel stocks={data.stocks} market={data.market} congress={congressBySymbol} />
              <WheelPanel wheels={data.wheels} market={data.market} congress={congressBySymbol} />
            </div>
          </>
        )}

        {can("congress") && (
          <>
            <div className="section-title">Congress trades (research · advisory only)</div>
            <CongressPanel
              view={congress.view}
              error={congress.error}
              settings={settings}
              pullStatus={congress.pullStatus}
              onPull={control ? congress.pull : undefined}
            />
          </>
        )}

        {can("robinhood") && (
          <>
            <div className="section-title">Robinhood (your accounts · read-only)</div>
            <RobinhoodPanel view={robinhood.view} error={robinhood.error} onReload={robinhood.reload} />
          </>
        )}

        {can("watch") && (
          <>
            <div className="section-title">Watch list (signals only · never traded)</div>
            <WatchPanel view={watch.view} error={watch.error} loading={watch.loading} onRefresh={watch.reload} />
          </>
        )}

        {(can("account") || can("alerts")) && (
          <>
            <div className="section-title">Orders & activity</div>
            <div className="grid-2">
              {can("account") && (
                <OrdersTable orders={data.orders} onCancel={control ? handleCancelOrder : undefined} cancellingId={cancellingId} />
              )}
              {can("alerts") && <AlertsFeed alerts={data.alerts} />}
            </div>
          </>
        )}

        {control && (
          <>
            <div className="section-title">Manual control</div>
            <div className="card">
              <div className="card-title">
                <span>Submit a manual order</span>
              </div>
              <p className="muted" style={{ margin: "6px 0 12px" }}>
                Opens the manual order form. Orders that open/increase exposure still pass the account's configured
                risk limits.
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
          </>
        )}

        <footer className="hint">NDHD Trading Dashboard{lastError ? ` · Last error: ${lastError}` : ""}</footer>
      </div>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      {settingsModal}
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
