// web/src/api.ts
// Thin REST client + WebSocket hook for the dashboard API (api/main.py).

import { useEffect, useRef, useState } from "react";
import type { Alert, LiveData, LiveMessage, Settings } from "./types.js";

const SETTINGS_KEY = "ndhd_dashboard_settings_v1";

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { apiBase: "", apiKey: "" };
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return { apiBase: parsed.apiBase || "", apiKey: parsed.apiKey || "" };
  } catch {
    return { apiBase: "", apiKey: "" };
  }
}

export function saveSettings(settings: Settings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Private windows can refuse storage; the settings still apply for this session.
  }
}

export function apiUrl(base: string, path: string): string {
  return `${(base || "").replace(/\/$/, "")}${path}`;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly body: unknown) {
    super(message);
  }
}

export async function apiFetch<T>(settings: Settings, path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(options.headers as Record<string, string>) };
  if (settings.apiKey) headers["X-API-Key"] = settings.apiKey;

  const res = await fetch(apiUrl(settings.apiBase, path), { ...options, headers });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const b = body as { detail?: unknown; message?: unknown } | null;
    const detail = (b && (b.detail || b.message)) || res.statusText;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status, body);
  }
  return body as T;
}

function wsUrl(settings: Settings): string {
  const base = settings.apiBase
    ? settings.apiBase.replace(/^http/, "ws")
    : `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  const qs = settings.apiKey ? `?api_key=${encodeURIComponent(settings.apiKey)}` : "";
  return `${base.replace(/\/$/, "")}/ws${qs}`;
}

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const MAX_LOG_LINES = 500;

const EMPTY: LiveData = {
  account: null,
  positions: [],
  orders: [],
  alerts: [],
  stocks: [],
  wheels: [],
  risk: null,
  logLines: [],
  lastUpdated: null,
};

/**
 * Connects to /ws, applies incoming "tick"/"strategy"/"log" messages to state,
 * and auto-reconnects with capped exponential backoff.
 */
export function useLiveData(settings: Settings) {
  const [connected, setConnected] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [data, setData] = useState<LiveData>(EMPTY);
  const [newAlerts, setNewAlerts] = useState<Alert[]>([]);
  const seenAlertKeys = useRef(new Set<string>());
  const retryDelay = useRef(RECONNECT_MIN_MS);
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;

    function scheduleReconnect() {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        retryDelay.current = Math.min(retryDelay.current * 1.6, RECONNECT_MAX_MS);
        connect();
      }, retryDelay.current);
    }

    function handle(msg: LiveMessage) {
      switch (msg.type) {
        case "tick": {
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
          if (fresh.length) setNewAlerts((prev) => [...prev, ...fresh]);
          break;
        }
        case "strategy":
          setData((prev) => ({ ...prev, stocks: msg.stocks, wheels: msg.wheels, risk: msg.risk, lastUpdated: new Date().toISOString() }));
          break;
        case "log":
          setData((prev) => {
            const combined = [...prev.logLines, ...(msg.entries || [])];
            return { ...prev, logLines: combined.length > MAX_LOG_LINES ? combined.slice(-MAX_LOG_LINES) : combined };
          });
          break;
        case "error":
          setLastError(msg.message);
          break;
      }
    }

    function connect() {
      if (stopped.current) return;
      let socket: WebSocket;
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
      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          handle(JSON.parse(event.data) as LiveMessage);
        } catch {
          // Ignore a malformed frame; the next tick carries a full snapshot.
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!stopped.current) scheduleReconnect();
      };
      socket.onerror = () => socket.close();
    }

    connect();

    return () => {
      stopped.current = true;
      clearTimeout(timerRef.current);
      socketRef.current?.close();
    };
  }, [settings.apiBase, settings.apiKey]);

  function consumeNewAlert(index: number) {
    setNewAlerts((prev) => prev.filter((_, i) => i !== index));
  }

  function clearLogs() {
    setData((prev) => ({ ...prev, logLines: [] }));
  }

  return { connected, lastError, data, newAlerts, consumeNewAlert, clearLogs };
}

/** Polls a REST endpoint on an interval (for data that changes slowly, like /api/congress). */
export function usePolled<T>(settings: Settings, path: string, everyMs: number) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const next = await apiFetch<T>(settings, path);
        if (!cancelled) {
          setValue(next);
          setError(null);
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      }
    }
    void load();
    const timer = setInterval(() => void load(), everyMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [settings.apiBase, settings.apiKey, path, everyMs]);

  return { value, error };
}
