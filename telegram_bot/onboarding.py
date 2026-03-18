"""
Onboarding Module
=================
Handles the /start command and walks the user through setup step by step:
  1. Ask for private key
  2. Validate key and show wallet address + BNB balance
  3. Ask for risk level (Low / Medium / High)
  4. Ask for compounding preference (Enable / Disable)
  5. Show summary and ask for confirmation

Uses python-telegram-bot's ConversationHandler — a state machine that tracks
where each user is in the multi-step flow. Think of it like a form wizard:
the user can only move forward by providing valid input at each step.

Takes: Telegram Update and Context objects (from the bot framework).
Returns: conversation state constants (tells the framework what step comes next).
Why: this is the user's first interaction — it must be bulletproof and clear.
"""

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from utils.validation import validate_private_key, sanitize_key_input
from utils.web3_helper import get_web3, get_balance
from utils.state_store import load_state, save_state, get_default_state
from telegram_bot.keyboards import risk_keyboard, compound_keyboard, confirm_keyboard

# --- Conversation states ---
# These constants define each step in the onboarding flow.
# The ConversationHandler uses them to know which handler to call next.
ASK_KEY = 0       # Waiting for the user to send their private key
ASK_RISK = 1      # Waiting for the user to pick a risk level
ASK_COMPOUND = 2  # Waiting for the user to toggle compounding
CONFIRM = 3       # Waiting for the user to confirm or cancel


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point — triggered when the user sends /start.
    Sends a welcome message and asks for the private key.
    Takes: update — the incoming message, context — bot context.
    Returns: ASK_KEY state (tells the framework to wait for a key next).
    Why: this kicks off the entire onboarding conversation.
    """
    # Security check: only allow onboarding in private DMs, not group chats
    # Private keys should never be sent in a group where others can see them
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "⚠️ For security, please message me in a *private chat*, not a group.\n"
            "Your private key must stay confidential.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Check if user is already onboarded and ask if they want to re-onboard
    if "private_key" in context.user_data:
        wallet = context.user_data.get("wallet_address", "Unknown")
        await update.message.reply_text(
            f"You're already set up with wallet `{wallet}`.\n\n"
            "Send /reset first if you want to start over, "
            "or use /update to check your status.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Welcome message explaining what the bot does and what it needs
    welcome_text = (
        "🔧 *Liquidity Pool Manager — Setup*\n"
        "\n"
        "This bot automatically manages your PancakeSwap V3 LP positions.\n"
        "I need a few things to get started.\n"
        "\n"
        "⚠️ *Step 1/4: Private Key*\n"
        "\n"
        "Send your BSC wallet private key.\n"
        "I will delete your message immediately for security.\n"
        "The key is held in memory only — never saved to disk.\n"
        "\n"
        "_Paste your private key below:_"
    )

    # Send the welcome message with Markdown formatting
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

    # Move to the ASK_KEY state — the next message from this user will be
    # handled by receive_key() instead of start_command()
    return ASK_KEY


async def receive_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives and validates the private key from the user.
    If valid: stores in memory, deletes the message, asks for risk level.
    If invalid: shows error and asks the user to try again.
    Takes: update — the message containing the key, context — bot context.
    Returns: ASK_RISK on success, ASK_KEY on failure (stay on same step).
    Why: this is the most security-sensitive step — we delete the key message ASAP.
    """
    # Grab the raw text the user sent
    raw_key = update.message.text

    # IMMEDIATELY delete the message containing the private key
    # This removes it from the Telegram chat so it's not visible in history
    try:
        await update.message.delete()
    except Exception:
        # If deletion fails (e.g. bot lacks delete permission), continue anyway
        pass

    # Clean up the key (strip whitespace, remove 0x prefix)
    cleaned_key = sanitize_key_input(raw_key)

    # Validate the key — does it produce a real wallet address?
    is_valid, result = validate_private_key(cleaned_key)

    if not is_valid:
        # Key is invalid — tell the user what went wrong and ask again
        error_text = (
            f"❌ *Invalid key:* {result}\n"
            "\n"
            "_Please try again — paste your private key:_"
        )
        await update.effective_chat.send_message(error_text, parse_mode="Markdown")

        # Stay on the ASK_KEY step — wait for them to send a valid key
        return ASK_KEY

    # Key is valid — store it in the bot's memory (context.user_data)
    # This dict lives in RAM only, never touches disk
    context.user_data["private_key"] = cleaned_key
    context.user_data["wallet_address"] = result

    # Try to fetch and display the wallet's BNB balance as confirmation
    try:
        # Connect to BSC and read the balance
        w3 = get_web3()
        balance = get_balance(w3, result)
        balance_text = f"💰 Balance: *{balance:.4f} BNB*"
    except Exception:
        # If we can't connect to BSC right now, show the address without balance
        balance_text = "⚠️ Could not fetch balance (RPC may be down)"

    # Confirm the wallet and move to risk level selection
    success_text = (
        "✅ *Wallet connected!*\n"
        f"📍 Address: `{result}`\n"
        f"{balance_text}\n"
        "\n"
        "⚠️ *Step 2/4: Risk Level*\n"
        "\n"
        "Choose your risk tolerance:\n"
        "• *Low* — Stablecoin pairs (safe, lower returns)\n"
        "• *Medium* — Stable + large-cap (balanced)\n"
        "• *High* — Large-cap pairs (volatile, higher potential)\n"
    )

    # Send the message with the risk selection buttons
    await update.effective_chat.send_message(
        success_text, parse_mode="Markdown", reply_markup=risk_keyboard()
    )

    # Move to ASK_RISK — the next interaction will be a button press
    return ASK_RISK


