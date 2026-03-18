"""
Notification Layer Module
==========================
Handles all outbound communication to the user via Telegram.
Formats status updates, action notifications, and critical alerts
into clean, readable messages.

Every message the bot sends to the user (outside of onboarding) goes
through this module. This keeps message formatting consistent and
makes it easy to change the output style in one place.

Takes: bot application, chat_id, and data to display.
Returns: nothing (sends Telegram messages as a side effect).
Why: separates "what to say" from "when to say it" — the dispatcher
decides when, this module decides how to format it.
"""

import asyncio
import logging
from utils.formatting import format_usd, format_tvl, format_pool_name, format_percent

logger = logging.getLogger(__name__)


def send_cycle_update(bot_app, chat_id: int, decision: str, plan: dict, pnl: dict) -> None:
    """
    Send a notification after a cycle that took action.
    Takes: bot_app — Telegram application, chat_id — user's chat,
           decision — what was decided, plan — the plan that was executed,
           pnl — current PnL numbers.
    Returns: nothing.
    Why: the user should know whenever the bot moves their money.
    """
    # Don't send notifications for NO_ACTION cycles (too noisy)
    if decision == "NO_ACTION":
        return

    # Build the message based on what happened
    if decision == "REBALANCE":
        target = plan.get("target_pool", {})
        reason = plan.get("reason", "Better opportunity found")

        msg = (
            "🔄 *Rebalanced!*\n"
            "━━━━━━━━━━━━━━━━\n"
            f"New pool: *{format_pool_name(target.get('symbol', 'Unknown'))}*\n"
            f"APY: *{target.get('apy', 0):.1f}%*\n"
            f"TVL: {format_tvl(target.get('tvl_usd', 0))}\n"
            f"Risk: {target.get('risk', 'unknown').capitalize()}\n"
            f"\nReason: _{reason}_\n"
            f"\n💵 Total PnL: {format_usd(pnl.get('total_pnl', 0))}\n"
            f"⛽ Total Gas: {format_usd(pnl.get('total_gas_spent', 0))}"
        )

    elif decision == "COMPOUND":
        msg = (
            "🔄 *Compounded!*\n"
            "━━━━━━━━━━━━━━━━\n"
            "Fees and rewards reinvested into position.\n"
            f"\n💵 Total PnL: {format_usd(pnl.get('total_pnl', 0))}\n"
            f"⛽ Total Gas: {format_usd(pnl.get('total_gas_spent', 0))}"
        )
    else:
        return

    # Check for anomalies to append as warnings
    anomalies = plan.get("anomalies", [])
    if anomalies:
        anomaly_text = "\n".join(f"  ⚠️ {a}" for a in anomalies[:3])
        msg += f"\n\n*Warnings:*\n{anomaly_text}"

    # Send the message
    _send_message(bot_app, chat_id, msg)


def send_safety_alert(bot_app, chat_id: int, error_details: str) -> None:
    """
    Send a critical safety alert when the fail-safe activates.
    Takes: bot_app, chat_id, error_details — description of what went wrong.
    Returns: nothing.
    Why: the user MUST be told when their money is at risk. This is the
    highest-priority notification the bot can send.
    """
    msg = (
        "🚨 *CRITICAL — SAFETY LOCK ACTIVATED*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "The bot has been *locked* due to a critical error.\n"
        "All processing has stopped to protect your funds.\n"
        "\n"
        f"*Error:* `{error_details}`\n"
        "\n"
        "*What happened:*\n"
        "• Active positions were converted to stablecoin (if possible)\n"
        "• No further cycles will run until you unlock\n"
        "\n"
        "*To resume:*\n"
        "• Use the 🔓 Clear Safety Lock button\n"
        "• Or send /reset to start fresh\n"
        "\n"
        "⚠️ _Review your wallet before resuming._"
    )

    _send_message(bot_app, chat_id, msg)


def send_position_summary(
    bot_app, chat_id: int, position: dict, pnl: dict, top_pools: list
) -> None:
    """
    Send a detailed position summary (used by /update in future).
    Takes: bot_app, chat_id, position — current position data,
           pnl — PnL numbers, top_pools — best available pools.
    Returns: nothing.
    Why: gives the user a full picture of where their money is and what else is available.
    """
    # Build position section
    if position:
        pos_text = (
            f"📈 *Active Position*\n"
            f"Pool: *{position.get('pool', 'Unknown')}*\n"
            f"APY: {position.get('apy', 0):.1f}%\n"
            f"Risk: {position.get('risk', '?').capitalize()}"
        )
    else:
        pos_text = "📈 *No active position*"

    # Build PnL section
    pnl_text = (
        f"\n💵 Cycle PnL: {format_usd(pnl.get('cycle_pnl', 0))}\n"
        f"💵 Total PnL: {format_usd(pnl.get('total_pnl', 0))}\n"
        f"⛽ Gas Spent: {format_usd(pnl.get('total_gas_spent', 0))}"
    )

    # Build top pools section
    if top_pools:
        pool_lines = []
        for i, pool in enumerate(top_pools[:5], 1):
            name = format_pool_name(pool.get("symbol", "?"))
            apy = pool.get("apy", 0) or 0
            tvl = format_tvl(pool.get("tvl_usd", 0))
            pool_lines.append(f"  {i}. {name} | APY: {apy:.1f}% | TVL: {tvl}")
        pools_text = "\n📋 *Top Pools:*\n" + "\n".join(pool_lines)
    else:
        pools_text = "\n📋 No pools available"

    msg = f"{pos_text}\n{pnl_text}\n{pools_text}"
    _send_message(bot_app, chat_id, msg)


def send_info(bot_app, chat_id: int, message: str) -> None:
    """
    Send a simple informational message.
    Takes: bot_app, chat_id, message — the text to send.
    Returns: nothing.
    Why: generic helper for one-off status messages that don't fit other categories.
    """
    _send_message(bot_app, chat_id, f"ℹ️ {message}")


def _send_message(bot_app, chat_id: int, text: str) -> None:
    """
    Bridge between sync code (scheduler thread) and async Telegram API.
    Takes: bot_app — the Application, chat_id — who to send to, text — message.
    Returns: nothing.
    Why: the scheduler and dispatcher run in a background thread (sync),
    but python-telegram-bot's send_message is async. This function creates
    a temporary event loop to bridge the gap.
    """
    if not bot_app or not chat_id:
        logger.warning("Cannot send message — missing bot_app or chat_id")
        return

    try:
        # Try to get an existing event loop (works if we're in an async context)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop closed")
        except RuntimeError:
            # No loop exists in this thread — create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Define the async send function
        async def _do_send():
            await bot_app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )

        # Run it in the loop
        loop.run_until_complete(_do_send())

    except Exception as e:
        # Never crash on a notification failure — log and move on
        logger.error(f"Failed to send Telegram message: {e}")
