# Alpaca Portfolio Strategy Engine

An automated portfolio execution framework for Alpaca that combines a technical-analysis stock strategy, an options Wheel strategy, and portfolio-level risk controls.

> **Important:** This project is an execution framework, not investment advice and does not guarantee profitability. The project is configured to use Alpaca paper trading by default. Live trading must be explicitly enabled.

## Overview

The project is designed around three major components:

1. **Stock Strategy**
   - EMA 20/50 crossover entries
   - ATR-based initial protective stop
   - ATR-based trailing stop
   - Trailing stop only tightens; it does not loosen
   - Optional long, short, or both directions
   - Risk-based position sizing
   - Position and portfolio exposure limits
   - Earnings blackout protection
   - Stock liquidity/spread checks

2. **Wheel Strategy**
   - Cash-secured puts (CSP)
   - Target option duration of approximately 30–45 DTE
   - Target delta around 0.30
   - 50% premium-profit buyback target
   - Assignment detection/reconciliation
   - Transition from assigned shares to covered calls
   - Covered-call strike must be above the assignment basis
   - 50% premium-profit buyback target
   - Return to cash-secured puts after shares are called away

3. **Portfolio Risk Engine**
   - Maximum single-position exposure
   - Maximum total stock exposure
   - Maximum number of simultaneous stock positions
   - Maximum daily drawdown
   - Earnings blackout controls
   - Stock and option liquidity/spread checks
   - Persistent strategy state
   - Broker-state reconciliation
   - Idempotent client order IDs
   - Paper-trading default
   - Explicit live-trading opt-in

## Architecture

```text
                         +----------------------+
                         |   Portfolio Engine    |
                         +----------+-----------+
                                    |
                  +-----------------+-----------------+
                  |                                   |
          +-------v--------+                  +-------v--------+
          | Stock Strategy |                  | Wheel Strategy |
          +-------+--------+                  +-------+--------+
                  |                                   |
          +-------v--------+                  +-------v--------+
          | EMA / ATR      |                  | CSP / CC       |
          | Signals        |                  | Option Select  |
          | Stops          |                  | Profit Target  |
          +-------+--------+                  +-------+--------+
                  |                                   |
                  +-----------------+-----------------+
                                    |
                           +--------v---------+
                           |   Risk Engine    |
                           +--------+---------+
                                    |
                           +--------v---------+
                           |   Alpaca Client  |
                           +--------+---------+
                                    |
                           +--------v---------+
                           | Alpaca API       |
                           | Paper / Live     |
                           +------------------+

                     Persistent State
                            |
                            v
                 portfolio_engine_state.json
```

## Project Status

This repository is intended to be developed incrementally.

The current implementation is a single Python execution engine containing:

- Configuration management
- Alpaca REST API integration
- Stock market data retrieval
- Historical daily bars
- EMA calculation
- ATR calculation
- Stock order execution
- Protective stop management
- Dynamic trailing-stop management
- Options contract discovery
- Options snapshot retrieval
- Cash-secured put execution
- Covered-call execution
- Wheel state management
- Assignment/call-away reconciliation
- Earnings filtering
- Portfolio risk controls
- Persistent JSON state
- Retry handling and rate-limit handling
- Continuous polling

## Requirements

- Python 3.10+ recommended
- Alpaca trading account
- Alpaca API credentials
- Alpaca paper-trading account for initial testing
- Internet connectivity
- `requests`
- `yfinance`
- `python-dotenv` recommended for local `.env` configuration

## Installation

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it.

### Windows

```powershell
.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install requests yfinance python-dotenv
```

## Configuration

Create a `.env` file in the project root.

**Never commit `.env` or real API credentials to source control.**

Example:

```dotenv
ALPACA_API_KEY=your_paper_api_key
ALPACA_SECRET_KEY=your_paper_secret_key

ALPACA_PAPER=true
LIVE_TRADING=false

STOCK_TICKERS=AAPL,MSFT,NVDA
STOCK_DIRECTION=long
STOCK_MAX_POSITIONS=3

EMA_FAST=20
EMA_SLOW=50
ATR_PERIOD=14
INITIAL_STOP_ATR=2.0
TRAIL_ATR=2.0
TRAIL_ACTIVATION_PROFIT_PCT=2.0

MAX_SINGLE_POSITION_PCT=10
MAX_TOTAL_STOCK_EXPOSURE_PCT=40
MAX_DAILY_DRAWDOWN_PCT=2

MIN_STOCK_PRICE=10
MAX_STOCK_SPREAD_PCT=0.25

WHEEL_TICKERS=SPY
WHEEL_TARGET_DELTA=0.30
WHEEL_MIN_DTE=30
WHEEL_MAX_DTE=45
WHEEL_PROFIT_TARGET_PCT=0.50
WHEEL_MIN_OPEN_INTEREST=500
WHEEL_MAX_OPTION_SPREAD_PCT=8
WHEEL_MIN_OPTION_BID=0.10
WHEEL_MAX_COLLATERAL_PCT=25
WHEEL_EARNINGS_BLACKOUT_DAYS=7

EARNINGS_FILTER=true

POLL_SECONDS=30
STATE_FILE=portfolio_engine_state.json
ORDER_TIMEOUT_SECONDS=60
REQUEST_RETRIES=3
```

