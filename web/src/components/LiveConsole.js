import React from "react";
import { html } from "../html.js";

// Renders the raw log stream from portfolio_engine.py / the dashboard API
// (see api/logs.py, api/ws.py's "log" message type) - proof of liveness
// even on ticks where account/positions/indicators haven't changed.
export function LiveConsole({ lines, connected, onClear }) {
  const bodyRef = React.useRef(null);
  const stickToBottom = React.useRef(true);

  function handleScroll(e) {
    const el = e.target;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  React.useEffect(() => {
    if (stickToBottom.current && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [lines.length]);

  return html`
    <div class="console-card">
      <div class="console-header">
        <span class="pulse ${connected ? "" : "stale"}" />
        <span class="title">Live engine console</span>
        <span class="spacer" />
        <span class="count">${lines.length} lines</span>
        <button onClick=${onClear}>Clear</button>
      </div>
      <div class="console-body" ref=${bodyRef} onScroll=${handleScroll}>
        ${lines.length === 0
          ? html`<div class="console-empty">
              Waiting for log output… if this stays empty, the trading engine hasn't been
              restarted since LOG_FILE was added, or it isn't running yet.
            </div>`
          : lines.map(
              (entry, i) => html`<div class="console-line ${entry.level}" key=${i}>${entry.line}</div>`
            )}
      </div>
    </div>
  `;
}
