# Project Log

## 2026-03-18 — Session 6: Sprint 5 + Sprint 6 — Analytics, Safety & Hardening

### What was done

**Sprint 5 — PnL, Notifications, Safety Controller:**
- Created `modules/pnl_tracker.py` — portfolio value calculation, cycle/cumulative PnL, gas cost tracking, cycle snapshot recording.
- Created `modules/notifier.py` — Telegram message formatting for rebalance/compound notifications, safety alerts, position summaries. Handles async/sync bridge for background thread messages.
- Created `modules/safety_controller.py` — full fail-safe: emergency swap all tokens to USDT, safety lock, user notification. Error classification (critical vs recoverable).
- Rewrote `modules/dispatcher.py` — now uses pnl_tracker, notifier, and safety_controller. Proper error classification — only critical errors trigger fail-safe, recoverable ones just skip the cycle.

**Sprint 6 — Hardening:**
- `modules/market_fetcher.py` — Added retry logic (3 attempts with backoff), 429 rate limit handling, logging.
- `utils/state_store.py` — Added state.json.bak backup before every write.
- `modules/scheduler.py` — Added consecutive error counter. After 5 failures in a row, auto-triggers fail-safe.
- `telegram_bot/onboarding.py` — Added DM-only check (blocks /start in group chats), re-onboard detection (tells user to /reset first).
- `telegram_bot/bot.py` — Added global error handler (catches unhandled exceptions, sends user-friendly message, logs full traceback). Added logging setup.
- `modules/execution_engine.py` — Added 20% gas buffer on all transactions.

### Why
- Sprint 5 gives the bot accountability (PnL tracking), communication (notifications), and protection (safety controller). Without these, the user has no visibility and no safety net.
- Sprint 6 makes every module resilient to real-world conditions: API failures, rate limits, corrupted state, group chat leaks, rapid consecutive failures.

### Decisions made
- **Emergency swap to USDT** — chosen as the safe-haven token because it's the most liquid stablecoin on BSC.
- **5 consecutive errors = auto fail-safe** — aggressive threshold. Better to lock early and let the user investigate than to keep trying and lose money.
- **DM-only onboarding** — private keys in group chats = instant compromise. Hard block, no exceptions.
- **State backup before every write** — state.json.bak preserves the previous state. If something corrupts the write, the backup survives.
- **Global error handler** — catches everything the individual handlers miss. User gets a friendly message instead of silence.

---

## 2026-03-18 — Session 4: Sprint 4 — Core Loop (The Big One)

### What was done
- Created `modules/comparator.py` — calculates deltas between cycles (APY change, TVL change, price movement), detects anomalies (APR crash >50%, TVL drop >30%, price spike >10%), checks if position is out of range, estimates impermanent loss.
- Created `modules/logic_engine.py` — the decision brain:
  - `score_pool()` — weighted scoring (50% APR, 30% TVL, 20% stability). Caps APR at 200%, log-scales TVL, disqualifies pools under $10K TVL.
  - `make_decision()` — 9-step decision flow: filter by risk → score pools → check out-of-range → compare best vs current → threshold check → compound check → NO_ACTION/REBALANCE/COMPOUND.
  - `build_rebalance_plan()` / `build_compound_plan()` — structured execution plans.
- Created `modules/execution_engine.py` — on-chain transactions:
  - `approve_token()` — ERC-20 approval with allowance check (skip if already approved, approve max uint).
  - `swap_tokens()` — PancakeSwap V3 exactInputSingle via SmartRouter.
  - `add_liquidity()` — mint V3 position NFT with token ordering enforcement.
  - `remove_liquidity()` — decreaseLiquidity on PositionManager.
  - `collect_fees()` — collect earned trading fees.
  - `harvest_cake()` — harvest CAKE from MasterChefV3.
  - `calculate_tick_range()` — risk-based range width (low=wide, high=tight).
  - `_sign_and_send()` — centralised tx signing with nonce tracking and receipt verification.
  - `_check_bnb_reserve()` — prevents gas spending from draining wallet.
