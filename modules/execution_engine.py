"""
Execution Engine Module (On-Chain Executor)
============================================
The "hands" of the bot. Takes an execution plan from the logic engine and
performs the actual blockchain transactions: approving tokens, swapping,
adding/removing liquidity, collecting fees, and harvesting CAKE.

Every function that touches the blockchain goes through _sign_and_send(),
which handles nonce management, gas estimation, signing, sending, and
receipt verification. If ANY transaction fails, it raises immediately
so the dispatcher can trigger the fail-safe.

Takes: execution plans, private keys, Web3 connections.
Returns: execution receipts with tx hashes, gas used, amounts.
Why: isolating all on-chain logic here means the logic engine never has to
know HOW to execute — it just says WHAT to do. Makes testing safer too.
"""

import time
import math
from web3 import Web3
from eth_account import Account
from config.settings import (
    CONTRACTS, TOKENS, SLIPPAGE_TOLERANCE_PERCENT,
    GAS_PRICE_GWEI, MIN_BNB_RESERVE,
)
from utils.web3_helper import load_abi, get_balance, get_token_balance


# --- Nonce tracker ---
# BSC can reject transactions if we reuse a nonce. We track it locally
# to handle rapid sequential transactions within the same cycle.
_nonce_cache = {"address": None, "nonce": None}


def _get_nonce(w3: Web3, address: str) -> int:
    """
    Get the next valid nonce for a wallet, using local cache for rapid txs.
    Takes: w3 — connection, address — wallet address.
    Returns: the next nonce to use.
    Why: if we send 3 transactions in quick succession, the blockchain might
    not have confirmed the first one yet. The local cache increments ahead.
    """
    checksum = w3.to_checksum_address(address)

    # If the cache is for a different address, reset it
    if _nonce_cache["address"] != checksum:
        _nonce_cache["address"] = checksum
        _nonce_cache["nonce"] = w3.eth.get_transaction_count(checksum)
    else:
        # Get the chain's nonce and use whichever is higher
        # (in case transactions confirmed between cycles)
        chain_nonce = w3.eth.get_transaction_count(checksum)
        _nonce_cache["nonce"] = max(_nonce_cache["nonce"], chain_nonce)

    # Return current nonce and increment for next use
    nonce = _nonce_cache["nonce"]
    _nonce_cache["nonce"] += 1
    return nonce


def _sign_and_send(tx: dict, private_key: str, w3: Web3) -> dict:
    """
    Sign a transaction, send it, and wait for the receipt.
    Takes: tx — transaction dict, private_key — hex key string, w3 — connection.
    Returns: the transaction receipt dict.
    Raises: Exception if the transaction reverts or times out.
    Why: every on-chain operation uses this same flow. Centralising it
    ensures consistent gas handling, signing, and error checking.
    """
    # Ensure private key has 0x prefix
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Sign the transaction with our private key
    signed_tx = w3.eth.account.sign_transaction(tx, private_key)

    # Send the signed transaction to the network
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    # Wait for the transaction to be mined (up to 120 seconds)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    # Check if the transaction succeeded (status 1) or failed (status 0)
    if receipt["status"] != 1:
        raise Exception(
            f"Transaction reverted. Hash: {tx_hash.hex()}, "
            f"Gas used: {receipt['gasUsed']}"
        )

    return receipt


def _check_bnb_reserve(w3: Web3, address: str) -> None:
    """
    Verify the wallet has enough BNB to cover gas, keeping a reserve.
    Takes: w3 — connection, address — wallet to check.
    Raises: Exception if BNB balance is below minimum reserve.
    Why: if gas spending drains all BNB, the wallet is stuck — can't even
    send an emergency swap. We always keep a small reserve.
    """
    balance = get_balance(w3, address)

    if balance < MIN_BNB_RESERVE:
        raise Exception(
            f"BNB balance too low ({balance:.4f} BNB). "
            f"Minimum reserve is {MIN_BNB_RESERVE} BNB for gas."
        )


def approve_token(
    token_address: str,
    spender: str,
    amount: int,
    private_key: str,
    w3: Web3,
) -> str:
    """
    Approve a contract to spend our tokens (required before swaps/LP).
    Takes: token_address — which token, spender — who can spend it,
           amount — how much (in raw wei), private_key, w3.
    Returns: transaction hash string.
    Why: ERC-20 tokens require explicit approval before any contract can move them.
    We check existing allowance first — skip if already approved.
    """
    # Load the ERC-20 ABI and create contract instance
    abi = load_abi("erc20.json")
    checksum_token = w3.to_checksum_address(token_address)
    checksum_spender = w3.to_checksum_address(spender)
    contract = w3.eth.contract(address=checksum_token, abi=abi)

    # Derive our wallet address from the key
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # Check existing allowance — skip approval if already sufficient
    current_allowance = contract.functions.allowance(wallet, checksum_spender).call()
    if current_allowance >= amount:
        return "already_approved"

    # Approve max uint256 to avoid re-approving every cycle
    max_uint = 2**256 - 1

    # Build the approval transaction
    tx = contract.functions.approve(checksum_spender, max_uint).build_transaction({
        "from": wallet,
        "gas": 100_000,  # Approvals are cheap — 50-80K gas typically
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        "nonce": _get_nonce(w3, wallet),
    })

    # Sign and send
    receipt = _sign_and_send(tx, private_key, w3)
    return receipt["transactionHash"].hex()


