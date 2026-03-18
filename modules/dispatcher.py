"""
Dispatcher Module (Cycle Orchestrator)
=======================================
Orchestrates one complete execution cycle by calling modules in strict order:

  1. Load config  →  2. Check locks  →  3. Fetch market data  →  4. Validate
  5. Calculate deltas  →  6. Make decision  →  7. Execute plan
  8. Track PnL  →  9. Save snapshot  →  10. Notify user

Think of this like an assembly line foreman: each worker (module) does one job,
and the dispatcher makes sure they run in the right order and passes the
output of each worker to the next one.

The ENTIRE function is wrapped in a try/except. If anything goes wrong,
control passes to the safety controller which protects the user's capital.

Takes: Web3 connection, user_data dict (private key), bot application.
Returns: nothing (side effects: on-chain transactions, state updates, Telegram messages).
Why: having one central orchestrator means the execution order is explicit,
debuggable, and testable. No module needs to know what runs before or after it.
"""

import time
import logging
from web3 import Web3

from modules.config_manager import (
    load_user_config, is_safety_locked, is_paused,
)
from modules.market_fetcher import fetch_all_market_data, validate_market_data
from modules.comparator import calculate_deltas
from modules.logic_engine import make_decision
from modules.execution_engine import execute_plan
from modules.pnl_tracker import update_pnl, record_cycle_snapshot
from modules.notifier import send_cycle_update
from modules.safety_controller import trigger_failsafe, is_critical_error
from utils.state_store import load_state, save_state

logger = logging.getLogger(__name__)


def run_cycle(w3: Web3, user_data: dict, bot_app) -> None:
    """
    Execute one complete cycle of the bot's main loop.
    Takes: w3 — blockchain connection, user_data — dict with private_key/wallet,
           bot_app — Telegram Application (for sending notifications).
    Returns: nothing.
    Why: called every 15 seconds by the scheduler. This is THE core function.
    """

    # ================================================================
    # STEP 1: Load user config from state.json
    # ================================================================
    user_config = load_user_config()

    # Make sure we have the essential config values
    if not user_config.get("risk_profile"):
        logger.debug("No risk profile set — skipping cycle")
        return

    # ================================================================
    # STEP 2: Check if the bot is locked or paused
    # ================================================================
    # Safety lock = critical error happened, bot is frozen
    if is_safety_locked():
        logger.debug("Safety lock active — skipping cycle")
        return

    # Paused = user voluntarily paused the bot
    if is_paused():
        logger.debug("Bot is paused — skipping cycle")
        return

    # Make sure we have a private key in memory
    private_key = user_data.get("private_key")
    if not private_key:
        logger.debug("No private key in memory — skipping cycle")
        return

    # Get the wallet address for balance checks
    wallet_address = user_data.get("wallet_address", "")

    # ================================================================
    # STEP 3: Fetch market data from all sources
    # ================================================================
    try:
        market_data = fetch_all_market_data(w3)
    except ConnectionError as e:
        logger.error(f"Market data fetch failed: {e}")
        # Connection errors are recoverable — skip this cycle, try next time
        return
    except Exception as e:
        logger.error(f"Unexpected fetch error: {e}")
        # Check if this is a critical error or recoverable
        if is_critical_error(e):
            trigger_failsafe(e, private_key, w3, user_data, bot_app)
        return

    # ================================================================
    # STEP 4: Validate the market data
    # ================================================================
    is_valid, reason = validate_market_data(market_data)
    if not is_valid:
        logger.warning(f"Market data validation failed: {reason}")
        # Bad data = skip this cycle. Don't make decisions on garbage.
        return

    # ================================================================
    # STEP 5: Load previous cycle snapshot and calculate deltas
    # ================================================================
    state = load_state()
    previous_snapshot = state.get("previous_cycle")

    # Calculate what changed since last cycle
    deltas = calculate_deltas(market_data, previous_snapshot)

    # ================================================================
    # STEP 6: Make a decision (the brain)
    # ================================================================
    current_position = state.get("current_position")

    decision, plan = make_decision(
        current_data=market_data,
        deltas=deltas,
        user_config=user_config,
        current_position=current_position,
    )

    logger.info(f"Decision: {decision}")

    # ================================================================
    # STEP 7: Execute the plan (if action is needed)
    # ================================================================
    execution_result = None

    if decision in ("REBALANCE", "COMPOUND"):
        try:
            execution_result = execute_plan(plan, private_key, w3)

            # Update the current position in state if rebalance succeeded
            if decision == "REBALANCE" and execution_result.get("success"):
                target = plan.get("target_pool", {})
                state["current_position"] = {
                    "pool_id": target.get("pool_id", ""),
                    "pool": target.get("symbol", "Unknown"),
                    "apy": target.get("apy", 0),
                    "risk": target.get("risk", ""),
                    "entered_at": time.time(),
                    "entry_prices": market_data.get("prices", {}),
                }

        except Exception as e:
            # Execution failure — check severity
            logger.error(f"Execution failed: {e}")
            if is_critical_error(e):
                # Critical: trigger full fail-safe (emergency swap + lock + notify)
                trigger_failsafe(e, private_key, w3, user_data, bot_app)
            return

    # ================================================================
    # STEP 8: Update PnL tracking
    # ================================================================
    if execution_result:
        try:
            pnl = update_pnl(execution_result, market_data, w3, wallet_address)
        except Exception as e:
            logger.warning(f"PnL tracking failed (non-critical): {e}")
            pnl = state.get("pnl", {})
    else:
        pnl = state.get("pnl", {})

    # Increment the cycle counter
    state["cycle_count"] = state.get("cycle_count", 0) + 1

    # ================================================================
    # STEP 9: Save the current data as previous cycle snapshot
    # ================================================================
    record_cycle_snapshot(market_data, state)

    # Save all state changes to disk
    save_state(state)

    # ================================================================
    # STEP 10: Notify the user (if action was taken)
    # ================================================================
    chat_id = user_data.get("chat_id")
    if decision != "NO_ACTION" and chat_id:
        try:
            send_cycle_update(bot_app, chat_id, decision, plan, pnl)
        except Exception as e:
            # Notification failure is never critical — just log it
            logger.warning(f"Notification failed: {e}")


def run_single_cycle(w3: Web3, user_data: dict, bot_app) -> str:
    """
    Run one cycle manually (triggered by /allocate command).
    Same as run_cycle but returns a status string for the Telegram response.
    Takes: w3, user_data, bot_app — same as run_cycle.
    Returns: status string describing what happened.
    Why: /allocate needs to give the user immediate feedback, unlike the
    background scheduler which runs silently.
    """
    try:
        run_cycle(w3, user_data, bot_app)

        # Check what happened by reading the updated state
        state = load_state()
        cycles = state.get("cycle_count", 0)
        position = state.get("current_position")

        # Check if safety lock was triggered during the cycle
        if state.get("safety_lock"):
            return "🚨 Cycle triggered safety lock. Check alerts."

        if position:
            return (
                f"✅ Cycle #{cycles} complete.\n"
                f"Position: {position.get('pool', 'Unknown')}\n"
                f"APY: {position.get('apy', 0):.1f}%"
            )
        else:
            return f"✅ Cycle #{cycles} complete. No action taken."

    except Exception as e:
        return f"❌ Cycle failed: {str(e)}"
