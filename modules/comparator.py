"""
Delta & State Comparator Module
================================
Compares the current cycle's market data against the previous cycle's snapshot.
Calculates how much things have changed (the "deltas") so the logic engine
can decide if action is needed.

Think of this like a security guard checking two photos taken 15 seconds apart:
"Did anything move enough to care about?"

Takes: current market data dict, previous cycle snapshot dict.
Returns: delta dict with change percentages and anomaly flags.
Why: raw market data doesn't tell you if something CHANGED — you need the
comparison. A pool with 45% APR is meaningless unless you know it was 60%
last cycle (that's a 25% drop — possibly worth rebalancing).
"""


def calculate_deltas(current_data: dict, previous_snapshot: dict) -> dict:
    """
    Calculate the changes between current and previous cycle data.
    Takes: current_data — this cycle's market data, previous_snapshot — last cycle's.
    Returns: dict with per-pool deltas and overall market movement.
    Why: the logic engine uses these deltas to decide if rebalancing is worth it.
    """
    # If there's no previous snapshot (first cycle), return empty deltas
    # The logic engine treats empty deltas as "nothing to compare, skip rebalancing"
    if not previous_snapshot:
        return {
            "pool_deltas": {},
            "has_previous": False,
            "anomalies": [],
        }

    # Build lookup of previous pools by their ID for fast comparison
    prev_pools = {}
    for pool in previous_snapshot.get("pools", []):
        pool_id = pool.get("pool_id", "")
        if pool_id:
            prev_pools[pool_id] = pool

    # Calculate deltas for each pool in the current data
    pool_deltas = {}
    for pool in current_data.get("pools", []):
        pool_id = pool.get("pool_id", "")

        # Skip if this pool wasn't in the previous snapshot (new pool)
        if pool_id not in prev_pools:
            continue

        prev = prev_pools[pool_id]

        # Calculate APY change (absolute and relative)
        current_apy = pool.get("apy", 0) or 0
        prev_apy = prev.get("apy", 0) or 0

        # Absolute change: how many percentage points did APY move?
        apy_change_abs = current_apy - prev_apy

        # Relative change: what % did APY change by? (avoids division by zero)
        if prev_apy > 0:
            apy_change_rel = ((current_apy - prev_apy) / prev_apy) * 100
        else:
            apy_change_rel = 0

        # Calculate TVL change
        current_tvl = pool.get("tvl_usd", 0) or 0
        prev_tvl = prev.get("tvl_usd", 0) or 0

        # Relative TVL change
        if prev_tvl > 0:
            tvl_change_rel = ((current_tvl - prev_tvl) / prev_tvl) * 100
        else:
            tvl_change_rel = 0

        # Store the delta for this pool
        pool_deltas[pool_id] = {
            "symbol": pool.get("symbol", ""),
            "apy_change_abs": apy_change_abs,
            "apy_change_rel": apy_change_rel,
            "tvl_change_rel": tvl_change_rel,
            "current_apy": current_apy,
            "prev_apy": prev_apy,
            "current_tvl": current_tvl,
            "prev_tvl": prev_tvl,
        }

    # Calculate price deltas if we have price data from both cycles
    price_deltas = {}
    current_prices = current_data.get("prices", {})
    prev_prices = previous_snapshot.get("prices", {})

    for symbol, current_price in current_prices.items():
        if symbol in prev_prices and prev_prices[symbol] > 0:
            # Calculate how much this token's price moved
            prev_price = prev_prices[symbol]
            change_pct = ((current_price - prev_price) / prev_price) * 100
            price_deltas[symbol] = change_pct

    return {
        "pool_deltas": pool_deltas,
        "price_deltas": price_deltas,
        "has_previous": True,
        "anomalies": detect_anomalies(pool_deltas, price_deltas),
    }