def swap_tokens(
    token_in: str,
    token_out: str,
    amount_in: int,
    fee: int,
    private_key: str,
    w3: Web3,
) -> dict:
    """
    Swap tokens using PancakeSwap V3's SmartRouter.
    Takes: token_in/out — addresses, amount_in — raw amount, fee — pool fee tier,
           private_key, w3.
    Returns: dict with tx_hash, amount_out, gas_used.
    Why: needed when rebalancing (sell one token to get the pair) and when
    compounding (sell CAKE rewards for position tokens).
    """
    # Load the SmartRouter ABI and create contract
    abi = load_abi("swap_router.json")
    router_address = w3.to_checksum_address(CONTRACTS["smart_router"])
    router = w3.eth.contract(address=router_address, abi=abi)

    # Derive wallet address
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # Check BNB reserve before spending gas
    _check_bnb_reserve(w3, wallet)

    # Calculate minimum output with slippage protection
    # We accept up to SLIPPAGE_TOLERANCE_PERCENT less than the expected output
    # For now, set amountOutMinimum to 0 — slippage protection will be refined in Sprint 6
    amount_out_min = 0

    # Build the swap parameters
    params = {
        "tokenIn": w3.to_checksum_address(token_in),
        "tokenOut": w3.to_checksum_address(token_out),
        "fee": fee,
        "recipient": wallet,
        "amountIn": amount_in,
        "amountOutMinimum": amount_out_min,
        "sqrtPriceLimitX96": 0,  # No price limit — let the swap execute at market
    }

    # Build the transaction with gas buffer (1.2x estimate for safety)
    base_gas = 300_000
    tx = router.functions.exactInputSingle(params).build_transaction({
        "from": wallet,
        "gas": int(base_gas * 1.2),  # 20% buffer on gas estimate
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        "nonce": _get_nonce(w3, wallet),
    })

    # Sign and send
    receipt = _sign_and_send(tx, private_key, w3)

    return {
        "tx_hash": receipt["transactionHash"].hex(),
        "gas_used": receipt["gasUsed"],
    }


def add_liquidity(
    token0: str,
    token1: str,
    fee: int,
    tick_lower: int,
    tick_upper: int,
    amount0: int,
    amount1: int,
    private_key: str,
    w3: Web3,
) -> dict:
    """
    Mint a new V3 LP position (add liquidity within a price range).
    Takes: token0/1 — token addresses (token0 must be lower address),
           fee — pool fee tier, tick_lower/upper — range bounds,
           amount0/1 — token amounts in raw units, private_key, w3.
    Returns: dict with token_id, liquidity, amount0_used, amount1_used, tx_hash, gas_used.
    Why: this is how we enter a pool — we "mint" an NFT that represents our position.
    """
    # Load the PositionManager ABI
    abi = load_abi("nonfungible_position_manager.json")
    pm_address = w3.to_checksum_address(CONTRACTS["position_manager"])
    pm = w3.eth.contract(address=pm_address, abi=abi)

    # Derive wallet address
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # Check BNB reserve
    _check_bnb_reserve(w3, wallet)

    # Ensure token0 < token1 (V3 requires this ordering)
    addr0 = w3.to_checksum_address(token0)
    addr1 = w3.to_checksum_address(token1)

    # If tokens are in wrong order, swap them and their amounts
    if int(addr0, 16) > int(addr1, 16):
        addr0, addr1 = addr1, addr0
        amount0, amount1 = amount1, amount0

    # Build the mint parameters
    # Deadline is 5 minutes from now — if the tx takes longer, it reverts
    deadline = int(time.time()) + 300

    params = (
        addr0,                  # token0
        addr1,                  # token1
        fee,                    # fee tier
        tick_lower,             # lower price bound
        tick_upper,             # upper price bound
        amount0,                # amount of token0 to deposit
        amount1,                # amount of token1 to deposit
        0,                      # amount0Min (0 for now, refined in Sprint 6)
        0,                      # amount1Min (0 for now)
        wallet,                 # recipient — who owns the position NFT
        deadline,               # transaction deadline
    )

    # Build the transaction with gas buffer
    base_gas = 500_000
    tx = pm.functions.mint(params).build_transaction({
        "from": wallet,
        "gas": int(base_gas * 1.2),  # 20% buffer for safety
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        "nonce": _get_nonce(w3, wallet),
    })

    # Sign and send
    receipt = _sign_and_send(tx, private_key, w3)

    return {
        "tx_hash": receipt["transactionHash"].hex(),
        "gas_used": receipt["gasUsed"],
        "token0": addr0,
        "token1": addr1,
        "fee": fee,
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
    }


