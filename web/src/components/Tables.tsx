import { money, num, pct } from "../format.js";
import type { Order, Position } from "../types.js";

interface PositionsProps {
  positions: Position[];
  onClose: (symbol: string) => void;
  closingSymbol: string | null;
}

export function PositionsTable({ positions, onClose, closingSymbol }: PositionsProps) {
  return (
    <div className="card">
      <div className="card-title">
        <span>Positions</span>
        <span className="muted">{positions.length}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Qty</th>
            <th>Avg entry</th>
            <th>Current</th>
            <th>Market value</th>
            <th>Unrealized P&L</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 && (
            <tr>
              <td colSpan={8}>
                <div className="empty-row">No open positions.</div>
              </td>
            </tr>
          )}
          {positions.map((p) => (
            <tr key={p.symbol}>
              <td>
                <strong>{p.symbol}</strong>
                {p.is_option && <> <span className="pill">OPT</span></>}
              </td>
              <td>
                <span className={`pill ${p.side}`}>{p.side}</span>
              </td>
              <td className="mono">{num(p.qty, 0)}</td>
              <td className="mono">{money(p.avg_entry_price)}</td>
              <td className="mono">{money(p.current_price)}</td>
              <td className="mono">{money(p.market_value)}</td>
              <td className={`mono ${p.unrealized_pl >= 0 ? "pos" : "neg"}`}>
                {money(p.unrealized_pl, { signed: true })} ({pct(p.unrealized_plpc, { signed: true })})
              </td>
              <td>
                <button className="btn danger small" disabled={closingSymbol === p.symbol} onClick={() => onClose(p.symbol)}>
                  {closingSymbol === p.symbol ? "Closing…" : "Close"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface OrdersProps {
  orders: Order[];
  onCancel: (orderId: string) => void;
  cancellingId: string | null;
}

export function OrdersTable({ orders, onCancel, cancellingId }: OrdersProps) {
  return (
    <div className="card">
      <div className="card-title">
        <span>Open orders</span>
        <span className="muted">{orders.length}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Type</th>
            <th>Qty</th>
            <th>Limit</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {orders.length === 0 && (
            <tr>
              <td colSpan={7}>
                <div className="empty-row">No open orders.</div>
              </td>
            </tr>
          )}
          {orders.map((o) => (
            <tr key={o.id}>
              <td>
                <strong>{o.symbol}</strong>
              </td>
              <td>
                <span className={`pill ${o.side}`}>{o.side}</span>
              </td>
              <td>{o.type}</td>
              <td className="mono">{o.qty ?? "—"}</td>
              <td className="mono">{o.limit_price ? money(o.limit_price) : "—"}</td>
              <td>
                <span className="pill">{o.status}</span>
              </td>
              <td>
                <button className="btn small" disabled={cancellingId === o.id} onClick={() => onCancel(o.id)}>
                  {cancellingId === o.id ? "Cancelling…" : "Cancel"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
