import { useEffect } from "react";
import { timeAgo } from "../format.js";
import type { Alert } from "../types.js";

export function AlertsFeed({ alerts }: { alerts: Alert[] }) {
  return (
    <div className="card">
      <div className="card-title">
        <span>Alert feed</span>
        <span className="muted">{alerts.length}</span>
      </div>
      <div className="alerts-feed">
        {alerts.length === 0 && <div className="empty-row">No alerts yet.</div>}
        {alerts.map((a, i) => (
          <div className={`alert-item ${a.severity}`} key={`${a.ts}-${i}`}>
            <div className={`alert-severity-dot ${a.severity}`} />
            <div>
              <div className="alert-subject">{a.subject}</div>
              <div className="alert-body">{a.body}</div>
              <div className="alert-ts">{timeAgo(a.ts)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const TOAST_MS = 6500;

export function ToastStack({ toasts, onDismiss }: { toasts: Alert[]; onDismiss: (index: number) => void }) {
  useEffect(() => {
    if (toasts.length === 0) return undefined;
    const timer = setTimeout(() => onDismiss(0), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toasts.length]);

  return (
    <div className="toast-stack">
      {toasts.slice(0, 4).map((t, i) => (
        <div className={`toast ${t.severity}`} key={`${t.ts}-${i}`}>
          <div className="subject">{t.subject}</div>
          <div className="body">{t.body}</div>
          <div className="toast-timer" style={{ animationDuration: `${TOAST_MS}ms` }} />
        </div>
      ))}
    </div>
  );
}