## Running the Engine

Run:

```bash
python portfolio_engine.py
```

The engine continuously polls the market and processes configured stock and Wheel symbols.

The polling interval is controlled by:

```dotenv
POLL_SECONDS=30
```

Stop the engine with `Ctrl+C`.

## Stock Strategy

### Entry

The stock strategy uses a daily EMA crossover:

- Fast EMA: 20 periods by default
- Slow EMA: 50 periods by default
- Long signal: fast EMA crosses above slow EMA and price is above the slow EMA
- Short signal: fast EMA crosses below slow EMA and price is below the slow EMA

Direction is controlled by:

```dotenv
STOCK_DIRECTION=long
```

Supported modes are:

```text
long
short
both
```

### Position Sizing

The implementation uses an approximately 1% of equity risk budget per position.

The stop distance is based on:

```text
ATR × INITIAL_STOP_ATR
```

Quantity is constrained by both:

- Risk-based quantity
- Maximum single-position notional

The smaller quantity is used.

### Initial Stop

For a long position:

```text
Initial Stop = Entry Price - (ATR × Initial Stop ATR)
```

For a short position:

```text
Initial Stop = Entry Price + (ATR × Initial Stop ATR)
```

### Trailing Stop

The trailing stop uses:

```text
Current Price ± (ATR × TRAIL_ATR)
```

depending on direction.

The trailing mechanism is deliberately designed to **only tighten** the active stop.

Trailing activation occurs after the configured profit threshold:

```dotenv
TRAIL_ACTIVATION_PROFIT_PCT=2.0
```

## Wheel Strategy

The Wheel strategy operates in two primary phases.

### Phase 1: Cash-Secured Put

The engine searches for option contracts based on:

- Target DTE
- Target delta
- Tradability
- Bid/ask spread
- Open interest
- Minimum bid
- Earnings restrictions

Default target:

```text
30–45 DTE
~0.30 delta
```

The default profit target is 50% of the collected premium.

### Phase 2: Covered Call

When shares are detected following likely put assignment, the state transitions to:

```text
COVERED_CALL
```

The covered call must have a strike above the recorded assignment basis.

The strategy then attempts to buy back the call after reaching the configured 50% premium-profit target.

If the shares are subsequently called away, the strategy returns to:

```text
CASH_PUT
```

## Risk Management

Risk controls are applied before new entries.

### Maximum Single Position

```dotenv
MAX_SINGLE_POSITION_PCT=10
```

Prevents a new stock position from exceeding the configured percentage of account equity.

### Maximum Stock Exposure

```dotenv
MAX_TOTAL_STOCK_EXPOSURE_PCT=40
```

Limits total stock exposure across the configured stock strategy.

### Maximum Stock Positions

```dotenv
STOCK_MAX_POSITIONS=3
```

### Daily Drawdown Halt

```dotenv
MAX_DAILY_DRAWDOWN_PCT=2
```

If the account's daily drawdown reaches the configured threshold, new stock and Wheel entries are halted for that session.

### Liquidity Checks

Stock entries are rejected when:

```dotenv
MAX_STOCK_SPREAD_PCT=0.25
```

is exceeded.

Wheel options also have configurable spread and liquidity requirements.

### Earnings Protection

When enabled:

```dotenv
EARNINGS_FILTER=true
```

the engine uses earnings information to avoid opening affected stock or Wheel positions during the configured blackout period.

If earnings information cannot be verified, the current implementation fails closed and treats the symbol as being in a blackout.

## Persistent State

Strategy state is stored in:

```text
portfolio_engine_state.json
```

The state contains information such as:

- Session date
- Session-start equity
- Stock strategy status
- Entry order IDs
- Stop order IDs
- Entry prices
- Initial stop prices
- Trailing stop prices
- Last ATR
- Last signal
- Wheel phase
- Option symbol
- Option type
- Entry premium
- Assignment basis
- Expected shares
- Last order ID

The state file allows the engine to preserve strategy ownership and resume/reconcile its state across process iterations.

**Do not commit the live state file to source control unless that is intentionally part of the deployment design.**

## Broker Reconciliation

The engine does not rely exclusively on its local state.

Before processing Wheel positions, it checks the broker's current positions and reconciles them with the persisted strategy state.

This is important for detecting situations such as:

```text
Cash-Secured Put
       |
       v
Put disappears + shares appear
       |
       v
Likely Assignment
       |
       v
Covered Call
       |
       v
Call disappears + shares disappear
       |
       v
Return to Cash-Secured Put
```

## Safety Features

The implementation contains several safeguards:

- Paper trading is the default.
- Live trading requires explicit configuration.
- Invalid configuration is rejected.
- EMA fast period must be less than EMA slow period.
- ATR periods and stop multipliers are validated.
- Wheel DTE ranges are validated.
- Wheel target delta is constrained.
- Blocked Alpaca accounts are rejected.
- Market-closed periods are skipped.
- Daily risk halts prevent new entries.
- Existing stock stops are protected from being loosened.
- Covered-call strikes must be above assignment basis.
- Earnings data failures fail closed.
- API rate limiting is handled with backoff.
- API requests use retry logic.
- Order state is persisted.
- Broker state is reconciled before strategy decisions.
- Dry-run orders are supported when live trading is disabled.

