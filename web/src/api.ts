// web/src/api.ts
// Thin REST client + WebSocket hook for the dashboard API (api/main.py).

import { useCallback, useEffect, useRef, useState } from "react";
import type { Alert, CongressPull, CongressView, LiveData, LiveMessage, Me, RobinhoodView, Settings, WatchView } from "./types.js";

const SETTINGS_KEY = "ndhd_dashboard_settings_v1";
// The API key is full admin access, so it is kept only for this tab
// (sessionStorage) and never saved for good: every new tab, browser restart
// or device starts at the sign-in screen.
const API_KEY_KEY = "ndhd_dashboard_api_key";

export function loadSettings(): Settings {
  let apiBase = "";
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Settings>;
      apiBase = parsed.apiBase || "";
      // Pages from before sign-in saved the key here; forget it.
      if (parsed.apiKey) localStorage.setItem(SETTINGS_KEY, JSON.stringify({ apiBase }));
    }
  } catch {
    // Storage refused or unreadable: defaults.
  }
  let apiKey = "";
  try {
    apiKey = sessionStorage.getItem(API_KEY_KEY) || "";
  } catch {
    // Storage refused: no key.
  }
  return { apiBase, apiKey };
}

export function saveSettings(settings: Settings): void {
  // Private windows can refuse storage; the settings still apply for this session.
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ apiBase: settings.apiBase }));
  } catch {
    // ignored
  }
  try {
    if (settings.apiKey) sessionStorage.setItem(API_KEY_KEY, settings.apiKey);
    else sessionStorage.removeItem(API_KEY_KEY);
  } catch {
    // ignored
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

const SIGNED_OUT: Me = { signed_in: false, users_enabled: false, username: null, role: null, sections: [], via: null };
// A server from before sign-in existed has no /api/auth/me (404): behave as
// before, with every section shown and the API key from Settings deciding.
const LEGACY_SERVER: Me = {
  signed_in: true,
  users_enabled: false,
  username: null,
  role: "admin",
  sections: ["account", "strategy", "alerts", "congress", "watch", "robinhood", "control"],
  via: "api_key",
};

/**
 * Who this browser is: signed in with a username (session cookie), by the
 * API key from Settings, or not at all. The server decides the sections;
 * the page only uses them to hide what would be refused anyway.
 */
export function useAuth(settings: Settings) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMe(await apiFetch<Me>(settings, "/api/auth/me"));
      setError(null);
    } catch (exc) {
      const legacy = exc instanceof ApiError && exc.status === 404;
      setMe(legacy ? LEGACY_SERVER : SIGNED_OUT);
      setError(legacy ? null : exc instanceof Error ? exc.message : String(exc));
    }
  }, [settings.apiBase, settings.apiKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function signIn(username: string, password: string): Promise<string | null> {
    try {
      setMe(await apiFetch<Me>(settings, "/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }));
      setError(null);
      return null;
    } catch (exc) {
      return exc instanceof Error ? exc.message : String(exc);
    }
  }

  async function signOut() {
    try {
      await apiFetch(settings, "/api/auth/logout", { method: "POST" });
    } finally {
      await load();
    }
  }

  return { me, error, signIn, signOut, reload: load };
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
  market: null,
  lastUpdated: null,
};

/**
 * Connects to /ws, applies incoming "tick"/"strategy"/"log" messages to state,
 * and auto-reconnects with capped exponential backoff. Stays disconnected
 * while `enabled` is false (not signed in). The server sends only the
 * message types this user's role may see.
 */
export function useLiveData(settings: Settings, enabled = true) {
  const [connected, setConnected] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [data, setData] = useState<LiveData>(EMPTY);
  const [newAlerts, setNewAlerts] = useState<Alert[]>([]);
  const seenAlertKeys = useRef(new Set<string>());
  // The first snapshot carries alerts that happened before the page opened.
  // They belong in the feed, not as pop-ups; only later arrivals toast.
  const alertsSeeded = useRef(false);
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
            alerts: msg.alerts ?? [],
            lastUpdated: new Date().toISOString(),
          }));
          const fresh = (msg.alerts || []).filter((a) => {
            const key = `${a.ts}|${a.subject}`;
            if (seenAlertKeys.current.has(key)) return false;
            seenAlertKeys.current.add(key);
            return true;
          });
          if (!alertsSeeded.current) {
            alertsSeeded.current = true;
          } else if (fresh.length) {
            setNewAlerts((prev) => [...prev, ...fresh]);
          }
          break;
        }
        case "strategy":
          setData((prev) => ({
            ...prev,
            stocks: msg.stocks,
            wheels: msg.wheels,
            risk: msg.risk,
            market: msg.market ?? prev.market,
            lastUpdated: new Date().toISOString(),
          }));
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

    if (enabled) connect();
    else setData(EMPTY);

    return () => {
      stopped.current = true;
      clearTimeout(timerRef.current);
      socketRef.current?.close();
    };
  }, [settings.apiBase, settings.apiKey, enabled]);

  function consumeNewAlert(index: number) {
    setNewAlerts((prev) => prev.filter((_, i) => i !== index));
  }

  function clearLogs() {
    setData((prev) => ({ ...prev, logLines: [] }));
  }

  return { connected, lastError, data, newAlerts, consumeNewAlert, clearLogs };
}

