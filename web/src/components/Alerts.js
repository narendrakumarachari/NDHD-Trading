import React from "react";
import { html } from "../html.js";
import { timeAgo } from "../format.js";

export function AlertsFeed({ alerts }) {
  return html`
    <div class="card">
      <div class="card-title"><span>Alert feed</span><span class="muted">${alerts.length}</span></div>
      <div class="alerts-feed">
        ${alerts.length === 0 && html`<div class="empty-row">No alerts yet.</div>`}
        ${alerts.map(
          (a, i) => html`
            <div class="alert-item ${a.severity}" key=${`${a.ts}-${i}`}>
              <div class="alert-severity-dot ${a.severity}" />
              <div>
                <div class="alert-subject">${a.subject}</div>
                <div class="alert-body">${a.body}</div>
                <div class="alert-ts">${timeAgo(a.ts)}</div>
              </div>
            </div>
          `
        )}
      </div>
    </div>
  `;
}

const TOAST_MS = 6500;

export function ToastStack({ toasts, onDismiss }) {
  React.useEffect(() => {
    if (toasts.length === 0) return undefined;
    const timer = setTimeout(() => onDismiss(0), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toasts.length]);

  return html`
    <div class="toast-stack">
      ${toasts.slice(0, 4).map(
        (t, i) => html`
          <div class="toast ${t.severity}" key=${`${t.ts}-${i}`}>
            <div class="subject">${t.subject}</div>
            <div class="body">${t.body}</div>
            <div class="toast-timer" style=${{ animationDuration: `${TOAST_MS}ms` }} />
          </div>
        `
      )}
    </div>
  `;
}
