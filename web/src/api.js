// web/src/api.js
// Thin REST client + WebSocket hook for the dashboard API (api/main.py).
// No build step (see web/README.md for why) - plain ES modules only.

import React from "react";

const SETTINGS_KEY = "ndhd_dashboard_settings_v1";

export function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { apiBase: "", apiKey: "" };
    const parsed = JSON.parse(raw);
    return { apiBase: parsed.apiBase || "", apiKey: parsed.apiKey || "" };
  } catch {
    return { apiBase: "", apiKey: "" };
  }
}

export function saveSettings(settings) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

function apiUrl(base, path) {
  const trimmed = (base || "").replace(/\/$/, "");
  return `${trimmed}${path}`;
}

export async function apiFetch(settings, path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (settings.apiKey) headers["X-API-Key"] = settings.apiKey;

  const res = await fetch(apiUrl(settings.apiBase, path), { ...options, headers });
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail = (body && (body.detail || body.message)) || res.statusText;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

function wsUrl(settings) {
  let base = settings.apiBase;
  if (!base) {
    base = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  } else {
    base = base.replace(/^http/, "ws");
  }
  const qs = settings.apiKey ? `?api_key=${encodeURIComponent(settings.apiKey)}` : "";
  return `${base.replace(/\/$/, "")}/ws${qs}`;
}

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const MAX_LOG_LINES = 500;

/**
 * Connects to /ws, applies incoming "tick"/"strategy" messages to state,
 * and auto-reconnects with capped exponential backoff. Returns
 * { connected, lastError, data } where `data` accumulates the latest
 * known snapshot fields from both message tiers.
 */
export function useLiveData(settings) {
  const [connected, setConnected] = React.useState(false);
  const [lastError, setLastError] = React.useState(null);
  const [data, setData] = React.useState({
    account: null,
    positions: [],
    orders: [],
    alerts: [],
    stocks: [],
    wheels: [],
    risk: null,
    logLines: [],
    lastUpdated: null,
  });
  const [newAlerts, setNewAlerts] = React.useState([]);
  const seenAlertKeys = React.useRef(new Set());
  const retryDelay = React.useRef(RECONNECT_MIN_MS);
  const socketRef = React.useRef(null);
  const timerRef = React.useRef(null);
  const stopped = React.useRef(false);

  React.useEffect(() => {
    stopped.current = false;

    function connect() {
      if (stopped.current) return;
      let socket;
      try {
        socket = new WebSocket(wsUrl(settings));
      } catch (exc) {
        setLastError(String(exc));
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        setConnected(true);
        setLastError(null);
        retryDelay.current = RECONNECT_MIN_MS;
      };

      socket.onmessage = (event) => {
        let msg;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }

        if (msg.type === "tick") {
          setData((prev) => ({
            ...prev,
            account: msg.account,
            positions: msg.positions,
            orders: msg.orders,
            alerts: msg.alerts,
            lastUpdated: new Date().toISOString(),
          }));

          const fresh = (msg.alerts || []).filter((a) => {
            const key = `${a.ts}|${a.subject}`;
            if (seenAlertKeys.current.has(key)) return false;
            seenAlertKeys.current.add(key);
            return true;
          });
          if (fresh.length) {
            setNewAlerts((prev) => [...prev, ...fresh]);
          }
        } else if (msg.type === "strategy") {
          setData((prev) => ({
            ...prev,
            stocks: msg.stocks,
            wheels: msg.wheels,
            risk: msg.risk,
            lastUpdated: new Date().toISOString(),
          }));
        } else if (msg.type === "log") {
          setData((prev) => {
            const combined = [...prev.logLines, ...(msg.entries || [])];
            const trimmed =
              combined.length > MAX_LOG_LINES ? combined.slice(combined.length - MAX_LOG_LINES) : combined;
            return { ...prev, logLines: trimmed };
          });
        } else if (msg.type === "error") {
          setLastError(msg.message);
        }
      };

      socket.onclose = () => {
        setConnected(false);
        if (!stopped.current) scheduleReconnect();
      };

      socket.onerror = () => {
        socket.close();
      };
    }

    function scheduleReconnect() {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        retryDelay.current = Math.min(retryDelay.current * 1.6, RECONNECT_MAX_MS);
        connect();
      }, retryDelay.current);
    }

    connect();

    return () => {
      stopped.current = true;
      clearTimeout(timerRef.current);
      socketRef.current && socketRef.current.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.apiBase, settings.apiKey]);

  function consumeNewAlert(index) {
    setNewAlerts((prev) => prev.filter((_, i) => i !== index));
  }

  function clearLogs() {
    setData((prev) => ({ ...prev, logLines: [] }));
  }

  return { connected, lastError, data, newAlerts, consumeNewAlert, clearLogs };
}
