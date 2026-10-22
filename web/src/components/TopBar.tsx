import type { Account, CongressView, Me } from "../types.js";
import { congressAge } from "./CongressPanel.js";

interface Props {
  account: Account | null;
  connected: boolean;
  congress: CongressView | null;
  onOpenSettings: () => void;
  /** Omitted when this role can't use controls: no kill switch button. */
  onOpenKillSwitch?: () => void;
  killSwitchBusy: boolean;
  /** False hides the congress data badge. */
  showCongress: boolean;
  me: Me;
  onSignOut: () => void;
}

export function TopBar({
  account,
  connected,
  congress,
  onOpenSettings,
  onOpenKillSwitch,
  killSwitchBusy,
  showCongress,
  me,
  onSignOut,
}: Props) {
  const age = congressAge(congress);
  return (
    <div className="topbar">
      <div className="brand">
        <div className="brand-mark">N</div>
        <span>NDHD Trading</span>
      </div>

      {account && (
        <span
          className={`badge ${account.paper ? "paper" : "live"}`}
          title={account.orders_enabled ? "Orders are sent to the broker" : "Dry run: orders are only logged, nothing is sent"}
        >
          {account.paper ? (account.orders_enabled ? "PAPER · orders on" : "PAPER · dry run") : "LIVE TRADING"}
        </span>
      )}

      <span className="badge">
        <span className={`dot ${connected ? "up" : "down"}`} />
        {connected ? "Live" : "Reconnecting…"}
      </span>

      {showCongress && (
        <a className={`badge congress-age ${age.tone}`} href="#congress" title="Congress trades data: how old it is">
          Congress data: {age.label}
        </a>
      )}

      <div className="topbar-spacer" />

      {me.via === "session" && (
        <span className="badge user-badge" title={`Signed in as ${me.username} (${me.role})`}>
          {me.username} · {me.role}
          <button className="link-btn" onClick={onSignOut}>
            Sign out
          </button>
        </span>
      )}

      {onOpenKillSwitch && (
        <button className="kill-switch-btn" disabled={killSwitchBusy} onClick={onOpenKillSwitch}>
          {killSwitchBusy ? "Working…" : "Kill switch"}
        </button>
      )}

      <button className="icon-btn" title="Settings" onClick={onOpenSettings}>
        ⚙
      </button>
    </div>
  );
}
