"""
PnL & State Analytics Module
==============================
Tracks profit and loss across every cycle: how much was earned, how much
gas was spent, and the net result. Also records cycle snapshots so the
comparator can see what changed.

Think of this like the bot's accounting department — every time money moves,
this module records it. The user sees the results in /update.

Takes: execution results, market data, state.
Returns: updated PnL dicts.
Why: without tracking, the user has no idea if the bot is making or losing money.
"""

import time
from utils.state_store import load_state, save_state
from utils.web3_helper import get_balance, get_token_balance
from config.settings import TOKENS


def update_pnl(execution_result: dict, market_data: dict, w3, wallet_address: str) -> dict:
    """
    Calculate and record PnL for the current cycle.
    Takes: execution_result — what happened on-chain, market_data — current prices,
           w3 — blockchain connection, wallet_address — the user's wallet.
    Returns: updated PnL dict with cycle_pnl, total_pnl, total_gas_spent.
    Why: called by the dispatcher after every cycle that involved a transaction.
    """
    # Load current state to get previous PnL totals
    state = load_state()
    pnl = state.get("pnl", {"cycle_pnl": 0.0, "total_pnl": 0.0, "total_gas_spent": 0.0})

    # Get current token prices for USD conversion
    prices = market_data.get("prices", {})
    bnb_price = prices.get("BNB", 600)

    # --- Calculate gas cost for this cycle ---
    gas_used = 0
    if execution_result:
        gas_used = execution_result.get("total_gas", 0)

    # Convert gas used to BNB cost (gas_used * gas_price_in_wei / 10^18)
    # Using 5 Gwei as typical BSC gas price
    gas_cost_bnb = (gas_used * 5 * 10**9) / 10**18

    # Convert BNB cost to USD
    gas_cost_usd = gas_cost_bnb * bnb_price

    # --- Calculate portfolio value ---
    # Get the total USD value of the wallet right now
    current_value = get_portfolio_value(w3, wallet_address, prices)

    # Get the previous portfolio value from state (if available)
    prev_value = state.get("portfolio_value", current_value)

    # Cycle PnL = change in portfolio value minus gas costs
    cycle_pnl = current_value - prev_value - gas_cost_usd

    # Update the running totals
    pnl["cycle_pnl"] = round(cycle_pnl, 4)
    pnl["total_pnl"] = round(pnl.get("total_pnl", 0) + cycle_pnl, 4)
    pnl["total_gas_spent"] = round(pnl.get("total_gas_spent", 0) + gas_cost_usd, 4)

    # Save the current portfolio value for next cycle's comparison
    state["pnl"] = pnl
    state["portfolio_value"] = current_value
    save_state(state)

    return pnl


def get_portfolio_value(w3, wallet_address: str, prices: dict) -> float:
    """
    Calculate the total USD value of all known tokens in the wallet.
    Takes: w3 — connection, wallet_address — wallet to check, prices — token USD prices.
    Returns: total value in USD.
    Why: used to calculate PnL by comparing portfolio value between cycles.
    """
    total_usd = 0.0

    # --- BNB balance ---
    try:
        bnb_balance = get_balance(w3, wallet_address)
        bnb_price = prices.get("BNB", 0)
        total_usd += bnb_balance * bnb_price
    except Exception:
        pass  # If balance fetch fails, skip this token

    # --- ERC-20 token balances ---
    # Check all the major tokens we track
    token_price_map = {
        "USDT": prices.get("USDT", 1.0),
        "USDC": prices.get("USDC", 1.0),
        "BUSD": prices.get("BUSD", 1.0),
        "CAKE": prices.get("CAKE", 0),
        "ETH": prices.get("ETH", 0),
        "BTCB": prices.get("BTC", 0),
    }

    for symbol, usd_price in token_price_map.items():
        # Skip tokens with no price data
        if usd_price <= 0:
            continue

        # Get the token address from our settings
        token_address = TOKENS.get(symbol)
        if not token_address:
            continue

        try:
            # Read the balance from the blockchain
            balance = get_token_balance(w3, wallet_address, token_address)

            # Add to total (balance * price per token)
            total_usd += balance * usd_price
        except Exception:
            pass  # If one token fails, keep checking the others

    return round(total_usd, 2)


def get_pnl_summary() -> dict:
    """
    Get the current PnL summary from state.
    Takes: nothing.
    Returns: dict with cycle_pnl, total_pnl, total_gas_spent.
    Why: called by /update to display PnL numbers to the user.
    """
    state = load_state()
    return state.get("pnl", {
        "cycle_pnl": 0.0,
        "total_pnl": 0.0,
        "total_gas_spent": 0.0,
    })


def record_cycle_snapshot(market_data: dict, state: dict) -> None:
    """
    Save the current cycle's market data as the previous_cycle snapshot.
    Takes: market_data — this cycle's data, state — the full state dict.
    Returns: nothing (modifies state dict in place, caller saves it).
    Why: the comparator needs last cycle's data to calculate deltas.
    """
    # Store a trimmed version of the market data (pools + prices + timestamp)
    state["previous_cycle"] = {
        "pools": market_data.get("pools", []),
        "prices": market_data.get("prices", {}),
        "timestamp": market_data.get("timestamp", time.time()),
    }
