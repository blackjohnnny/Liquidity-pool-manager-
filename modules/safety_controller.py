"""
Safety Controller Module (Fail-Safe)
======================================
The last line of defence. When a critical error occurs during execution,
this module takes over to protect the user's capital:

  1. HALT all processing immediately
  2. CONVERT all assets to stablecoin (emergency swap)
  3. LOCK the system (safetyLock = True) so no more cycles run
  4. NOTIFY the user with full error details
  5. EXIT safely

Think of this like a circuit breaker in an electrical system — when something
goes dangerously wrong, it trips and cuts the power to prevent damage.
The user has to manually flip it back on.

Takes: error details, private key, Web3 connection, bot app.
Returns: nothing (side effects: on-chain emergency swaps, state lock, notification).
Why: the bot manages real money autonomously. Without a fail-safe, a bug in
one cycle could cascade into total loss of funds.
"""

import logging
from eth_account import Account
from web3 import Web3
from config.settings import CONTRACTS, TOKENS, GAS_PRICE_GWEI
from utils.web3_helper import get_balance, get_token_balance, load_abi
from utils.state_store import load_state, save_state
from modules.config_manager import set_safety_lock
from modules.notifier import send_safety_alert

logger = logging.getLogger(__name__)

# --- USDT is our safe haven — the token we convert everything to ---
SAFE_TOKEN = TOKENS["USDT"]


def trigger_failsafe(error, private_key: str, w3: Web3, user_data: dict, bot_app) -> None:
    """
    Execute the full fail-safe procedure.
    Takes: error — the error that triggered this, private_key — wallet key,
           w3 — blockchain connection, user_data — contains chat_id, bot_app.
    Returns: nothing.
    Why: this is the EMERGENCY function. Called when something goes critically wrong
    during a cycle. Must be as robust as possible — if even the fail-safe fails,
    the user needs to be told.
    """
    logger.critical(f"FAIL-SAFE TRIGGERED: {error}")

    error_details = str(error)

    # --- Step 1: Attempt to emergency-swap all assets to stablecoin ---
    swap_success = False
    try:
        if private_key and w3:
            swap_result = emergency_swap_to_stable(private_key, w3)
            swap_success = swap_result.get("success", False)

            if swap_success:
                logger.info("Emergency swap completed successfully")
            else:
                logger.warning("Emergency swap partially failed")
                error_details += f"\nEmergency swap: {swap_result.get('errors', [])}"
    except Exception as swap_error:
        # Even the emergency swap failed — log it but keep going
        logger.error(f"Emergency swap failed entirely: {swap_error}")
        error_details += f"\nEmergency swap FAILED: {swap_error}"

    # --- Step 2: Set the safety lock ---
    # This prevents any further cycles from running
    set_safety_lock(True)
    logger.info("Safety lock ACTIVATED")

    # --- Step 3: Notify the user ---
    chat_id = user_data.get("chat_id")
    if chat_id and bot_app:
        try:
            send_safety_alert(bot_app, chat_id, error_details)
        except Exception as notify_error:
            # If we can't even notify, log it — there's nothing else we can do
            logger.error(f"Failed to send safety alert: {notify_error}")


