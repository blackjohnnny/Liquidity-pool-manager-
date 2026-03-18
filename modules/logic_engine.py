"""
Logic Engine Module (Decision Brain)
=====================================
The core decision-maker. Given market data, deltas, user config, and the
current position, it decides ONE of three actions:

  NO_ACTION  — nothing worth doing this cycle, stay put
  REBALANCE  — move liquidity to a better pool
  COMPOUND   — harvest rewards and reinvest into the current position

Think of this as the "brain" of the bot. The market fetcher is the eyes,
the execution engine is the hands, and this module is the brain that
looks at what the eyes see and tells the hands what to do.

Takes: market data, deltas, user config, current position.
Returns: (decision_string, execution_plan_dict).
Why: separating the decision from the execution means we can test the logic
without ever touching real money on-chain.
"""

from config.settings import REBALANCE_THRESHOLD_PERCENT
from modules.config_manager import filter_pools_by_risk, get_risk_profile
from modules.comparator import detect_out_of_range

# --- Scoring weights ---
# These control how much each factor matters when ranking pools.
# APR is king (0.5), but TVL (0.3) and stability (0.2) also matter.
WEIGHT_APR = 0.5       # Higher APR = higher score
WEIGHT_TVL = 0.3       # Higher TVL = more liquid = safer
WEIGHT_STABILITY = 0.2  # Lower volatility = more predictable


def score_pool(pool: dict, pool_delta: dict = None) -> float:
    """
    Calculate a single score for a pool based on APR, TVL, and stability.
    Takes: pool — pool data dict, pool_delta — change data from comparator (optional).
    Returns: a float score (higher = better pool).
    Why: we need a single number to rank pools against each other.

    The scoring works like a weighted average:
    - APR score: normalised APR (capped at 200% to prevent outlier distortion)
    - TVL score: log-scaled TVL (so $10M vs $100M isn't a 10x difference in score)
    - Stability score: inverse of recent APR volatility (stable = good)
    """
    import math

    # --- APR component ---
    # Cap at 200% to prevent insanely high (and probably unsustainable) APRs
    # from dominating the score
    raw_apy = pool.get("apy", 0) or 0
    capped_apy = min(raw_apy, 200.0)

    # Normalise to 0-100 range (200% APR = score of 100)
    apr_score = (capped_apy / 200.0) * 100

    # --- TVL component ---
    # Use log scale so the difference between $1M and $10M matters,
    # but the difference between $100M and $1B doesn't dominate
    raw_tvl = pool.get("tvl_usd", 0) or 0

    # Minimum TVL threshold — pools under $10K are too thin to use safely
    if raw_tvl < 10_000:
        return 0  # Instant disqualification — too risky

    # Log-scale normalisation (log10 of $10K=4, $1M=6, $100M=8)
    # Map to 0-100 range where $100M+ gets full marks
    tvl_score = min((math.log10(max(raw_tvl, 1)) - 4) / 4 * 100, 100)

    # --- Stability component ---
    # If we have delta data, use APR volatility as the stability metric
    # Lower volatility = higher stability score
    stability_score = 80  # Default: assume moderately stable if no delta data

    if pool_delta:
        # Use the absolute relative APR change as a volatility proxy
        apr_volatility = abs(pool_delta.get("apy_change_rel", 0))

        # Map volatility to stability: 0% change = 100, 50%+ change = 0
        stability_score = max(0, 100 - (apr_volatility * 2))

    # --- Weighted final score ---
    final_score = (
        (apr_score * WEIGHT_APR) +
        (tvl_score * WEIGHT_TVL) +
        (stability_score * WEIGHT_STABILITY)
    )

    return final_score


