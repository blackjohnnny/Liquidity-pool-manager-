"""
Scheduler & Cycle Controller Module
=====================================
Runs the 15-second execution cycle that drives the entire bot.
Every 15 seconds, it calls the dispatcher to run one full cycle:
fetch data → compare → decide → execute → track PnL → notify.

Uses APScheduler's BackgroundScheduler to run in a separate thread
so the Telegram bot can keep handling messages while cycles run.

Think of this like a heartbeat — every 15 seconds, the bot "wakes up",
checks the market, decides if anything needs doing, then goes back to sleep.

Takes: a Web3 connection, user_data dict (holds private key), bot application.
Returns: nothing (runs continuously in the background).
Why: the bot must operate autonomously 24/7 without the user doing anything.
"""

import threading
import time
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config.settings import CYCLE_INTERVAL_SECONDS

# Set up logging so we can track scheduler activity
logger = logging.getLogger(__name__)

# --- Module-level state ---
# The scheduler instance and a lock to prevent overlapping cycles
_scheduler = None
_cycle_lock = threading.Lock()
_is_running = False

# Consecutive error counter — if too many cycles fail in a row, trigger safety
_consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5  # After 5 failures in a row, trigger fail-safe


def start_scheduler(w3, user_data: dict, bot_app) -> None:
    """
    Start the 15-second background scheduler.
    Takes: w3 — Web3 connection, user_data — dict with private_key and wallet_address,
           bot_app — the Telegram Application object (for sending messages).
    Returns: nothing (runs in background thread).
    Why: called once after the user finishes /start onboarding. Runs until the bot stops.
    """
    global _scheduler, _is_running

    # Don't start a second scheduler if one is already running
    if _is_running:
        logger.info("Scheduler already running — skipping start")
        return

    # Create the background scheduler
    _scheduler = BackgroundScheduler()

    # Add the cycle job — runs every CYCLE_INTERVAL_SECONDS (default 15)
    _scheduler.add_job(
        func=_run_cycle_wrapper,
        trigger=IntervalTrigger(seconds=CYCLE_INTERVAL_SECONDS),
        args=[w3, user_data, bot_app],
        id="main_cycle",
        name="Main Execution Cycle",
        max_instances=1,  # Prevent overlapping cycles (APScheduler enforces this)
    )

    # Start the scheduler in a background thread
    _scheduler.start()
    _is_running = True

    logger.info(f"Scheduler started — running every {CYCLE_INTERVAL_SECONDS} seconds")


def stop_scheduler() -> None:
    """
    Stop the background scheduler cleanly.
    Takes: nothing.
    Returns: nothing.
    Why: called on bot shutdown or /reset to stop the cycle loop.
    """
    global _scheduler, _is_running

    if _scheduler and _is_running:
        _scheduler.shutdown(wait=False)
        _is_running = False
        logger.info("Scheduler stopped")


def _run_cycle_wrapper(w3, user_data: dict, bot_app) -> None:
    """
    Wrapper around the dispatcher's run_cycle that adds safety checks.
    Takes: w3, user_data, bot_app — same as start_scheduler.
    Returns: nothing.
    Why: adds the threading lock and error logging around the dispatcher call.
    The scheduler calls this every 15 seconds — it must NEVER crash.
    """
    # Use a non-blocking lock attempt — if the previous cycle is still running,
    # skip this one rather than queue up
    acquired = _cycle_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("Previous cycle still running — skipping this tick")
        return

    global _consecutive_errors

    try:
        # Import here to avoid circular imports (dispatcher imports other modules)
        from modules.dispatcher import run_cycle

        # Record the cycle start time for performance tracking
        start_time = time.time()

        # Run the full cycle
        run_cycle(w3, user_data, bot_app)

        # Success — reset the consecutive error counter
        _consecutive_errors = 0

        # Log the cycle duration
        elapsed = time.time() - start_time
        if elapsed > CYCLE_INTERVAL_SECONDS - 1:
            logger.warning(f"Cycle took {elapsed:.1f}s — dangerously close to interval")
        else:
            logger.debug(f"Cycle completed in {elapsed:.1f}s")

    except Exception as e:
        # Increment the consecutive error counter
        _consecutive_errors += 1
        logger.error(f"Unhandled error in cycle ({_consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")

        # If too many consecutive failures, trigger the fail-safe
        if _consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.critical(f"{MAX_CONSECUTIVE_ERRORS} consecutive cycle failures — triggering safety")
            from modules.safety_controller import trigger_failsafe
            trigger_failsafe(
                Exception(f"{MAX_CONSECUTIVE_ERRORS} consecutive cycle failures"),
                user_data.get("private_key", ""),
                w3,
                user_data,
                bot_app,
            )
            _consecutive_errors = 0  # Reset after triggering

    finally:
        # Always release the lock so the next cycle can run
        _cycle_lock.release()


def is_scheduler_running() -> bool:
    """
    Check if the scheduler is currently active.
    Takes: nothing.
    Returns: True if running, False if stopped.
    Why: used by Telegram handlers to show the correct status.
    """
    return _is_running