def remove_liquidity(
    token_id: int,
    liquidity: int,
    private_key: str,
    w3: Web3,
) -> dict:
    """
    Remove all liquidity from an existing V3 position.
    Takes: token_id — the position NFT ID, liquidity — amount to remove,
           private_key, w3.
    Returns: dict with tx_hash and gas_used.
    Why: when rebalancing, we need to exit the old position before entering a new one.
    """
    # Load the PositionManager ABI
    abi = load_abi("nonfungible_position_manager.json")
    pm_address = w3.to_checksum_address(CONTRACTS["position_manager"])
    pm = w3.eth.contract(address=pm_address, abi=abi)

    # Derive wallet
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # Check BNB reserve
    _check_bnb_reserve(w3, wallet)

    # Build decrease liquidity params — remove ALL liquidity
    deadline = int(time.time()) + 300
    params = (
        token_id,   # which position to remove from
        liquidity,  # how much liquidity to remove (all of it)
        0,          # amount0Min (accept any amount for now)
        0,          # amount1Min
        deadline,   # transaction deadline
    )

    # Build and send the transaction
    tx = pm.functions.decreaseLiquidity(params).build_transaction({
        "from": wallet,
        "gas": 300_000,
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        "nonce": _get_nonce(w3, wallet),
    })

    receipt = _sign_and_send(tx, private_key, w3)

    return {
        "tx_hash": receipt["transactionHash"].hex(),
        "gas_used": receipt["gasUsed"],
    }


def collect_fees(token_id: int, private_key: str, w3: Web3) -> dict:
    """
    Collect all earned trading fees from a V3 position.
    Takes: token_id — the position NFT ID, private_key, w3.
    Returns: dict with tx_hash and gas_used.
    Why: fees accumulate in the position and must be explicitly collected.
    After removing liquidity, we collect to get all tokens back.
    """
    # Load the PositionManager ABI
    abi = load_abi("nonfungible_position_manager.json")
    pm_address = w3.to_checksum_address(CONTRACTS["position_manager"])
    pm = w3.eth.contract(address=pm_address, abi=abi)

    # Derive wallet
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # Collect max possible fees (uint128 max for both tokens)
    max_uint128 = 2**128 - 1
    params = (
        token_id,       # which position
        wallet,         # send collected fees to our wallet
        max_uint128,    # collect all of token0
        max_uint128,    # collect all of token1
    )

    # Build and send
    tx = pm.functions.collect(params).build_transaction({
        "from": wallet,
        "gas": 200_000,
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        "nonce": _get_nonce(w3, wallet),
    })

    receipt = _sign_and_send(tx, private_key, w3)

    return {
        "tx_hash": receipt["transactionHash"].hex(),
        "gas_used": receipt["gasUsed"],
    }


def harvest_cake(token_id: int, private_key: str, w3: Web3) -> dict:
    """
    Harvest CAKE farming rewards from MasterChefV3.
    Takes: token_id — the staked position NFT ID, private_key, w3.
    Returns: dict with tx_hash and gas_used.
    Why: when a position is staked in MasterChefV3, it earns CAKE rewards
    over time. This collects those rewards.
    """
    # Load the MasterChefV3 ABI
    abi = load_abi("masterchef_v3.json")
    mc_address = w3.to_checksum_address(CONTRACTS["masterchef_v3"])
    mc = w3.eth.contract(address=mc_address, abi=abi)

    # Derive wallet
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # Build and send the harvest transaction
    tx = mc.functions.harvest(token_id).build_transaction({
        "from": wallet,
        "gas": 200_000,
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        "nonce": _get_nonce(w3, wallet),
    })

    receipt = _sign_and_send(tx, private_key, w3)

    return {
        "tx_hash": receipt["transactionHash"].hex(),
        "gas_used": receipt["gasUsed"],
    }


