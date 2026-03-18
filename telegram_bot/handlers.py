"""
Handlers Module
===============
Handles the main Telegram commands: /allocate, /update, /reset.
These are the commands available AFTER onboarding is complete.

Each handler checks that the user has completed /start first (private key exists
in context.user_data). If not, it tells them to run /start.

Takes: Telegram Update and Context objects.
Returns: nothing (sends messages back to the user).
Why: separates command logic from the onboarding conversation flow.
"""

from telegram import Update
from telegram.ext import ContextTypes
from utils.state_store import load_state, reset_state
from utils.web3_helper import get_web3, get_balance
from utils.formatting import format_usd, format_tvl, format_pool_name, format_pool_row
from modules.market_fetcher import fetch_all_market_data, validate_market_data
from modules.config_manager import filter_pools_by_risk
from modules.dispatcher import run_single_cycle
from modules.scheduler import start_scheduler, stop_scheduler, is_scheduler_running
from telegram_bot.keyboards import settings_keyboard


def _is_onboarded(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check if the user has completed the /start onboarding.
    Takes: context — the bot context containing user_data.
    Returns: True if private key exists in memory, False otherwise.
    Why: every command needs this check — can't do anything without a wallet.
    """
    # The private key is stored in user_data during onboarding
    return "private_key" in context.user_data


async def allocate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /allocate — runs one allocation cycle AND starts the background scheduler.
    First call: runs a single cycle immediately + starts the 15s scheduler.
    Subsequent calls: runs one extra cycle on top of the scheduler.
    Takes: update — the incoming message, context — bot context.
    Returns: nothing (sends a Telegram reply with the result).
    Why: this is how the user kicks off the bot's autonomous operation.
    """
    # Check if the user has completed onboarding
    if not _is_onboarded(context):
        await update.message.reply_text(
            "⚠️ You need to set up first. Send /start to begin."
        )
        return

    # Send a "working" message while the cycle runs
    working_msg = await update.message.reply_text("🔄 Running allocation cycle...")

    # Get a Web3 connection for on-chain operations
    try:
        w3 = get_web3()
    except Exception as e:
        await working_msg.edit_text(f"❌ Cannot connect to BSC: {e}")
        return

    # Run one cycle immediately and get the result
    result = run_single_cycle(w3, context.user_data, context.application)

    # Start the background scheduler if it's not already running
    if not is_scheduler_running():
        start_scheduler(w3, context.user_data, context.application)
        scheduler_msg = "\n\n🕐 Scheduler started — cycles will run every 15 seconds."
    else:
        scheduler_msg = "\n\n🕐 Scheduler is already running."

    # Show the result
    await working_msg.edit_text(
        f"{result}{scheduler_msg}",
        parse_mode="Markdown",
    )


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /update — shows bot status, balance, config, AND live pool data.
    Fetches real pool data from DeFiLlama, filters by risk, and displays top pools.
    Takes: update — the incoming message, context — bot context.
    Returns: nothing (sends a status message with pool rankings).
    Why: the user's main way to check what the bot sees and what pools are available.
    """
    # Check if the user has completed onboarding
    if not _is_onboarded(context):
        await update.message.reply_text(
            "⚠️ You need to set up first. Send /start to begin."
        )
        return

    # Send a "loading" message since data fetching takes a moment
    loading_msg = await update.message.reply_text("🔄 Fetching live data...")

    # Load the persisted state from state.json
    state = load_state()

    # Get the wallet address from memory
    wallet = context.user_data.get("wallet_address", "Unknown")

    # Try to fetch the live BNB balance from the blockchain
    try:
        w3 = get_web3()
        balance = get_balance(w3, wallet)
        balance_text = f"{balance:.4f} BNB"
    except Exception:
        w3 = None
        balance_text = "Unable to fetch (RPC error)"

    # Read config values from state
    config = state.get("user_config", {})
    risk = config.get("risk_profile", "low")
    compound = config.get("compound_enabled", False)
    pnl = state.get("pnl", {})
    position = state.get("current_position", None)
    cycles = state.get("cycle_count", 0)

    # Determine the bot status (active, paused, or safety-locked)
    if state.get("safety_lock", False):
        status = "🔒 SAFETY LOCKED"
    elif state.get("paused", False):
        status = "⏸ Paused"
    else:
        status = "✅ Active"

    # Build the position display
    if position:
        position_text = f"Pool: {position.get('pool', 'Unknown')}"
    else:
        position_text = "No active position"

    # --- Fetch live pool data ---
    pool_section = ""
    try:
        # Get all market data (DeFiLlama pools + Binance prices)
        market_data = fetch_all_market_data(w3)

        # Validate the data before displaying it
        is_valid, reason = validate_market_data(market_data)

        if is_valid:
            # Filter pools by the user's risk level
            all_pools = market_data.get("pools", [])
            filtered = filter_pools_by_risk(all_pools, risk)

            # Sort by APY descending (best returns first)
            filtered.sort(key=lambda p: p.get("apy", 0), reverse=True)

            # Take top 5 pools for display
            top_pools = filtered[:5]

            if top_pools:
                # Build the pool ranking lines
                risk_labels = {"low": "Low", "medium": "Medium", "high": "High"}
                risk_display = risk_labels.get(risk, risk)

                pool_lines = []
                for i, pool in enumerate(top_pools, 1):
                    # Format each pool as a ranked line
                    symbol = pool.get("symbol", "???")
                    apy = pool.get("apy", 0) or 0
                    tvl = pool.get("tvl_usd", 0) or 0
                    pool_risk = pool.get("risk", "?")

                    # Build display line
                    tvl_display = format_tvl(tvl)
                    pool_name = format_pool_name(symbol)
                    pool_lines.append(
                        f"  {i}. {pool_name} | APY: {apy:.1f}% | TVL: {tvl_display}"
                    )

                pool_section = (
                    f"\n📋 *Top Pools ({risk_display} Risk):*\n"
                    + "\n".join(pool_lines)
                    + f"\n  _({len(filtered)} pools available)_"
                )
            else:
                pool_section = "\n📋 *Pools:* No pools match your risk level."
        else:
            # Validation failed — show the reason
            pool_section = f"\n⚠️ *Pool data issue:* {reason}"

    except ConnectionError as e:
        # API was unreachable — show error but don't crash
        pool_section = f"\n⚠️ *Cannot fetch pools:* {str(e)}"
    except Exception as e:
        # Unexpected error — show generic message
        pool_section = f"\n⚠️ *Pool fetch error:* {str(e)}"

    # Build the full status message
    compound_text = "Enabled" if compound else "Disabled"
    risk_cap = risk.capitalize() if risk else "Not set"

    status_text = (
        "📊 *Bot Status*\n"
        "━━━━━━━━━━━━━━━━\n"
        f"📍 Wallet: `{wallet}`\n"
        f"💰 Balance: *{balance_text}*\n"
        "\n"
        f"📈 Position: {position_text}\n"
        "\n"
        f"💵 Cycle PnL: {format_usd(pnl.get('cycle_pnl', 0))}\n"
        f"💵 Total PnL: {format_usd(pnl.get('total_pnl', 0))}\n"
        f"⛽ Gas Spent: {format_usd(pnl.get('total_gas_spent', 0))}\n"
        f"{pool_section}\n"
        "\n"
        f"⚡ Risk: *{risk_cap}*\n"
        f"🔄 Compound: *{compound_text}*\n"
        f"🔁 Cycles: {cycles}\n"
        f"📡 Status: *{status}*"
    )

    # Edit the loading message with the real content + settings buttons
    paused = state.get("paused", False)
    await loading_msg.edit_text(
        status_text,
        parse_mode="Markdown",
        reply_markup=settings_keyboard(compound, paused),
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /reset — wipes all session data and resets state.json to defaults.
    This clears: private key from memory, all PnL, config, safety lock, positions.
    Takes: update — the incoming message, context — bot context.
    Returns: nothing (sends confirmation).
    Why: allows the user to start completely fresh — also clears safety lock.
    """
    # Stop the background scheduler if it's running
    stop_scheduler()

    # Clear the private key and all user data from memory
    context.user_data.clear()

    # Reset state.json to fresh defaults (wipes PnL, config, positions, safety lock)
    reset_state()

    await update.message.reply_text(
        "🔄 *Session reset complete.*\n"
        "\n"
        "• Private key wiped from memory\n"
        "• All settings cleared\n"
        "• PnL history reset\n"
        "• Safety lock cleared\n"
        "\n"
        "Send /start to set up again.",
        parse_mode="Markdown",
    )
