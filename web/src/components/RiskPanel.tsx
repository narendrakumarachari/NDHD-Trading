import { money, pct } from "../format.js";
import type { Risk } from "../types.js";

function Meter({ label, valueLabel, fraction }: { label: string; valueLabel: string; fraction: number }) {
  const clamped = Math.max(0, Math.min(1, fraction || 0));
  const cls = clamped > 0.95 ? "danger" : clamped > 0.75 ? "warn" : "";
  return (
    <div className="meter">
      <div className="meter-row">
        <span>{label}</span>
        <span>{valueLabel}</span>
      </div>
      <div className="meter-track">
        <div className={`meter-fill ${cls}`} style={{ width: `${clamped * 100}%` }} />
      </div>
    </div>
  );
}

export function RiskPanel({ risk }: { risk: Risk | null }) {
  if (!risk) {
    return (
      <div className="card">
        <div className="card-title">Risk</div>
        <div className="muted">Loading…</div>
      </div>
    );
  }

  const ratio = (value: number, cap: number) => (cap > 0 ? value / cap : 0);
  const sectorEntries = Object.entries(risk.sector_exposure_pct || {});

  return (
    <div className="card">
      <div className="card-title">
        <span>Risk & guardrails</span>
        {risk.kill_switch_active && (
          <span className="pill" style={{ background: "#fff0f5", color: "#d6336c", borderColor: "#ffc9dd" }}>
            Kill switch active
          </span>
        )}
      </div>

      <Meter
        label="Daily drawdown"
        valueLabel={`${pct(risk.daily_drawdown_pct)} / ${pct(risk.daily_drawdown_limit_pct)}`}
        fraction={ratio(risk.daily_drawdown_pct, risk.daily_drawdown_limit_pct)}
      />
      <Meter
        label="Stock exposure"
        valueLabel={`${money(risk.stock_exposure)} / ${money(risk.stock_exposure_cap)}`}
        fraction={ratio(risk.stock_exposure, risk.stock_exposure_cap)}
      />
      <Meter
        label="Stock positions"
        valueLabel={`${risk.stock_position_count} / ${risk.stock_position_cap}`}
        fraction={ratio(risk.stock_position_count, risk.stock_position_cap)}
      />

      {sectorEntries.length > 0 && (
        <div style={{ marginTop: "14px" }}>
          <div className="meter-row">
            <span>Sector exposure (cap {pct(risk.sector_cap_pct)})</span>
          </div>
          {sectorEntries.map(([sector, value]) => (
            <Meter key={sector} label={sector} valueLabel={pct(value)} fraction={ratio(value, risk.sector_cap_pct)} />
          ))}
        </div>
      )}

      <div
        className={`banner ${risk.pdt_protection.effective ? "reconnect" : "danger"}`}
        style={{ marginTop: "16px", marginBottom: 0 }}
      >
        <div>
          <strong>PDT protection: {risk.pdt_protection.effective ? "active" : "degraded"}</strong>
          {risk.pdt_protection.note}
        </div>
      </div>
    </div>
  );
}
