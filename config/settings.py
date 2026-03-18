"""
Settings Module
===============
Loads environment variables from .env and defines all project-wide constants:
contract addresses, token addresses, risk classification sets, and defaults.

This is the single source of truth for configuration — every other module
imports from here instead of reading .env directly.
"""

import os
from dotenv import load_dotenv

# --- Load the .env file so os.environ picks up our config ---
load_dotenv()


# ============================================================
# TELEGRAM CONFIG
# ============================================================

# The bot token from @BotFather — required to run the bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ============================================================
# BLOCKCHAIN CONFIG
# ============================================================

# RPC endpoint for BSC — this is how we talk to the blockchain
# Default is BSC mainnet; swap to testnet URL in .env if needed
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")

# ============================================================
# BOT BEHAVIOUR DEFAULTS
# ============================================================

# How often the bot runs its check cycle (in seconds)
CYCLE_INTERVAL_SECONDS = int(os.getenv("CYCLE_INTERVAL_SECONDS", "15"))

# Minimum score difference before the bot will rebalance to a new pool
# e.g. 5.0 means the new pool must score 5% higher than the current one
REBALANCE_THRESHOLD_PERCENT = float(os.getenv("REBALANCE_THRESHOLD_PERCENT", "5.0"))

# Max acceptable price movement during a swap (protection against front-running)
SLIPPAGE_TOLERANCE_PERCENT = float(os.getenv("SLIPPAGE_TOLERANCE_PERCENT", "0.5"))

# Gas price in Gwei — BSC is cheap, 5 Gwei is typical
GAS_PRICE_GWEI = int(os.getenv("GAS_PRICE_GWEI", "5"))

# Never let gas spending drain the wallet below this BNB amount
MIN_BNB_RESERVE = 0.01

# ============================================================
# PANCAKESWAP V3 CONTRACT ADDRESSES (BSC MAINNET)
# ============================================================
# These are the verified, deployed PancakeSwap V3 contracts.
# We interact with these to manage LP positions.

CONTRACTS = {
    # Creates and tracks all V3 pool contracts
    "v3_factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",

    # Mint/burn/collect LP positions — positions are NFTs in V3
    "position_manager": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",

    # Token swaps with optimal routing
    "smart_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",

    # CAKE farming — stake LP NFTs here to earn CAKE rewards
    "masterchef_v3": "0x556B9306565093C855AEA9AE92A594704c2Cd59e",
}

# ============================================================
# TOKEN ADDRESSES (BSC MAINNET)
# ============================================================
# Commonly used tokens on BSC that we need for swaps, LP, and classification.

TOKENS = {
    "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
    "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
    "ETH":  "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "BTCB": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
}

# ============================================================
# RISK CLASSIFICATION SETS
# ============================================================
# Used by the config_manager to categorise LP pairs into risk tiers.
# A pool's risk = f(what tokens are in it).

# Stablecoins — pegged to $1, minimal volatility
STABLECOINS = {
    TOKENS["USDT"].lower(),
    TOKENS["USDC"].lower(),
    TOKENS["BUSD"].lower(),
}

# Large-cap tokens — established, liquid, but still volatile
LARGE_CAPS = {
    TOKENS["WBNB"].lower(),
    TOKENS["ETH"].lower(),
    TOKENS["BTCB"].lower(),
    TOKENS["CAKE"].lower(),
}

# ============================================================
# FILE PATHS
# ============================================================

# Where we persist cycle state, PnL, config between restarts
STATE_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "state.json")

# Directory containing smart contract ABIs
ABI_DIR = os.path.join(os.path.dirname(__file__), "abi")


def get_setting(key: str, default: str = "") -> str:
    """
    Read a setting from environment variables with a fallback default.
    Takes: key (str) — the env var name, default (str) — fallback value.
    Returns: the value as a string.
    Why: centralises all env reads so we never call os.getenv scattered across files.
    """
    return os.getenv(key, default)