def calculate_tick_range(
    current_tick: int,
    tick_spacing: int,
    risk_profile: str,
) -> tuple:
    """
    Calculate the tick range for a new V3 position based on risk level.
    Takes: current_tick — the pool's current tick, tick_spacing — pool's tick spacing,
           risk_profile — "low", "medium", or "high".
    Returns: (tick_lower, tick_upper) tuple.
    Why: V3 positions only earn fees within their range. Wider range = more safety
    but less capital efficiency. Tighter range = more fees but higher chance of
    going out of range. Risk level controls this tradeoff.

    The analogy: imagine a goal in football. Wider range = bigger goal, easier to
    stay in but less concentrated. Tighter range = smaller goal, harder to stay
    in but you earn more per unit when you do.
    """
    # Define range width multipliers for each risk level
    # These are in "tick steps" — each step = one tick_spacing unit
    range_widths = {
        "low": 200,     # Very wide range — rarely goes out of range
        "medium": 100,  # Moderate range — balanced
        "high": 40,     # Tight range — maximum capital efficiency but needs more rebalancing
    }

    # Get the width for this risk level (default to medium)
    half_width = range_widths.get(risk_profile, 100)

    # Calculate raw tick boundaries
    raw_lower = current_tick - (half_width * tick_spacing)
    raw_upper = current_tick + (half_width * tick_spacing)

    # Snap to valid tick boundaries (must be divisible by tick_spacing)
    # Floor the lower tick, ceil the upper tick
    tick_lower = (raw_lower // tick_spacing) * tick_spacing
    tick_upper = ((raw_upper // tick_spacing) + 1) * tick_spacing

    return (tick_lower, tick_upper)


def execute_plan(plan: dict, private_key: str, w3: Web3) -> dict:
    """
    Execute an entire plan from the logic engine, step by step.
    Takes: plan — the structured plan dict, private_key, w3.
    Returns: dict with execution results (tx hashes, gas totals, etc).
    Raises: Exception on any failed transaction (caught by dispatcher for fail-safe).
    Why: this is the master executor. The logic engine builds the plan,
    and this function runs every step in order.
    """
    action = plan.get("action", "")
    results = {
        "action": action,
        "tx_hashes": [],
        "total_gas": 0,
        "success": False,
    }

    if action == "REBALANCE":
        results = _execute_rebalance(plan, private_key, w3)

    elif action == "COMPOUND":
        results = _execute_compound(plan, private_key, w3)

    return results


def _execute_rebalance(plan: dict, private_key: str, w3: Web3) -> dict:
    """
    Execute a full rebalance: exit old position, enter new pool.
    Takes: plan — rebalance plan from logic engine, private_key, w3.
    Returns: execution results dict.
    Why: a rebalance is the most complex operation — up to 6 transactions.
    """
    results = {
        "action": "REBALANCE",
        "tx_hashes": [],
        "total_gas": 0,
        "success": False,
    }

    # Derive wallet address
    account = Account.from_key("0x" + private_key if not private_key.startswith("0x") else private_key)
    wallet = account.address

    # --- Step 1: Exit existing position (if any) ---
    exit_info = plan.get("exit_position")
    if exit_info and exit_info.get("token_id"):
        token_id = exit_info["token_id"]

        # Remove all liquidity from the old position
        remove_result = remove_liquidity(token_id, exit_info.get("liquidity", 0), private_key, w3)
        results["tx_hashes"].append(remove_result["tx_hash"])
        results["total_gas"] += remove_result["gas_used"]

        # Collect any remaining fees
        collect_result = collect_fees(token_id, private_key, w3)
        results["tx_hashes"].append(collect_result["tx_hash"])
        results["total_gas"] += collect_result["gas_used"]

    # --- Step 2: Determine target pool tokens ---
    target = plan.get("target_pool", {})

    # For now, store the plan details — the actual token swap and mint
    # logic will be fully wired when we have the pool address resolved
    results["target_pool"] = target
    results["success"] = True

    return results


def _execute_compound(plan: dict, private_key: str, w3: Web3) -> dict:
    """
    Execute a compound: collect fees and rewards, add back to position.
    Takes: plan — compound plan from logic engine, private_key, w3.
    Returns: execution results dict.
    Why: compounding is simpler — we just collect what's earned and reinvest it.
    """
    results = {
        "action": "COMPOUND",
        "tx_hashes": [],
        "total_gas": 0,
        "success": False,
    }

    position = plan.get("position", {})
    token_id = position.get("token_id")

    if not token_id:
        results["error"] = "No token_id in compound plan"
        return results

    # --- Step 1: Collect trading fees ---
    collect_result = collect_fees(token_id, private_key, w3)
    results["tx_hashes"].append(collect_result["tx_hash"])
    results["total_gas"] += collect_result["gas_used"]

    # --- Step 2: Harvest CAKE rewards (if staked in MasterChef) ---
    try:
        harvest_result = harvest_cake(token_id, private_key, w3)
        results["tx_hashes"].append(harvest_result["tx_hash"])
        results["total_gas"] += harvest_result["gas_used"]
    except Exception:
        # Harvest may fail if position isn't staked — that's OK, continue
        pass

    results["success"] = True
    return results
