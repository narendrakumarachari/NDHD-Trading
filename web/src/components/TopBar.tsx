import type { Account } from "../types.js";

interface Props {
  account: Account | null;
  connected: boolean;
  onOpenSettings: () => void;
  onOpenKillSwitch: () => void;
  killSwitchBusy: boolean;
}

export function TopBar({ account, connected, onOpenSettings, onOpenKillSwitch, killSwitchBusy }: Props) {
  return (
    <div className="topbar">
      <div className="brand">
        <div className="brand-mark">N</div>
        <span>NDHD Trading</span>
      </div>

      {account && (
        <span className={`badge ${account.paper ? "paper" : "live"}`}>{account.paper ? "PAPER" : "LIVE TRADING"}</span>
      )}

      <span className="badge">
        <span className={`dot ${connected ? "up" : "down"}`} />
        {connected ? "Live" : "Reconnecting…"}
      </span>

      <div className="topbar-spacer" />

      <button className="kill-switch-btn" disabled={killSwitchBusy} onClick={onOpenKillSwitch}>
        {killSwitchBusy ? "Working…" : "Kill switch"}
      </button>

      <button className="icon-btn" title="Settings" onClick={onOpenSettings}>
        ⚙
      </button>
    </div>
  );
}