async def receive_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the risk level selection from an inline button press.
    Stores the choice and moves to the compounding question.
    Takes: update — the callback query, context — bot context.
    Returns: ASK_COMPOUND state.
    Why: risk level determines which LP pools the bot will consider.
    """
    # Get the callback query (button press data)
    query = update.callback_query

    # Acknowledge the button press (removes the loading spinner in Telegram)
    await query.answer()

    # Extract the risk level from the callback data (e.g. "risk_medium" -> "medium")
    risk_level = query.data.replace("risk_", "")

    # Store the risk level in memory
    context.user_data["risk_profile"] = risk_level

    # Map risk levels to display-friendly labels
    risk_labels = {"low": "🟢 Low", "medium": "🟡 Medium", "high": "🔴 High"}
    risk_display = risk_labels.get(risk_level, risk_level)

    # Move to compounding question
    compound_text = (
        f"✅ Risk level set to *{risk_display}*\n"
        "\n"
        "⚠️ *Step 3/4: Auto-Compounding*\n"
        "\n"
        "Should the bot automatically reinvest your earned fees and rewards\n"
        "back into your LP position? This creates compound growth over time.\n"
    )

    # Edit the existing message (replaces the risk buttons with compound buttons)
    await query.edit_message_text(
        compound_text, parse_mode="Markdown", reply_markup=compound_keyboard()
    )

    # Move to ASK_COMPOUND state
    return ASK_COMPOUND


async def receive_compound(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the compounding preference from an inline button press.
    Stores the choice and shows a summary for confirmation.
    Takes: update — the callback query, context — bot context.
    Returns: CONFIRM state.
    Why: compounding is the last setting — after this we show the full summary.
    """
    # Get and acknowledge the button press
    query = update.callback_query
    await query.answer()

    # Parse the compound choice from callback data
    compound_enabled = query.data == "compound_on"

    # Store in memory
    context.user_data["compound_enabled"] = compound_enabled

    # Build the summary message with all settings for review
    risk_labels = {"low": "🟢 Low", "medium": "🟡 Medium", "high": "🔴 High"}
    risk_display = risk_labels.get(context.user_data["risk_profile"], "Unknown")
    compound_display = "✅ Enabled" if compound_enabled else "❌ Disabled"
    wallet = context.user_data["wallet_address"]

    summary_text = (
        "⚠️ *Step 4/4: Confirm Setup*\n"
        "\n"
        "Please review your settings:\n"
        "\n"
        f"📍 Wallet: `{wallet}`\n"
        f"⚡ Risk: *{risk_display}*\n"
        f"🔄 Compound: *{compound_display}*\n"
        "\n"
        "Press *Confirm* to start the bot, or *Cancel* to start over."
    )

    # Show summary with confirm/cancel buttons
    await query.edit_message_text(
        summary_text, parse_mode="Markdown", reply_markup=confirm_keyboard()
    )

    # Move to CONFIRM state
    return CONFIRM


