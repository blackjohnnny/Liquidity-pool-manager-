# Liquidity Pool Manager — Project Context

## What This Project Is
An automated bot that manages PancakeSwap V3 concentrated liquidity positions on BSC. Controlled entirely via Telegram. Runs a 15-second cycle that monitors pools, makes risk-based decisions, and auto-rebalances/compounds.

**Target chain:** Binance Smart Chain (BSC), PancakeSwap V3.
**Language:** Python 3.10+
**Interface:** Telegram bot (no web UI).
**Storage:** `.env` for config, `state.json` for runtime state. No database, no cloud.

---

## Architecture — 3 Layers

### 1. Control & Logic Layer
- **Scheduler & Cycle Controller** — 15-second loop, enforces safetyLock, prevents overlapping cycles.
- **Dispatcher** — Orchestrates module execution in fixed order: config → fetch → compare → decide → execute → PnL → notify.
- **Logic Engine** — Risk-filtered, threshold-based decision making. Outputs: NO_ACTION, REBALANCE, or COMPOUND.
- **Execution Engine** — On-chain operations: token approvals, LP removal, swaps, LP add, staking. Verifies receipts.
- **Safety Controller** — Monitors for critical errors. On failure: halt → swap to stablecoin → safetyLock=true → notify → exit.

### 2. Data & Security Layer
- **Config Manager** — Loads settings from `.env` and `state.json`. Private key collected via Telegram, held in memory only.
- **Market Data Fetcher** — DeFiLlama pools API, Binance public price API, on-chain web3 reads. Full validation.
- **Delta & State Comparator** — Compares current vs previous cycle: APR change, TVL volatility, price movement. Detects anomalies.
- **PnL & State Analytics** — Tracks cycle/cumulative PnL, gas usage, liquidity changes. Persists to `state.json`.

### 3. Interface Layer
- **Telegram Bot** — Status display (balance, active LP, PnL, system state), inline buttons (compounding toggle, risk profile, pause/resume, reset), notifications.

---

## 10 Core Modules
1. Scheduler & Cycle Controller (`modules/scheduler.py`)
2. Dispatcher (`modules/dispatcher.py`)
3. Safety Controller (`modules/safety_controller.py`)
4. Config Manager (`modules/config_manager.py`)
5. Market Data Fetcher (`modules/market_fetcher.py`)
6. Delta & State Comparator (`modules/comparator.py`)
7. Logic Engine (`modules/logic_engine.py`)
8. Execution Engine (`modules/execution_engine.py`)
9. PnL & State Analytics (`modules/pnl_tracker.py`)
10. Notification Layer (`modules/notifier.py`)

---

## Key Algorithms
1. **Scheduler & Cycle Control** — 15s loop, safetyLock check, timestamp, dispatch, sleep.
2. **Market Data Fetching & Validation** — Fetch from DeFiLlama + Binance + on-chain, validate all fields, reject cycle on any critical failure.
3. **Decision-Making & Rebalancing** — Calculate deltas from previous cycle, filter pools by riskProfile, score remaining pools (APR, TVL, stability), rebalance if best pool exceeds threshold vs current position, else compound if enabled.
4. **Fail-Safe & Error Handling** — On critical error: halt all → emergency swap to stablecoin → safetyLock=true → log → notify → exit.

---

## Risk Classification
- **Low:** stablecoin ↔ stablecoin
- **Medium:** stablecoin ↔ large-cap
- **High:** large-cap ↔ large-cap
- **Extreme:** low-cap ↔ low-cap (filtered out)

---

## Key Variables
| Variable | Type | Purpose | Storage |
|---|---|---|---|
| user_config | Dict | User prefs (risk, compounding) | state.json |
| risk_profile | String | "low" / "medium" / "high" | state.json |
| compound_enabled | Boolean | Compounding toggle | state.json |
| safety_lock | Boolean | Blocks execution after fail-safe | state.json |
| private_key | String | Wallet key — NEVER saved to disk | Memory only |
| wallet_address | String | Public wallet address | state.json |
| previous_cycle | Object | Last cycle snapshot | state.json |
| current_position | Object | Active LP position details | state.json |
| pnl | Object | cycle_pnl, total_pnl, total_gas_spent | state.json |
| cycle_count | Int | Total completed cycles | state.json |

---

## Tech Stack
- **Python 3.10+** — Core language
- **python-telegram-bot 21.7** — Telegram interface
- **web3.py 7.6** — BSC blockchain interaction
- **APScheduler 3.11** — 15-second cycle timing
- **python-dotenv** — .env loading
- **requests** — HTTP API calls
- **External APIs:** DeFiLlama (free, no key), Binance public API (free, no key), BSC public RPC

---

## PancakeSwap V3 Contracts (BSC Mainnet)
- Factory: `0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865`
- PositionManager: `0x46A15B0b27311cedF172AB29E4f4766fbE7F4364`
- SmartRouter: `0x13f4EA83D0bd40E75C8222255bc855a974568Dd4`
- MasterChefV3: `0x556B9306565093C855AEA9AE92A594704c2Cd59e`

---

## Telegram Commands
- `/start` — Onboarding: private key → risk level → compounding → confirm
- `/allocate` — Trigger fund allocation
- `/update` — View current status
- `/reset` — Wipe session / clear safetyLock
- Inline buttons: compounding on/off, risk level, pause/resume, safety lock clear

---

## Success Criteria (Essential)
1. User can import wallet with private key; bot can sign transactions.
2. Bot fetches and displays live LP data (APR, token pair, TVL).
3. User selects risk level; bot filters LPs accordingly.
4. Bot auto-allocates funds based on risk + APR logic, displays result in Telegram.
5. All Telegram commands work reliably.

---

## Development Methodology
Agile — 6 sprints:
1. Wallet import & key validation (CURRENT)
2. Telegram command handling
3. LP data collection & filtering
4. Allocation & auto-compound logic
5. PnL tracking & safety controller
6. Final testing, UX polish, error handling

---

## Hosting Requirements
- Any machine with Python 3.10+ and internet
- 1 CPU / 1GB RAM minimum
- Can run locally, on a VPS, or Raspberry Pi
