import { money, pct } from "../format.js";
import type { Account, Risk } from "../types.js";
import { Sparkline } from "./Sparkline.js";

interface Props {
  account: Account | null;
  risk: Risk | null;
  equitySeries: number[];
}

export function Hero({ account, risk, equitySeries }: Props) {
  const equity = account ? account.equity : null;
  const sessionStart = risk ? risk.session_start_equity : null;
  const dollarChange = equity !== null && sessionStart ? equity - sessionStart : null;
  const pctChange = dollarChange !== null && sessionStart ? (dollarChange / sessionStart) * 100 : null;
  const up = dollarChange === null || dollarChange >= 0;

  return (
    <div className="hero">
      <div className="hero-equity">
        <div className="label">Portfolio equity</div>
        <div className="value">{equity !== null ? money(equity) : "—"}</div>
        <div className={`delta ${up ? "up" : "down"}`}>
          {dollarChange !== null
            ? `${money(dollarChange, { signed: true })} (${pct(pctChange, { signed: true })}) today`
            : "Waiting for session data…"}
        </div>
        <div className="sparkline">
          <Sparkline series={equitySeries} />
        </div>
      </div>

      <div className="hero-side">
        <div className="stat-card">
          <div className="label">Buying power</div>
          <div className="value">{account ? money(account.buying_power) : "—"}</div>
          <div className="sub">Cash: {account ? money(account.cash) : "—"}</div>
        </div>
        <div className="stat-card">
          <div className="label">Daily drawdown</div>
          <div className="value">{risk ? pct(risk.daily_drawdown_pct) : "—"}</div>
          <div className="sub">Halt at {risk ? pct(risk.daily_drawdown_limit_pct) : "—"}</div>
        </div>
      </div>
    </div>
  );
}