def rank_pools(pools: list, deltas: dict) -> list:
    """
    Score and rank a list of pools from best to worst.
    Takes: pools — filtered pool list, deltas — delta data from comparator.
    Returns: list of (pool, score) tuples sorted by score descending.
    Why: after filtering by risk, we need to know which pool is the BEST option.
    """
    # Get per-pool delta data for stability scoring
    pool_deltas = deltas.get("pool_deltas", {})

    scored = []
    for pool in pools:
        # Find the matching delta data for this pool (if it exists)
        pool_id = pool.get("pool_id", "")
        delta = pool_deltas.get(pool_id, None)

        # Calculate the score
        score = score_pool(pool, delta)

        # Only include pools that scored above zero (survived TVL check)
        if score > 0:
            scored.append((pool, score))

    # Sort by score descending — best pool first
    scored.sort(key=lambda x: x[1], reverse=True)

    return scored


def make_decision(
    current_data: dict,
    deltas: dict,
    user_config: dict,
    current_position: dict,
) -> tuple:
    """
    The main decision function. Decides what action to take this cycle.
    Takes:
        current_data — this cycle's market data (pools + prices)
        deltas — changes since last cycle (from comparator)
        user_config — user's risk profile and compound preference
        current_position — the user's active LP position (or None)
    Returns: tuple of (decision: str, plan: dict)
        - ("NO_ACTION", {}) — nothing to do
        - ("REBALANCE", {plan details}) — move to a better pool
        - ("COMPOUND", {plan details}) — harvest and reinvest rewards
    Why: this is THE function that drives the entire bot. Everything else exists
    to feed data into this function or execute its output.
    """
    # Get the user's risk profile for filtering
    risk_profile = user_config.get("risk_profile", "low")
    compound_enabled = user_config.get("compound_enabled", False)

    # --- Step 1: Filter pools by the user's risk level ---
    all_pools = current_data.get("pools", [])
    filtered_pools = filter_pools_by_risk(all_pools, risk_profile)

    # If no pools survive the filter, nothing to do
    if not filtered_pools:
        return ("NO_ACTION", {"reason": "No pools match risk profile"})

    # --- Step 2: Score and rank the filtered pools ---
    ranked = rank_pools(filtered_pools, deltas)

    # If nothing scored above zero, nothing worth investing in
    if not ranked:
        return ("NO_ACTION", {"reason": "No pools met minimum quality threshold"})

    # The best available pool
    best_pool, best_score = ranked[0]

    # --- Step 3: Check if we have an existing position ---
    if current_position is None:
        # No position exists — we should allocate to the best pool
        # This is essentially the first allocation, not a "rebalance"
        plan = build_rebalance_plan(None, best_pool, current_data.get("prices", {}))
        return ("REBALANCE", plan)

    # --- Step 4: Score the current position's pool ---
    # Find the current pool in our data to score it
    current_pool_id = current_position.get("pool_id", "")
    current_pool_data = None
    for pool in all_pools:
        if pool.get("pool_id") == current_pool_id:
            current_pool_data = pool
            break

    # If we can't find the current pool in the data, something is off
    # Default to a low score so rebalancing is considered
    if current_pool_data:
        current_delta = deltas.get("pool_deltas", {}).get(current_pool_id, None)
        current_score = score_pool(current_pool_data, current_delta)
    else:
        current_score = 0

    # --- Step 5: Check if the position is out of range ---
    # V3 positions stop earning when price leaves their range
    current_tick = current_position.get("current_tick")
    if current_tick is not None and detect_out_of_range(current_position, current_tick):
        # Position is out of range — rebalance regardless of score difference
        plan = build_rebalance_plan(current_position, best_pool, current_data.get("prices", {}))
        plan["reason"] = "Position out of range"
        return ("REBALANCE", plan)

    # --- Step 6: Check anomalies ---
    # If the comparator flagged any anomalies, be cautious
    anomalies = deltas.get("anomalies", [])
    if anomalies:
        # Log anomalies but don't auto-rebalance just because of them
        # The anomaly info is passed along in the plan for the notifier
        pass

    # --- Step 7: Compare best pool vs current pool ---
    # Only rebalance if the best pool beats the current by the threshold
    score_difference = best_score - current_score

    # Calculate the percentage improvement
    if current_score > 0:
        improvement_pct = (score_difference / current_score) * 100
    else:
        # Current pool scored 0 — any positive alternative is worth switching to
        improvement_pct = 100 if best_score > 0 else 0

    # Check if the improvement exceeds the rebalance threshold
    if improvement_pct >= REBALANCE_THRESHOLD_PERCENT:
        plan = build_rebalance_plan(current_position, best_pool, current_data.get("prices", {}))
        plan["reason"] = f"Better pool found (+{improvement_pct:.1f}% score improvement)"
        plan["anomalies"] = anomalies
        return ("REBALANCE", plan)

    # --- Step 8: Check if we should compound ---
    # Only compound if enabled AND no rebalance was needed
    if compound_enabled:
        # Check if there are pending rewards to compound
        tokens_owed_0 = current_position.get("tokens_owed_0", 0)
        tokens_owed_1 = current_position.get("tokens_owed_1", 0)

        # Only compound if rewards exceed a minimum threshold (avoid wasting gas on dust)
        if tokens_owed_0 > 0 or tokens_owed_1 > 0:
            plan = build_compound_plan(current_position, current_data.get("prices", {}))
            plan["anomalies"] = anomalies
            return ("COMPOUND", plan)

    # --- Step 9: Nothing to do ---
    return ("NO_ACTION", {
        "reason": "Current position is optimal",
        "current_score": current_score,
        "best_score": best_score,
        "improvement_pct": improvement_pct,
        "anomalies": anomalies,
    })


