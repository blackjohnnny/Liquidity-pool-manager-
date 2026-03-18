"""
Main Entry Point
=================
Starts the Liquidity Pool Manager bot.

Run this file to launch the bot:
    python main.py

The bot will connect to Telegram and wait for the user to send /start.
All configuration is loaded from .env (copy .env.example and fill in your values).

Why this file is minimal: all logic lives in dedicated modules. This file
only imports and calls run_bot(). If it grows beyond this, something is wrong.
"""

from telegram_bot.bot import run_bot

# --- Entry point ---
# Only runs when this file is executed directly (not when imported)
if __name__ == "__main__":
    print("=" * 40)
    print("  Liquidity Pool Manager")
    print("  Starting bot...")
    print("=" * 40)

    # Start the Telegram bot — this blocks forever (polling loop)
    # Press Ctrl+C to stop
    run_bot()
