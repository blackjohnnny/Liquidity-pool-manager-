"""
State Store Module
==================
Handles reading and writing the state.json file — our local "database".
This is where we persist everything between bot restarts: user config,
PnL history, cycle snapshots, and the safety lock flag.

Uses atomic writes (write to temp file, then rename) to prevent corruption
if the bot crashes mid-write. Also uses file locking to prevent the scheduler
thread and telegram handler thread from writing at the same time.

Takes: state dictionaries.
Returns: state dictionaries.
Why: without persistent state, the bot forgets everything on restart.
"""

import json
import os
import threading
from config.settings import STATE_FILE_PATH

# --- Thread lock to prevent simultaneous reads/writes from different threads ---
# The scheduler runs in one thread, Telegram handlers in another —
# both might try to read/write state.json at the same time
_state_lock = threading.Lock()


def get_default_state() -> dict:
    """
    Return the skeleton state structure with all fields set to defaults.
    Takes: nothing.
    Returns: a fresh state dict with every expected key.
    Why: used when state.json doesn't exist yet (first run) or after /reset.
    """
    return {
        # Safety lock — when True, the bot refuses to run any cycles
        # Only set to True by the fail-safe controller after a critical error
        "safety_lock": False,

        # Whether the bot is paused by the user (different from safety lock)
        "paused": False,

        # User preferences — filled in during /start onboarding
        "user_config": {
            "risk_profile": None,       # "low", "medium", or "high"
            "compound_enabled": False,   # Whether to auto-compound rewards
            "wallet_address": None,      # Public address derived from private key
        },

        # Snapshot of the previous cycle's market data — used by the comparator
        # to calculate deltas (what changed since last check)
        "previous_cycle": None,

        # Profit and loss tracking
        "pnl": {
            "cycle_pnl": 0.0,           # PnL from the most recent cycle
            "total_pnl": 0.0,           # Cumulative PnL since bot started
            "total_gas_spent": 0.0,     # Total gas fees paid in USD
        },

        # Current LP position details (None if no position is open)
        "current_position": None,

        # How many cycles have completed since the bot started
        "cycle_count": 0,
    }


def load_state() -> dict:
    """
    Read state.json from disk and return it as a dict.
    If the file doesn't exist or is corrupted, return fresh defaults.
    Takes: nothing (reads from STATE_FILE_PATH).
    Returns: the state dict.
    Why: called at the start of every cycle and every Telegram command.
    """
    # Acquire the lock so no other thread can write while we're reading
    with _state_lock:
        # If the file doesn't exist yet, return a fresh default state
        if not os.path.exists(STATE_FILE_PATH):
            return get_default_state()

        try:
            # Read and parse the JSON file
            with open(STATE_FILE_PATH, "r") as f:
                state = json.load(f)

            # Validate the loaded state has all expected keys
            # If any are missing (e.g. from an older version), fill them in
            default = get_default_state()
            for key in default:
                if key not in state:
                    state[key] = default[key]

            return state

        except (json.JSONDecodeError, IOError):
            # File is corrupted or unreadable — start fresh
            return get_default_state()


def save_state(state: dict) -> None:
    """
    Write the state dict to state.json using atomic write.
    Takes: state (dict) — the full state to persist.
    Returns: nothing.
    Why: atomic write prevents corruption — we write to a temp file first,
    then rename it over the real file. If the bot crashes during write,
    the old file is still intact.
    """
    # Acquire the lock so no other thread can read/write simultaneously
    with _state_lock:
        # Keep one backup of the previous state in case the new write corrupts
        backup_path = STATE_FILE_PATH + ".bak"
        if os.path.exists(STATE_FILE_PATH):
            try:
                # Copy current state to backup before overwriting
                import shutil
                shutil.copy2(STATE_FILE_PATH, backup_path)
            except Exception:
                pass  # Backup failure is not critical — continue with the write

        # Write to a temporary file first (same directory for rename to work)
        temp_path = STATE_FILE_PATH + ".tmp"

        with open(temp_path, "w") as f:
            # indent=2 makes the file human-readable if you want to inspect it
            json.dump(state, f, indent=2)

        # Atomically replace the old state file with the new one
        # On Windows, os.replace handles overwriting the existing file
        os.replace(temp_path, STATE_FILE_PATH)


def reset_state() -> None:
    """
    Wipe state.json back to fresh defaults.
    Takes: nothing.
    Returns: nothing.
    Why: called by /reset command to clear everything — PnL, config, safety lock.
    """
    # Just save a fresh default state, overwriting whatever was there
    save_state(get_default_state())