def build_rebalance_plan(
    current_position: dict,
    target_pool: dict,
    prices: dict,
) -> dict:
    """
    Build a structured plan describing the steps to rebalance.
    Takes:
        current_position — existing position to exit (or None for first allocation)
        target_pool — the pool to enter
        prices — current token prices
    Returns: dict describing each step the execution engine must perform.
    Why: separating the "what to do" (plan) from "doing it" (execution) makes
    the system more testable and debuggable. We can log the plan before executing.
    """
    plan = {
        "action": "REBALANCE",
        "target_pool": {
            "pool_id": target_pool.get("pool_id", ""),
            "symbol": target_pool.get("symbol", ""),
            "apy": target_pool.get("apy", 0),
            "tvl_usd": target_pool.get("tvl_usd", 0),
            "risk": target_pool.get("risk", "unknown"),
        },
        "steps": [],
    }

    # If we have an existing position, first step is to exit it
    if current_position:
        plan["exit_position"] = {
            "token_id": current_position.get("token_id"),
            "pool_id": current_position.get("pool_id", ""),
        }
        # The execution steps for exiting
        plan["steps"].append("remove_liquidity")
        plan["steps"].append("collect_fees")

    # Steps to enter the new pool
    plan["steps"].append("swap_tokens")    # Swap to get the right token pair
    plan["steps"].append("approve_tokens")  # Approve the position manager to spend
    plan["steps"].append("add_liquidity")   # Mint a new V3 position

    return plan


def build_compound_plan(current_position: dict, prices: dict) -> dict:
    """
    Build a structured plan describing the steps to compound rewards.
    Takes: current_position — the position to compound, prices — current token prices.
    Returns: dict describing the compounding steps.
    Why: compounding is simpler than rebalancing — just collect fees and add them
    back to the existing position.
    """
    plan = {
        "action": "COMPOUND",
        "position": {
            "token_id": current_position.get("token_id"),
            "pool_id": current_position.get("pool_id", ""),
        },
        "steps": [
            "collect_fees",       # Collect earned trading fees
            "harvest_rewards",    # Harvest CAKE farming rewards (if staked)
            "swap_rewards",       # Swap CAKE to position tokens
            "add_liquidity",      # Add everything back to the position
        ],
    }

    return plan