## API Integration

The Alpaca client currently handles:

### Trading

- Account
- Clock
- Positions
- Orders
- Order lookup
- Order cancellation
- Order replacement
- Equity orders
- Option orders
- Option assignment activities

### Market Data

- Stock snapshots
- Historical stock bars
- Option contracts
- Option snapshots

The implementation uses Alpaca REST endpoints directly through `requests`.

## Suggested Project Structure

As the project grows, consider separating the current single-file implementation into modules:

```text
alpaca-portfolio-engine/
│
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
│
├── src/
│   ├── main.py
│   ├── config.py
│   ├── alpaca_client.py
│   ├── models.py
│   ├── state_store.py
│   ├── risk_engine.py
│   │
│   ├── strategies/
│   │   ├── stock_strategy.py
│   │   └── wheel_strategy.py
│   │
│   ├── indicators/
│   │   ├── ema.py
│   │   └── atr.py
│   │
│   └── filters/
│       └── earnings.py
│
├── tests/
│   ├── test_config.py
│   ├── test_indicators.py
│   ├── test_risk_engine.py
│   ├── test_stock_strategy.py
│   └── test_wheel_strategy.py
│
└── data/
    └── portfolio_engine_state.json
```

This modular structure is a recommended future organization; it is not the current file layout.

## Testing Strategy

Before enabling live trading, the project should be tested at multiple levels.

### Unit Tests

Test independently:

- EMA calculations
- ATR calculations
- Position sizing
- Stop calculations
- Trailing-stop monotonicity
- Risk limits
- DTE calculations
- Option candidate selection
- Premium profit calculations
- Wheel state transitions
- Assignment detection
- Configuration validation

### Integration Tests

Validate:

- Alpaca authentication
- Account status checks
- Market clock
- Stock data retrieval
- Option data retrieval
- Order submission
- Order replacement
- Order cancellation
- Position reconciliation

### Paper Trading

Run the complete engine against the Alpaca paper account before considering live deployment.

Recommended progression:

```text
Unit Tests
    ↓
API Integration Tests
    ↓
Dry Run
    ↓
Alpaca Paper Trading
    ↓
Extended Paper Observation
    ↓
Small Controlled Live Deployment
```

## Production Considerations

Before using this system with real capital, additional production engineering should be considered.

### Observability

Add:

- Structured logging
- Persistent trade logs
- Order audit trail
- Metrics
- Alerts
- Health checks
- Error notifications

### Reliability

Consider:

- Process supervision
- Automatic restart
- Persistent database state instead of JSON
- Distributed locking / single-instance enforcement
- Recovery after network failure
- Recovery after process restart
- Order reconciliation after unexpected shutdown
- Clock synchronization
- Market-session handling

### Trading Safety

Consider implementing additional controls such as:

- Maximum total portfolio loss
- Maximum option assignment exposure
- Maximum number of Wheel contracts
- Maximum symbol concentration
- Maximum order value
- Maximum order frequency
- Stale-data detection
- Quote-age checks
- Market-wide circuit-breaker handling
- Manual emergency shutdown
- Kill switch
- Independent position reconciliation
- Alerts when local state and broker state disagree

## Secrets and Security

Never place real Alpaca credentials in:

- `README.md`
- Source code
- Git commits
- Screenshots
- Logs
- Docker images
- CI/CD configuration files without secret management

Use environment variables or a proper secrets manager.

Example:

```dotenv
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
```

Add `.env` to `.gitignore`.

## Operational Notes

The engine checks whether the market is open before processing strategies. When the market is closed, it waits for the next polling cycle.

The engine also catches strategy-level exceptions so that a failure for one symbol does not necessarily terminate the entire process.

The main loop continues to run at the configured polling interval.

## Disclaimer

This software is provided for educational and engineering purposes.

Automated trading involves substantial financial risk. Technical indicators, option strategies, stop losses, and risk controls cannot eliminate the possibility of losses, slippage, assignment, gaps, liquidity problems, API failures, or unexpected market behavior.

Use paper trading and extensive testing before considering any live deployment.

## Roadmap

Potential future improvements:

- [ ] Move configuration into a dedicated configuration module
- [ ] Add comprehensive unit tests
- [ ] Add integration tests with mocked Alpaca responses
- [ ] Add structured trade/order audit logs
- [ ] Add persistent database-backed state
- [ ] Add application health monitoring
- [ ] Add alerting
- [ ] Add emergency kill switch
- [ ] Add stronger broker/local-state reconciliation
- [ ] Add automated deployment
- [ ] Add backtesting framework
- [ ] Add performance analytics
- [ ] Add portfolio-level P&L reporting
- [ ] Add configurable strategy enable/disable switches
- [ ] Add stronger option assignment/activity reconciliation
- [ ] Add production-grade secret management

## License

Add the project's license here before publishing the repository.
