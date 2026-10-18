# robinhood_view

The dashboard's **Robinhood** panel: a read-only snapshot of your own
Robinhood accounts, compared with this project's risk limits.

- **Human research only.** Nothing here can place, change or cancel an order,
  and `portfolio_engine.py` never reads it.
- **Where the data comes from:** Claude Code reads Robinhood through
  Robinhood's official MCP server (`robinhood-trading`), using read-only tools.
  Order-placing tools are denied in `.claude/settings.local.json`.
- **Where it is stored:** `data/robinhood_snapshot.json` (git-ignored, like all
  of `data/`). Change it with `ROBINHOOD_SNAPSHOT_FILE`.
- **Account numbers:** only the last four digits are ever written.

## Refreshing

In Claude Code, ask: *"refresh my Robinhood snapshot"*. Claude reads the
accounts, holdings, quotes, open orders, covered calls and earnings dates,
rewrites the file, and you click **Reload** in the panel.

## Snapshot shape (`schema: 1`)

```json
{
  "schema": 1,
  "taken_at": "2026-10-18T19:46:00Z",
  "prices_note": "Stocks: Friday close. Crypto: live.",
  "accounts": [
    {
      "label": "Main", "last4": "5406", "kind": "margin", "managed": false,
      "default": true, "agent_can_trade": false, "trade_approvals_required": null,
      "total_value": 4323.40, "cash": 642.15, "buying_power": 642.15, "options_value": -19,
      "stocks": [{"symbol": "QS", "quantity": 133.03, "avg_cost": 6.32, "price": 4.02,
                  "next_earnings": "2026-10-21", "earnings_timing": "pm", "earnings_confirmed": true}],
      "covered_calls": [{"symbol": "QS", "contracts": 1, "strike": 8, "expiration": "2027-01-15",
                         "premium_per_share": 0.10}],
      "crypto": [{"symbol": "DOGE", "quantity": 487.43, "cost": 72.03, "price": 0.0762}],
      "open_orders": [{"asset": "ADA", "kind": "crypto", "side": "sell", "type": "limit",
                       "quantity": 134.37, "limit_price": 0.33, "price": 0.2115, "placed": "2026-09-28"}]
    }
  ]
}
```

The **default** account is the one shown in detail. `trade_approvals_required`
applies only to the account an agent can trade in.

## Tests

```powershell
python -m unittest discover -s robinhood_view -t . -v
```
