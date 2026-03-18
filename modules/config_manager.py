"""
Config Manager Module
=====================
Manages user configuration and provides risk classification for LP pools.
Sits between the raw state.json (handled by state_store) and the rest of the
system — other modules ask config_manager for settings instead of reading
state.json directly.

Also contains the pool risk classification logic: given two token addresses,
it determines whether the pair is low, medium, high, or extreme risk based
on what kind of tokens they are (stablecoin, large-cap, or small-cap).

Takes: token addresses, state dicts.
Returns: config values, risk labels, filtered pool lists.
Why: centralises all config reads and risk logic so it's consistent across modules.
"""

from utils.state_store import load_state, save_state
from config.settings import STABLECOINS, LARGE_CAPS


def load_user_config() -> dict:
    """
    Read the user_config section from state.json.
    Takes: nothing.
    Returns: dict with keys: risk_profile, compound_enabled, wallet_address.
    Why: convenience wrapper — modules shouldn't need to know state.json's structure.
    """
    # Load the full state and extract just the user config section
    state = load_state()
    return state.get("user_config", {})


def save_user_config(config: dict) -> None:
    """
    Write updated user config back to state.json.
    Takes: config (dict) — the user_config section to save.
    Returns: nothing.
    Why: updates only the config section without touching PnL or other state.
    """
    # Load full state, replace config section, save back
    state = load_state()
    state["user_config"] = config
    save_state(state)


def get_risk_profile() -> str:
    """
    Get the user's current risk level.
    Takes: nothing.
    Returns: "low", "medium", or "high" (or None if not set).
    Why: used by the logic engine to filter which pools to consider.
    """
    config = load_user_config()
    return config.get("risk_profile")


def is_compound_enabled() -> bool:
    """
    Check if auto-compounding is turned on.
    Takes: nothing.
    Returns: True if enabled, False otherwise.
    Why: the logic engine checks this to decide if it should harvest rewards.
    """
    config = load_user_config()
    return config.get("compound_enabled", False)


def is_safety_locked() -> bool:
    """
    Check if the safety lock is active (bot is frozen after a critical error).
    Takes: nothing.
    Returns: True if locked, False if normal.
    Why: the scheduler checks this at the top of every cycle — if locked, skip everything.
    """
    state = load_state()
    return state.get("safety_lock", False)


def is_paused() -> bool:
    """
    Check if the bot is paused by the user.
    Takes: nothing.
    Returns: True if paused, False if running.
    Why: different from safety lock — this is a voluntary pause, not an error state.
    """
    state = load_state()
    return state.get("paused", False)


def set_safety_lock(locked: bool) -> None:
    """
    Set or clear the safety lock flag.
    Takes: locked (bool) — True to lock, False to unlock.
    Returns: nothing.
    Why: called by safety_controller to lock after a critical error,
    and by the user's /reset or safety-clear button to unlock.
    """
    state = load_state()
    state["safety_lock"] = locked
    save_state(state)


def classify_pool_risk(token0_address: str, token1_address: str) -> str:
    """
    Determine the risk level of an LP pair based on its token types.
    Takes: token0_address, token1_address — the two tokens in the pool.
    Returns: "low", "medium", "high", or "extreme".

    The logic works like a simple decision tree:
    - Both tokens are stablecoins? → Low risk (minimal price movement)
    - One stable + one large-cap? → Medium risk (one side is anchored)
    - Both large-cap? → High risk (both sides can move, but liquid)
    - Anything else? → Extreme risk (unknown or small-cap tokens)

    Why: this is the core of the risk filtering system — it decides which
    pools a low-risk user sees vs a high-risk user.
    """
    # Normalise addresses to lowercase for comparison with our sets
    t0 = token0_address.lower()
    t1 = token1_address.lower()

    # Check which category each token falls into
    t0_stable = t0 in STABLECOINS
    t1_stable = t1 in STABLECOINS
    t0_large = t0 in LARGE_CAPS
    t1_large = t1 in LARGE_CAPS

    # Both stablecoins → lowest risk (e.g. USDT/USDC)
    if t0_stable and t1_stable:
        return "low"

    # One stable + one large-cap → medium risk (e.g. USDT/BNB)
    if (t0_stable and t1_large) or (t1_stable and t0_large):
        return "medium"

    # Both large-cap → high risk (e.g. BNB/ETH)
    if t0_large and t1_large:
        return "high"

    # Anything else (small-cap, unknown tokens) → extreme risk
    # These are filtered out entirely — too risky for automated management
    return "extreme"


def filter_pools_by_risk(pools: list, risk_profile: str) -> list:
    """
    Filter a list of pools to only include those matching the user's risk level.
    Takes: pools (list of dicts) — each with 'risk' key, risk_profile (str) — user's level.
    Returns: filtered list of pools.

    The filtering is inclusive downward:
    - "low" → only low-risk pools
    - "medium" → low + medium pools
    - "high" → low + medium + high pools

    Why: a "medium" user should still see safe stablecoin pools, not just medium ones.
    This gives them the full range of options up to their tolerance.
    """
    # Define which risk levels each profile is allowed to see
    allowed_risks = {
        "low": {"low"},
        "medium": {"low", "medium"},
        "high": {"low", "medium", "high"},
    }

    # Get the set of allowed risk levels for this user
    allowed = allowed_risks.get(risk_profile, {"low"})

    # Keep only pools whose risk level is in the allowed set
    return [pool for pool in pools if pool.get("risk") in allowed]