- Created `modules/scheduler.py` — 15-second BackgroundScheduler with threading lock, non-blocking cycle skip on overlap, cycle duration warnings.
- Created `modules/dispatcher.py` — 10-step cycle orchestrator: config → lock check → fetch → validate → deltas → decide → execute → PnL → snapshot → notify. Full try/except with safety lock on critical errors. Async bridge for Telegram notifications from background thread.
- Created `config/abi/swap_router.json` and `config/abi/masterchef_v3.json`.
- Updated `telegram_bot/handlers.py`:
  - `/allocate` now runs a real cycle via `run_single_cycle()` and starts the 15s scheduler.
  - `/reset` now stops the scheduler before clearing state.

### Why
- This is the core of the entire system. Before Sprint 4, the bot could only see the market. Now it can think (logic engine) and act (execution engine), running autonomously every 15 seconds.

### Plans / Next steps
- Sprint 5: PnL tracking (`pnl_tracker.py`), notifications (`notifier.py`), safety controller (`safety_controller.py`).
- Sprint 6: Hardening — retries, slippage protection, nonce recovery, state backup, error handlers.

### Decisions made
- **Scoring weights: 50% APR, 30% TVL, 20% stability** — APR is the main driver but TVL and stability prevent chasing unsustainable yields.
- **$10K TVL minimum** — pools below this are instant disqualification. Too thin to safely enter/exit.
- **APR capped at 200% for scoring** — prevents outlier pools with 10,000% APR from dominating (those are usually unsustainable or scams).
- **Tick range width by risk** — low=200 ticks wide, medium=100, high=40. Wider = safer but less efficient.
- **amountOutMinimum = 0 for now** — proper slippage protection deferred to Sprint 6 hardening.
- **Nonce tracking in local cache** — handles rapid sequential transactions within one cycle.
- **Safety lock on critical execution error** — immediate lock, Sprint 5 will add emergency swap to stablecoin.
- **Async bridge for notifications** — scheduler thread uses `asyncio.new_event_loop()` to send Telegram messages from non-async context.

---

## 2026-03-18 — Session 3: Sprint 3 — LP Data Collection & Filtering

### What was done
- Created `utils/formatting.py` — format_usd, format_percent, format_bnb, format_tvl, format_pool_row, format_address, format_pool_name.
- Created `modules/config_manager.py` — loads/saves user config, risk classification (classify_pool_risk), pool filtering by risk level (inclusive downward: high sees low+medium+high).
- Created `modules/market_fetcher.py` — the bot's "eyes":
  - `fetch_defi_llama_pools()` — fetches PancakeSwap V3 pools from DeFiLlama, cached for 5 min.
  - `fetch_token_prices()` — fetches live prices from Binance public API.
  - `fetch_pool_on_chain()` — reads pool state (tick, price, liquidity) directly from BSC.
  - `fetch_position_on_chain()` — reads an existing LP position's details from the PositionManager NFT.
  - `enrich_pools_with_risk()` — classifies each pool's risk from its token pair.
  - `validate_market_data()` — validates all fields before they reach the logic engine.
- Created ABIs: `factory_v3.json`, `pool_v3.json`, `nonfungible_position_manager.json`.
- Updated `telegram_bot/handlers.py` — `/update` now fetches live pool data, filters by risk, and shows top 5 pools ranked by APY with TVL. Includes settings buttons.

### Why
- Sprint 3 gives the bot real data to work with. Before this, it was a Telegram bot that asked questions but couldn't see the market. Now `/update` shows actual PancakeSwap V3 pools with live APY and TVL.
- Risk classification + filtering ensures users only see pools matching their tolerance.
- Validation prevents bad data from ever reaching the decision engine (Sprint 4).

### Plans / Next steps
- Sprint 4: The big one — comparator, logic engine, execution engine, scheduler, dispatcher. The bot will make real decisions and execute on-chain.