def detect_anomalies(pool_deltas: dict, price_deltas: dict) -> list:
    """
    Flag unusual market movements that might indicate danger or opportunity.
    Takes: pool_deltas — per-pool change data, price_deltas — per-token price changes.
    Returns: list of anomaly strings describing what was detected.
    Why: sudden drops in APR, TVL, or price can signal problems (rug pull, exploit,
    mass exit). The logic engine can use these to be more cautious.
    """
    anomalies = []

    # Check for APR crashes — more than 50% relative drop is suspicious
    for pool_id, delta in pool_deltas.items():
        symbol = delta.get("symbol", pool_id)

        # APR crashed — could mean rewards stopped or liquidity flooded in
        if delta["apy_change_rel"] < -50:
            anomalies.append(
                f"APR CRASH: {symbol} dropped {delta['apy_change_rel']:.1f}%"
            )

        # TVL crashed — could mean mass exit or exploit
        if delta["tvl_change_rel"] < -30:
            anomalies.append(
                f"TVL DROP: {symbol} lost {abs(delta['tvl_change_rel']):.1f}% liquidity"
            )

    # Check for sudden price spikes — more than 10% in one cycle is extreme
    for symbol, change in price_deltas.items():
        if abs(change) > 10:
            direction = "spiked" if change > 0 else "crashed"
            anomalies.append(
                f"PRICE MOVE: {symbol} {direction} {abs(change):.1f}%"
            )

    return anomalies


def detect_out_of_range(position: dict, current_tick: int) -> bool:
    """
    Check if an existing LP position is still within its price range.
    Takes: position — dict with tick_lower and tick_upper, current_tick — pool's current tick.
    Returns: True if OUT of range (bad — not earning fees), False if still in range (good).
    Why: V3 positions only earn fees when the price is inside their range. If it drifts
    out, the position is dead weight and should be rebalanced to a new range.
    """
    # Get the position's tick boundaries
    tick_lower = position.get("tick_lower", 0)
    tick_upper = position.get("tick_upper", 0)

    # The position is in range when: tick_lower <= current_tick < tick_upper
    # If the current tick is outside this range, the position earns nothing
    return current_tick < tick_lower or current_tick >= tick_upper


def calculate_il_estimate(entry_prices: dict, current_prices: dict) -> float:
    """
    Estimate impermanent loss based on price divergence between two tokens.
    Takes: entry_prices — prices when position was opened, current_prices — prices now.
    Returns: estimated IL as a negative percentage (e.g. -2.5 means 2.5% loss).
    Why: impermanent loss is the hidden cost of LP — if one token moves a lot relative
    to the other, you'd have been better off just holding. This estimate helps
    the logic engine weigh the cost of staying in a position.

    Uses the standard IL formula: IL = 2*sqrt(r)/(1+r) - 1
    where r = price_ratio_now / price_ratio_at_entry
    """
    # Need prices for both tokens to calculate the ratio
    if not entry_prices or not current_prices:
        return 0.0

    # Get the two token symbols from the entry prices
    tokens = list(entry_prices.keys())
    if len(tokens) < 2:
        return 0.0

    token_a, token_b = tokens[0], tokens[1]

    # Calculate the price ratio at entry and now
    # Guard against division by zero
    if entry_prices.get(token_b, 0) == 0 or current_prices.get(token_b, 0) == 0:
        return 0.0

    ratio_entry = entry_prices[token_a] / entry_prices[token_b]
    ratio_now = current_prices.get(token_a, 0) / current_prices.get(token_b, 1)

    # Calculate r = how much the ratio has changed
    if ratio_entry == 0:
        return 0.0

    r = ratio_now / ratio_entry

    # Apply the IL formula: IL = 2*sqrt(r)/(1+r) - 1
    # This gives a value between 0 (no IL) and -1 (100% IL, theoretically impossible)
    import math
    il = (2 * math.sqrt(r)) / (1 + r) - 1

    # Convert to percentage (multiply by 100)
    return il * 100