const PULL_STATUS_POLL_MS = 2000;

/**
 * Congress data, on demand. The view is read from the local data file once
 * when the page opens and again after a pull - never on a timer. `pull()`
 * asks the server to fetch only what's new from the House Clerk; while that
 * runs, its status is checked every 2 s, and polling stops when it finishes.
 */
export function useCongress(settings: Settings, enabled = true) {
  const [view, setView] = useState<CongressView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pullStatus, setPullStatus] = useState<CongressPull | null>(null);
  const message = (exc: unknown) => (exc instanceof Error ? exc.message : String(exc));

  const load = useCallback(async () => {
    try {
      setView(await apiFetch<CongressView>(settings, "/api/congress"));
      setError(null);
    } catch (exc) {
      setError(message(exc));
    }
  }, [settings.apiBase, settings.apiKey]);

  useEffect(() => {
    if (!enabled) {
      setView(null);
      return;
    }
    void load();
    apiFetch<CongressPull>(settings, "/api/congress/refresh").then(setPullStatus, () => undefined);
  }, [load, enabled]);

  const running = pullStatus?.state === "running";
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(async () => {
      try {
        const next = await apiFetch<CongressPull>(settings, "/api/congress/refresh");
        setPullStatus(next);
        if (next.state !== "running") void load();
      } catch (exc) {
        setError(message(exc));
      }
    }, PULL_STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  async function pull() {
    try {
      setPullStatus(await apiFetch<CongressPull>(settings, "/api/congress/refresh", { method: "POST" }));
    } catch (exc) {
      setError(message(exc));
    }
  }

  return { view, error, pullStatus, pull };
}

/**
 * The Robinhood snapshot, read from the local data file when the page opens
 * and when "Reload" is clicked. Claude Code writes the file; this only reads it.
 */
export function useRobinhood(settings: Settings, enabled = true) {
  const [view, setView] = useState<RobinhoodView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await apiFetch<RobinhoodView>(settings, "/api/robinhood"));
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [settings.apiBase, settings.apiKey]);

  useEffect(() => {
    // Never fetched for a role without the section; and cleared on sign-out.
    if (enabled) void load();
    else setView(null);
  }, [load, enabled]);

  return { view, error, reload: load };
}

/**
 * The watch-only list, loaded when the page opens and on "Refresh". The API
 * caches it for 10 minutes; refresh=true recomputes now.
 */
export function useWatch(settings: Settings, enabled = true) {
  const [view, setView] = useState<WatchView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      try {
        setView(await apiFetch<WatchView>(settings, `/api/watch${refresh ? "?refresh=true" : ""}`));
        setError(null);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setLoading(false);
      }
    },
    [settings.apiBase, settings.apiKey],
  );

  useEffect(() => {
    if (enabled) void load();
    else setView(null);
  }, [load, enabled]);

  return { view, error, loading, reload: () => load(true) };
}