### Decisions made
- **DeFiLlama cached for 5 min** — their data updates hourly, so fetching every 15s wastes bandwidth. On-chain data (tick/price) will still be read every cycle in Sprint 4.
- **Risk filtering is inclusive downward** — a "medium" user sees low AND medium pools. Gives them the full safe range up to their tolerance.
- **Extreme risk pools filtered out entirely** — unknown or small-cap token pairs are too risky for automated management.
- **Binance public API for prices** — free, no key, fast. Stablecoins hardcoded to $1.0.
- **/update shows top 5 pools** — enough to be useful without cluttering the Telegram message.

---

## 2026-03-18 — Session 2: Sprint 1 Implementation

### What was done
- Completed Sprint 1: Wallet Import & Key Validation.
- Created full project scaffold: `config/`, `modules/`, `telegram_bot/`, `utils/`, `tests/` directories with `__init__.py` files.
- Created `.gitignore` (secrets, pycache, venv, IDE, OS files).
- Created `.env.example` template and local `.env` with dev token.
- Created `requirements.txt` (python-telegram-bot, web3, python-dotenv, APScheduler, requests).
- Created `config/settings.py` — loads .env, defines all constants, contract addresses, token addresses, risk classification sets.
- Created `config/abi/erc20.json` — standard ERC-20 ABI.
- Created `utils/validation.py` — private key validation, address validation, input sanitisation.
- Created `utils/web3_helper.py` — Web3 connection, BNB balance, token balance, ABI loading.
- Created `utils/state_store.py` — atomic read/write of state.json with thread locking, default state schema.
- Created `telegram_bot/keyboards.py` — risk, compound, confirm, and settings inline button layouts.
- Created `telegram_bot/onboarding.py` — full /start ConversationHandler: key → risk → compound → confirm.
- Created `telegram_bot/handlers.py` — /allocate (placeholder), /update (live status), /reset (wipe session).
- Created `telegram_bot/callbacks.py` — handles post-onboarding button presses (risk change, compound toggle, pause, safety clear).
- Created `telegram_bot/bot.py` — Application builder, handler registration.
- Created `main.py` — entry point, runs the bot.
- Updated `CLAUDE.md` — removed Firestore/GCP/email references, added actual file paths, updated to match real architecture.
- Updated `README.md` — removed GCP requirements, updated commands (/start not /import), updated structure and dependencies.

### Why
- Sprint 1 is the foundation — everything in later sprints builds on the Telegram bot, validation, state store, and web3 connection.
- Removed Firestore/GCP/email from design — replaced with .env + state.json for simplicity and portability (anyone can clone and run without cloud accounts).
- Private key collected via Telegram (not .env) for security — held in memory only, message deleted immediately.

### Plans / Next steps
- Test Sprint 1: install deps, run `python main.py`, go through /start onboarding in Telegram.
- Sprint 2: Wire up /update with real data, add `utils/formatting.py`, polish command responses.
- Sprint 3: `modules/market_fetcher.py` + `modules/config_manager.py` — live pool data from DeFiLlama + Binance + on-chain.

### Decisions made
- **No Firestore/GCP** — .env for config, state.json for persistent state. Zero cloud dependencies.
- **No pandas, no email** — removed as unnecessary. Fewer dependencies = simpler setup.
- **/start replaces /import** — standard Telegram convention, handles full onboarding flow.
- **Private key in memory only** — entered via Telegram, message auto-deleted, never saved to disk.
- **PancakeSwap V3 mainnet contracts** — V3 lacks reliable testnet deployment. Built against mainnet addresses, user can point RPC to testnet.
- **Thread-safe state.json** — uses threading.Lock + atomic writes (temp file + rename) to prevent corruption from scheduler + Telegram threads.
- **DeFiLlama + Binance public API** for price/pool data — both free, no API keys needed.

---

## 2026-03-18 — Session 1: Project Setup

### What was done
- Read and analysed the full NEA analysis and design document (~830 lines).
- Created initial `CLAUDE.md` and `README.md`.
- Created `PROJECT_LOG.md`.

### Why
- Project going public on GitHub, needed professional documentation.
- `CLAUDE.md` serves as quick-reference for future sessions.

### Decisions made
- README has no academic/coursework references.
- Project structure follows the 10-module architecture from the design doc.
