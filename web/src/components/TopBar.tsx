import type { Account, CongressView } from "../types.js";
import { congressAge } from "./CongressPanel.js";

interface Props {
  account: Account | null;
  connected: boolean;
  congress: CongressView | null;
  onOpenSettings: () => void;
  onOpenKillSwitch: () => void;
  killSwitchBusy: boolean;
}

export function TopBar({ account, connected, congress, onOpenSettings, onOpenKillSwitch, killSwitchBusy }: Props) {
  const age = congressAge(congress);
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

      <a className={`badge congress-age ${age.tone}`} href="#congress" title="Congress trades data: how old it is">
        Congress data: {age.label}
      </a>

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
