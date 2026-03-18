"""
Bot Module
==========
Sets up the Telegram bot application, registers all command handlers,
callback handlers, the onboarding conversation flow, and a global error handler.

Think of this as the "wiring diagram" — it connects user actions (commands,
button presses) to the right handler functions. It doesn't contain any
logic itself, just the registration.

Takes: nothing (reads token from settings).
Returns: a configured Application object ready to start polling.
Why: centralises all handler registration in one place so main.py stays clean.
"""

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config.settings import TELEGRAM_BOT_TOKEN
from telegram_bot.onboarding import get_onboarding_handler
from telegram_bot.handlers import allocate_command, update_command, reset_command
from telegram_bot.callbacks import button_callback

# Set up logging for the bot
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Global error handler — catches any unhandled exceptions in any handler.
    Takes: update — the update that caused the error, context — with context.error.
    Returns: nothing (sends user-friendly error message).
    Why: without this, unhandled exceptions crash the handler silently. The user
    sees nothing and wonders why the bot stopped responding. This catches everything
    and sends a friendly "something went wrong" message.
    """
    # Log the full error for debugging
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)

    # Try to send a friendly error message to the user
    if update and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Something went wrong. Please try again.\n"
                    "If this keeps happening, use /reset to start fresh."
                ),
            )
        except Exception:
            # If we can't even send the error message, just log it
            pass


def create_bot() -> Application:
    """
    Build and configure the Telegram bot application with all handlers.
    Takes: nothing (uses TELEGRAM_BOT_TOKEN from settings).
    Returns: a fully configured Application object.
    Why: this is the single setup function — call it once, get a ready-to-run bot.
    """
    # Validate that we have a token before trying to build the bot
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Copy .env.example to .env and add your bot token from @BotFather."
        )

    # Build the application using the bot token
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Register the global error handler ---
    # This catches ANY unhandled exception in any handler and sends a user-friendly message
    app.add_error_handler(_error_handler)

    # --- Register the onboarding conversation handler ---
    # This handles /start and the multi-step setup flow (key, risk, compound, confirm).
    # Must be added FIRST — ConversationHandler needs priority over standalone handlers
    # so it can track which step the user is on.
    onboarding_handler = get_onboarding_handler()
    app.add_handler(onboarding_handler)

    # --- Register standalone command handlers ---
    # These handle commands that work after onboarding is complete.
    app.add_handler(CommandHandler("allocate", allocate_command))
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("reset", reset_command))

    # --- Register the callback query handler for settings buttons ---
    # This catches button presses that are NOT part of the onboarding flow.
    # The onboarding ConversationHandler catches its own buttons internally,
    # so this only fires for post-onboarding settings buttons.
    app.add_handler(CallbackQueryHandler(button_callback))

    return app


def run_bot() -> None:
    """
    Create the bot and start polling for Telegram messages.
    Takes: nothing.
    Returns: never (runs forever until interrupted with Ctrl+C).
    Why: this is the main entry point — starts the bot and keeps it running.
    """
    # Build the bot with all handlers registered
    app = create_bot()

    # Start polling — the bot will continuously check Telegram for new messages
    # drop_pending_updates=True skips any messages that arrived while the bot was offline
    print("Bot is starting...")
    app.run_polling(drop_pending_updates=True)
