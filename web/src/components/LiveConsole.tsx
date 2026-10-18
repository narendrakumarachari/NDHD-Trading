import { useEffect, useRef, type UIEvent } from "react";
import type { LogEntry } from "../types.js";

// Renders the raw log stream from portfolio_engine.py / the dashboard API
// (see api/logs.py, api/ws.py's "log" message type) - proof of liveness
// even on ticks where account/positions/indicators haven't changed.

interface Props {
  lines: LogEntry[];
  connected: boolean;
  onClear: () => void;
}

export function LiveConsole({ lines, connected, onClear }: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  function handleScroll(e: UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  useEffect(() => {
    if (stickToBottom.current && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [lines.length]);

  return (
    <div className="console-card">
      <div className="console-header">
        <span className={`pulse ${connected ? "" : "stale"}`} />
        <span className="title">Live engine console</span>
        <span className="spacer" />
        <span className="count">{lines.length} lines</span>
        <button onClick={onClear}>Clear</button>
      </div>
      <div className="console-body" ref={bodyRef} onScroll={handleScroll}>
        {lines.length === 0 ? (
          <div className="console-empty">
            Waiting for log output… if this stays empty, the trading engine hasn't been restarted since LOG_FILE was
            added, or it isn't running yet.
          </div>
        ) : (
          lines.map((entry, i) => (
            <div className={`console-line ${entry.level}`} key={i}>
              {entry.line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