def emergency_swap_to_stable(private_key: str, w3: Web3) -> dict:
    """
    Swap all non-stablecoin tokens to USDT to protect capital.
    Takes: private_key — wallet key, w3 — blockchain connection.
    Returns: dict with success flag and list of errors.
    Why: when the bot detects a critical error, the safest move is to
    convert everything to a stable asset. Even if the market is crashing,
    at least the value is preserved in USDT.
    """
    # Derive the wallet address from the private key
    key = "0x" + private_key if not private_key.startswith("0x") else private_key
    account = Account.from_key(key)
    wallet = account.address

    # Track results
    result = {"success": True, "swaps": [], "errors": []}

    # --- Tokens to check and potentially swap ---
    # We skip USDT, USDC, BUSD since they're already stable
    tokens_to_swap = {
        "WBNB": TOKENS["WBNB"],
        "CAKE": TOKENS["CAKE"],
        "ETH": TOKENS["ETH"],
        "BTCB": TOKENS["BTCB"],
    }

    # Load the swap router ABI and contract
    router_abi = load_abi("swap_router.json")
    router_address = w3.to_checksum_address(CONTRACTS["smart_router"])
    router = w3.eth.contract(address=router_address, abi=router_abi)

    # Load ERC-20 ABI for approvals
    erc20_abi = load_abi("erc20.json")

    for symbol, token_address in tokens_to_swap.items():
        try:
            # Check our balance of this token
            balance = get_token_balance(w3, wallet, token_address)

            # Skip if balance is negligible (less than $1 worth)
            if balance <= 0:
                continue

            # Get the raw balance in wei (need to read decimals)
            token_contract = w3.eth.contract(
                address=w3.to_checksum_address(token_address),
                abi=erc20_abi,
            )
            decimals = token_contract.functions.decimals().call()
            raw_balance = token_contract.functions.balanceOf(
                w3.to_checksum_address(wallet)
            ).call()

            # Skip dust amounts (less than 100 units of the smallest denomination)
            if raw_balance < 100:
                continue

            # --- Approve the router to spend our tokens ---
            allowance = token_contract.functions.allowance(
                w3.to_checksum_address(wallet),
                router_address,
            ).call()

            if allowance < raw_balance:
                # Approve max amount
                approve_tx = token_contract.functions.approve(
                    router_address, 2**256 - 1
                ).build_transaction({
                    "from": wallet,
                    "gas": 100_000,
                    "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
                    "nonce": w3.eth.get_transaction_count(wallet),
                })

                signed = w3.eth.account.sign_transaction(approve_tx, key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            # --- Swap to USDT ---
            # Use a common fee tier (2500 = 0.25%)
            swap_params = {
                "tokenIn": w3.to_checksum_address(token_address),
                "tokenOut": w3.to_checksum_address(SAFE_TOKEN),
                "fee": 2500,
                "recipient": w3.to_checksum_address(wallet),
                "amountIn": raw_balance,
                "amountOutMinimum": 0,  # Emergency = accept any price
                "sqrtPriceLimitX96": 0,
            }

            swap_tx = router.functions.exactInputSingle(swap_params).build_transaction({
                "from": wallet,
                "gas": 300_000,
                "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
                "nonce": w3.eth.get_transaction_count(wallet),
            })

            signed = w3.eth.account.sign_transaction(swap_tx, key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            # Check if the swap succeeded
            if receipt["status"] == 1:
                result["swaps"].append(f"{symbol}: swapped {balance:.4f} to USDT")
                logger.info(f"Emergency swap {symbol} -> USDT: success")
            else:
                result["errors"].append(f"{symbol}: swap reverted")
                result["success"] = False

        except Exception as e:
            # Log the error but keep trying other tokens
            error_msg = f"{symbol}: {str(e)}"
            result["errors"].append(error_msg)
            result["success"] = False
            logger.error(f"Emergency swap {symbol} failed: {e}")

    return result


def is_critical_error(error: Exception) -> bool:
    """
    Classify whether an error is critical (needs fail-safe) or recoverable.
    Takes: error — the exception that occurred.
    Returns: True if critical (trigger fail-safe), False if recoverable (just skip cycle).
    Why: not every error needs an emergency response. A timeout is recoverable
    (try again next cycle). A failed transaction is critical (money at risk).
    """
    # Get the error type name for classification
    error_type = type(error).__name__
    error_msg = str(error).lower()

    # --- Critical errors: money is at risk ---
    critical_types = {
        "ContractLogicError",     # Smart contract reverted — something broke
        "TransactionNotFound",     # Transaction disappeared — very bad
        "InsufficientFunds",       # Wallet is empty
    }

    if error_type in critical_types:
        return True

    # Check error message for critical keywords
    critical_keywords = [
        "reverted",               # Contract rejected our transaction
        "execution reverted",     # Same but different format
        "insufficient funds",     # Can't pay for gas
        "nonce too low",          # Transaction ordering broken
        "replacement transaction", # Nonce conflict
    ]

    for keyword in critical_keywords:
        if keyword in error_msg:
            return True

    # --- Recoverable errors: just skip this cycle ---
    # Connection timeouts, API failures, rate limits — all recoverable
    recoverable_types = {
        "ConnectionError",
        "TimeoutError",
        "HTTPError",
        "RequestException",
    }

    if error_type in recoverable_types:
        return False

    # Default: treat unknown errors as critical (better safe than sorry)
    return True
