"""
Keyboards Module
================
Builds the inline button layouts that appear in Telegram messages.
Each function returns an InlineKeyboardMarkup — a grid of clickable buttons
that sit below a message. When a user taps a button, Telegram sends us a
callback with the button's data string, which we handle in callbacks.py.

Takes: nothing (pure layout builders).
Returns: InlineKeyboardMarkup objects.
Why: keeps button layout logic separate from handler logic — easier to update the UI.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def risk_keyboard() -> InlineKeyboardMarkup:
    """
    Build the risk level selection keyboard.
    Shows three buttons in a row: Low, Medium, High.
    Returns: InlineKeyboardMarkup with risk options.
    Why: used during onboarding when we ask the user to pick their risk tolerance.
    """
    # Each button has display text and a callback_data string
    # The callback_data is what we receive when the user taps it
    buttons = [
        [
            InlineKeyboardButton("🟢 Low", callback_data="risk_low"),
            InlineKeyboardButton("🟡 Medium", callback_data="risk_medium"),
            InlineKeyboardButton("🔴 High", callback_data="risk_high"),
        ]
    ]

    return InlineKeyboardMarkup(buttons)


def compound_keyboard() -> InlineKeyboardMarkup:
    """
    Build the compounding preference keyboard.
    Shows two buttons: Enable and Disable.
    Returns: InlineKeyboardMarkup with compound options.
    Why: used during onboarding to ask if the user wants auto-compounding.
    """
    buttons = [
        [
            InlineKeyboardButton("✅ Enable", callback_data="compound_on"),
            InlineKeyboardButton("❌ Disable", callback_data="compound_off"),
        ]
    ]

    return InlineKeyboardMarkup(buttons)


def confirm_keyboard() -> InlineKeyboardMarkup:
    """
    Build the confirmation keyboard for the end of onboarding.
    Shows Confirm and Cancel buttons.
    Returns: InlineKeyboardMarkup with confirm/cancel.
    Why: gives the user a chance to review their settings before the bot starts.
    """
    buttons = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_setup"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_setup"),
        ]
    ]

    return InlineKeyboardMarkup(buttons)


def settings_keyboard(compound_enabled: bool, paused: bool) -> InlineKeyboardMarkup:
    """
    Build the full settings panel keyboard.
    Shows buttons for risk change, compound toggle, pause/resume, and safety reset.
    Takes: compound_enabled (bool) — current compound state, paused (bool) — current pause state.
    Returns: InlineKeyboardMarkup with all settings options.
    Why: shown after onboarding and with /update so the user can adjust on the fly.
    """
    # Compound button text changes based on current state
    compound_text = "⏸ Disable Compound" if compound_enabled else "▶️ Enable Compound"
    compound_data = "compound_off" if compound_enabled else "compound_on"

    # Pause button text changes based on current state
    pause_text = "▶️ Resume Bot" if paused else "⏸ Pause Bot"
    pause_data = "resume_bot" if paused else "pause_bot"

    buttons = [
        # Row 1: Risk level options
        [
            InlineKeyboardButton("🟢 Low", callback_data="risk_low"),
            InlineKeyboardButton("🟡 Medium", callback_data="risk_medium"),
            InlineKeyboardButton("🔴 High", callback_data="risk_high"),
        ],
        # Row 2: Compound toggle and pause toggle
        [
            InlineKeyboardButton(compound_text, callback_data=compound_data),
            InlineKeyboardButton(pause_text, callback_data=pause_data),
        ],
        # Row 3: Safety reset (only matters after a fail-safe event)
        [
            InlineKeyboardButton("🔓 Clear Safety Lock", callback_data="clear_safety_lock"),
        ],
    ]

    return InlineKeyboardMarkup(buttons)