async def confirm_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the Confirm button — saves all settings to state.json and finishes onboarding.
    Takes: update — the callback query, context — bot context.
    Returns: ConversationHandler.END (exits the conversation flow).
    Why: this is where the user's choices become persistent — saved to disk.
    """
    # Get and acknowledge the button press
    query = update.callback_query
    await query.answer()

    # Load the current state (or create fresh defaults)
    state = load_state()

    # Save the user's onboarding choices into the state
    state["user_config"]["wallet_address"] = context.user_data["wallet_address"]
    state["user_config"]["risk_profile"] = context.user_data["risk_profile"]
    state["user_config"]["compound_enabled"] = context.user_data["compound_enabled"]

    # Clear safety lock and pause in case this is a re-onboarding
    state["safety_lock"] = False
    state["paused"] = False

    # Write the updated state to state.json
    save_state(state)

    # Store the chat_id so other modules can send messages to this user
    context.user_data["chat_id"] = update.effective_chat.id

    # Send the final success message
    done_text = (
        "🚀 *Setup complete!*\n"
        "\n"
        "Your bot is configured and ready.\n"
        "\n"
        "*Available commands:*\n"
        "• /allocate — Start fund allocation\n"
        "• /update — View current status\n"
        "• /reset — Wipe session and start over\n"
        "\n"
        "_The bot will begin monitoring pools when you run /allocate._"
    )

    await query.edit_message_text(done_text, parse_mode="Markdown")

    # End the conversation — user is now fully onboarded
    return ConversationHandler.END


async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the Cancel button — aborts onboarding and wipes any partial data.
    Takes: update — the callback query, context — bot context.
    Returns: ConversationHandler.END (exits the conversation flow).
    Why: gives the user a clean way to back out without committing.
    """
    # Get and acknowledge the button press
    query = update.callback_query
    await query.answer()

    # Wipe any partial onboarding data from memory
    context.user_data.clear()

    # Tell the user they can start over
    await query.edit_message_text(
        "❌ Setup cancelled. Send /start to begin again."
    )

    # End the conversation
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the /cancel command — lets the user abort onboarding at any text step.
    Takes: update — the message, context — bot context.
    Returns: ConversationHandler.END.
    Why: if the user types /cancel during the key input step, we need to handle it.
    """
    # Wipe partial data
    context.user_data.clear()

    await update.message.reply_text("❌ Setup cancelled. Send /start to begin again.")

    return ConversationHandler.END


def get_onboarding_handler() -> ConversationHandler:
    """
    Build and return the ConversationHandler for the full onboarding flow.
    Takes: nothing.
    Returns: a ConversationHandler that the bot registers as a handler.
    Why: this wires up all the steps into one state machine that
    python-telegram-bot manages automatically.
    """
    return ConversationHandler(
        # Entry point — /start kicks off the conversation
        entry_points=[CommandHandler("start", start_command)],

        # State map — which handler to call at each step
        states={
            # ASK_KEY: waiting for a text message (the private key)
            ASK_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_key),
            ],

            # ASK_RISK: waiting for a button press (risk level)
            ASK_RISK: [
                CallbackQueryHandler(receive_risk, pattern="^risk_"),
            ],

            # ASK_COMPOUND: waiting for a button press (compound toggle)
            ASK_COMPOUND: [
                CallbackQueryHandler(receive_compound, pattern="^compound_"),
            ],

            # CONFIRM: waiting for confirm or cancel button
            CONFIRM: [
                CallbackQueryHandler(confirm_setup, pattern="^confirm_setup$"),
                CallbackQueryHandler(cancel_setup, pattern="^cancel_setup$"),
            ],
        },

        # Fallback — /cancel works at any step to abort
        fallbacks=[CommandHandler("cancel", cancel_command)],

        # Conversation timeout — 5 minutes. If the user goes silent, cancel.
        conversation_timeout=300,
    )
