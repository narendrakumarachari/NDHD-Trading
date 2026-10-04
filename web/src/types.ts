// web/src/types.ts - the API's response shapes (mirrors api/schemas.py).
// Keep these in step with the Pydantic models; /docs shows the live contract.

export interface Settings {
  apiBase: string;
  apiKey: string;
}

export interface Account {
  status: string;
  currency: string;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  trading_blocked: boolean;
  account_blocked: boolean;
  paper: boolean;
  live_trading: boolean;
  pdt_fields_present: boolean;
  daytrade_count: number | null;
}

export interface Position {
  symbol: string;
  asset_class: string;
  is_option: boolean;
  side: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
}

export interface Order {
  id: string;
  symbol: string;
  side: string;
  type: string;
  status: string;
  qty: number | null;
  filled_qty: number | null;
  limit_price: number | null;
  stop_price: number | null;
  filled_avg_price: number | null;
  client_order_id: string | null;
  submitted_at: string | null;
  time_in_force: string | null;
}

export type Severity = "critical" | "warning" | "info";

export interface Alert {
  ts: string;
  subject: string;
  body: string;
  key: string | null;
  severity: Severity;
}

export interface StockIndicators {
  available: boolean;
  error: string | null;
  price: number | null;
  ema_fast: number | null;
  ema_slow: number | null;
  atr: number | null;
  adx: number | null;
  trending: boolean | null;
  signal: string | null;
  spread_pct: number | null;
}

export interface StockStrategy {
  symbol: string;
  direction: string;
  status: string;
  entry_price: number | null;
  initial_stop_price: number | null;
  trailing_stop_price: number | null;
  activated_trailing: boolean;
  last_signal: string | null;
  last_atr: number | null;
  indicators: StockIndicators;
}

export interface WheelQuote {
  bid: number;
  ask: number;
  mid: number;
}

export interface WheelStrategy {
  symbol: string;
  phase: string;
  option_symbol: string | null;
  option_type: string | null;
  entry_premium: number | null;
  assignment_basis: number | null;
  contracts: number;
  expected_shares: number;
  quote: WheelQuote | null;
  unrealized_gain_pct: number | null;
}

export interface PdtProtection {
  enabled_in_config: boolean;
  effective: boolean;
  note: string;
}

export interface Risk {
  equity: number;
  session_start_equity: number | null;
  daily_drawdown_pct: number;
  daily_drawdown_limit_pct: number;
  daily_risk_halted: boolean;
  kill_switch_active: boolean;
  kill_switch_date: string | null;
  stock_exposure: number;
  stock_exposure_cap: number;
  stock_exposure_pct_of_cap: number;
  sector_exposure_pct: Record<string, number>;
  sector_cap_pct: number;
  stock_position_count: number;
  stock_position_cap: number;
  wheel_collateral_cap_pct: number;
  pdt_protection: PdtProtection;
}

export interface LogEntry {
  line: string;
  level: string;
}

export interface LiveData {
  account: Account | null;
  positions: Position[];
  orders: Order[];
  alerts: Alert[];
  stocks: StockStrategy[];
  wheels: WheelStrategy[];
  risk: Risk | null;
  logLines: LogEntry[];
  lastUpdated: string | null;
}

// WebSocket messages from api/ws.py.
export type LiveMessage =
  | { type: "tick"; account: Account | null; positions: Position[]; orders: Order[]; alerts: Alert[] }
  | { type: "strategy"; stocks: StockStrategy[]; wheels: WheelStrategy[]; risk: Risk | null }
  | { type: "log"; entries: LogEntry[]; backlog?: boolean }
  | { type: "error"; tier: string; message: string };

export interface ManualOrderRequest {
  symbol: string;
  asset_class: "equity" | "option";
  side: "buy" | "sell";
  qty: number;
  order_type: "market" | "limit";
  limit_price: number | null;
  position_intent: string | null;
  confirm: boolean;
}

export interface ManualOrderResult {
  accepted: boolean;
  reason: string | null;
  order_id: string | null;
  status: string | null;
  dry_run: boolean;
  risk_check_passed: boolean | null;
}

// ---------------------------------------------------------------------------
// Congressional trade disclosures (GET /api/congress). Research / advisory only.
// ---------------------------------------------------------------------------

export type CongressStatus = "ok" | "stale" | "missing" | "unreadable";

export interface CongressTrade {
  filer: string;
  party: string | null;
  chamber: string | null;
  ticker: string | null;
  asset_name: string;
  direction: string;
  transaction_type: string | null;
  owner: string | null;
  trade_date: string;
  filing_date: string;
  filing_lag_days: number;
  amount_low: number;
  amount_high: number | null;
  amount_mid_estimate: number;
  source_status: string;
  filing_url: string | null;
}

export interface CongressCluster {
  direction: string;
  filers: string[];
}

export interface CongressSymbol {
  symbol: string;
  roles: string[];
  engine_alert: boolean;
  reason: string;
  headline: string | null;
  cluster: CongressCluster | null;
  verified_trades: CongressTrade[];
  unverified_count: number;
}

export interface CongressRuleCluster {
  ticker: string;
  direction: string;
  filers: string[];
  headline: string;
}

export interface CongressActivity {
  ticker: string;
  name: string;
  sector: string;
  buyers: string[];
  sellers: string[];
  trades: number;
  buy_est: number;
  sell_est: number;
  latest_filing: string;
}

export interface CongressReview {
  filing_id: string;
  filer: string;
  filing_date: string;
  filing_url: string;
  reason: string;
  record: number | null;
}

export interface CongressView {
  status: CongressStatus;
  message: string | null;
  data_file: string;
  as_of: string | null;
  window_days: number | null;
  business_days_old: number | null;
  stale: boolean;
  engine_flag_in_env: boolean;
  counts: Record<string, number>;
  by_party: Record<string, { buys: number; sells: number }>;
  your_symbols: CongressSymbol[];
  engine_rule_clusters: CongressRuleCluster[];
  most_active: CongressActivity[];
  recent_verified: CongressTrade[];
  needs_review: CongressReview[];
  ledger_available: boolean;
  note: string;
}
