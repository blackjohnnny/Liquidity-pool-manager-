"""
Callbacks Module
================
Handles inline button presses that happen OUTSIDE of the onboarding conversation.
These are the settings buttons (risk change, compound toggle, pause/resume, safety reset)
that appear in the settings panel after onboarding is complete.

The onboarding flow has its OWN callback handlers inside onboarding.py — this module
only handles buttons pressed from the settings keyboard or status messages.

Takes: Telegram Update (with callback_query) and Context objects.
Returns: nothing (sends confirmation messages back to the user).
Why: separates post-onboarding button logic from the onboarding conversation state machine.
"""

from telegram import Update
from telegram.ext import ContextTypes
from utils.state_store import load_state, save_state


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Master router for all inline button presses outside of onboarding.
    Reads the callback_data string and delegates to the right action.
    Takes: update — the callback query event, context — bot context.
    Returns: nothing.
    Why: python-telegram-bot sends ALL button presses to registered callback handlers,
    so we need one function that routes based on the button's data string.
    """
    # Get the callback query object (contains the button's data string)
    query = update.callback_query

    # Acknowledge the button press immediately (stops the loading spinner)
    await query.answer()

    # Get the data string we assigned to this button in keyboards.py
    data = query.data

    # Route to the correct handler based on the button's data string
    if data.startswith("risk_"):
        await _handle_risk_change(query, data)

    elif data in ("compound_on", "compound_off"):
        await _handle_compound_toggle(query, data)

    elif data in ("pause_bot", "resume_bot"):
        await _handle_pause_toggle(query, data)

    elif data == "clear_safety_lock":
        await _handle_safety_clear(query)


async def _handle_risk_change(query, data: str) -> None:
    """
    Handle a risk level change button press.
    Updates state.json with the new risk profile.
    Takes: query — the callback query, data — the button's callback data.
    Returns: nothing.
    Why: allows the user to change risk on the fly without re-doing /start.
    """
    # Extract the risk level from callback data (e.g. "risk_medium" -> "medium")
    risk_level = data.replace("risk_", "")

    # Load current state, update the risk profile, save back
    state = load_state()
    state["user_config"]["risk_profile"] = risk_level
    save_state(state)

    # Map to display labels
    risk_labels = {"low": "🟢 Low", "medium": "🟡 Medium", "high": "🔴 High"}
    label = risk_labels.get(risk_level, risk_level)

    # Confirm the change to the user
    await query.edit_message_text(
        f"⚡ Risk level changed to *{label}*.\n"
        "_Next cycle will use the new filters._",
        parse_mode="Markdown",
    )


async def _handle_compound_toggle(query, data: str) -> None:
    """
    Handle a compound enable/disable button press.
    Updates state.json with the new compounding preference.
    Takes: query — the callback query, data — "compound_on" or "compound_off".
    Returns: nothing.
    Why: lets the user toggle compounding without restarting the bot.
    """
    # Determine the new state from the button data
    compound_enabled = data == "compound_on"

    # Load state, update, save
    state = load_state()
    state["user_config"]["compound_enabled"] = compound_enabled
    save_state(state)

    # Confirm with appropriate message
    status = "✅ Enabled" if compound_enabled else "❌ Disabled"
    await query.edit_message_text(
        f"🔄 Auto-compounding: *{status}*",
        parse_mode="Markdown",
    )


async def _handle_pause_toggle(query, data: str) -> None:
    """
    Handle the pause/resume button press.
    Updates the paused flag in state.json.
    Takes: query — the callback query, data — "pause_bot" or "resume_bot".
    Returns: nothing.
    Why: lets the user temporarily stop the bot without wiping their session.
    """
    # Determine if we're pausing or resuming
    paused = data == "pause_bot"

    # Load state, update, save
    state = load_state()
    state["paused"] = paused
    save_state(state)

    # Confirm with the right message
    if paused:
        await query.edit_message_text("⏸ *Bot paused.* No cycles will run.", parse_mode="Markdown")
    else:
        await query.edit_message_text("▶️ *Bot resumed.* Cycles will continue.", parse_mode="Markdown")


async def _handle_safety_clear(query) -> None:
    """
    Handle the safety lock clear button press.
    Resets safety_lock to False in state.json so the bot can run again.
    Takes: query — the callback query.
    Returns: nothing.
    Why: after a fail-safe event locks the bot, the user must manually unlock it.
    """
    # Load state and check if safety lock is actually set
    state = load_state()

    if not state.get("safety_lock", False):
        # Safety lock isn't set — nothing to clear
        await query.edit_message_text("ℹ️ Safety lock is not active.")
        return

    # Clear the safety lock
    state["safety_lock"] = False
    save_state(state)

    # Confirm to the user
    await query.edit_message_text(
        "🔓 *Safety lock cleared.*\n"
        "The bot can now resume normal operation.",
        parse_mode="Markdown",
    )
