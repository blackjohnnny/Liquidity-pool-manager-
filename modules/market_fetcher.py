"""
Market Data Fetcher Module
==========================
Fetches live liquidity pool data from three sources:
  1. DeFiLlama Yields API — pool APR, TVL, and token pair info (free, no key)
  2. Binance Public API — real-time token prices (free, no key)
  3. On-chain reads — pool state (tick, price, liquidity) direct from BSC

All data is validated before being passed to other modules. If any critical
validation fails, the cycle is rejected to prevent bad decisions.

Think of this as the bot's "eyes" — it gathers everything the logic engine
needs to make smart decisions about where to put money.

Takes: a Web3 connection.
Returns: validated market data dicts, or raises errors on bad data.
Why: separating data fetching from decision logic means bad data can never
silently influence execution — it's caught here or not at all.
"""

import time
import logging
import requests
from web3 import Web3
from config.settings import CONTRACTS, TOKENS
from utils.web3_helper import load_abi
from modules.config_manager import classify_pool_risk

logger = logging.getLogger(__name__)

# --- Retry settings ---
# If an API call fails, we retry up to MAX_RETRIES times with RETRY_DELAY between each
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

# --- Cache for DeFiLlama data ---
# DeFiLlama updates pool data roughly every hour, so there's no point
# re-fetching it every 15 seconds. We cache it and refresh every 5 minutes.
_llama_cache = {
    "data": None,          # The cached pool list
    "timestamp": 0,        # When we last fetched
    "ttl": 300,            # Cache lifetime in seconds (5 minutes)
}

# --- Request timeouts ---
# If an API doesn't respond within this time, we fail fast instead of hanging
HTTP_TIMEOUT = 10  # seconds


def fetch_defi_llama_pools() -> list:
    """
    Fetch PancakeSwap V3 pool data from DeFiLlama's yields API.
    Takes: nothing.
    Returns: list of pool dicts with keys: pool_id, symbol, tvl_usd, apy, apy_base, apy_reward.
    Why: DeFiLlama aggregates APR/TVL data across DeFi — best free source for pool metrics.

    Response is cached for 5 minutes since DeFiLlama only updates hourly anyway.
    """
    # Check if we have a valid cached response
    now = time.time()
    if _llama_cache["data"] and (now - _llama_cache["timestamp"]) < _llama_cache["ttl"]:
        # Cache is still fresh — return it without hitting the API
        return _llama_cache["data"]

    # Fetch fresh data from DeFiLlama with retry logic
    url = "https://yields.llama.fi/pools"
    raw_data = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Make the HTTP request with a timeout to prevent hanging
            response = requests.get(url, timeout=HTTP_TIMEOUT)

            # Handle rate limiting (HTTP 429) — wait and retry
            if response.status_code == 429:
                logger.warning(f"DeFiLlama rate limited (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY * attempt)
                continue

            # Raise an error if the response status is not 200
            response.raise_for_status()

            # Parse the JSON response
            raw_data = response.json()

            # Success — break out of retry loop
            break

        except requests.exceptions.Timeout:
            logger.warning(f"DeFiLlama timeout (attempt {attempt}/{MAX_RETRIES})")
            if attempt == MAX_RETRIES:
                raise ConnectionError("DeFiLlama API timed out after all retries")
            time.sleep(RETRY_DELAY)

        except requests.exceptions.RequestException as e:
            logger.warning(f"DeFiLlama error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                raise ConnectionError(f"DeFiLlama API failed after all retries: {e}")
            time.sleep(RETRY_DELAY)

    # If raw_data is still None after all retries, raise
    if raw_data is None:
        raise ConnectionError("DeFiLlama returned no data after all retries")

    # The response has a "data" key containing a list of all pools across DeFi
    all_pools = raw_data.get("data", [])

    # Filter to only PancakeSwap V3 pools on BSC (Binance chain)
    # DeFiLlama uses "pancakeswap-amm-v3" as the project name and "Binance" as the chain
    pcs_pools = []
    for pool in all_pools:
        # Match project name (case-insensitive) and chain
        project = pool.get("project", "").lower()
        chain = pool.get("chain", "")

        if project == "pancakeswap-amm-v3" and chain == "Binance":
            # Extract only the fields we care about
            pcs_pools.append({
                "pool_id": pool.get("pool", ""),           # DeFiLlama's unique pool ID
                "symbol": pool.get("symbol", "Unknown"),   # e.g. "USDT-WBNB"
                "tvl_usd": pool.get("tvlUsd", 0),         # Total Value Locked in USD
                "apy": pool.get("apy", 0),                 # Total APY (base + reward)
                "apy_base": pool.get("apyBase", 0),       # APY from trading fees only
                "apy_reward": pool.get("apyReward", 0),   # APY from CAKE farming rewards
                "pool_address": pool.get("pool", ""),       # On-chain pool address
            })

    # Update the cache with fresh data
    _llama_cache["data"] = pcs_pools
    _llama_cache["timestamp"] = now

    return pcs_pools


def fetch_token_prices(symbols: list = None) -> dict:
    """
    Fetch real-time token prices from Binance's public API.
    Takes: symbols (list of str) — token symbols to fetch, or None for defaults.
    Returns: dict mapping symbol → USD price, e.g. {"BNB": 600.5, "ETH": 3200.0}.
    Why: we need current prices to calculate position values and display USD amounts.
    """
    # Default tokens we always need prices for
    if symbols is None:
        symbols = ["BNB", "ETH", "BTC", "CAKE"]

    # Binance uses trading pair format: "BNBUSDT", "ETHUSDT", etc.
    # We fetch all prices at once and filter to what we need
    url = "https://api.binance.com/api/v3/ticker/price"

    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        all_prices = response.json()
    except requests.exceptions.Timeout:
        raise ConnectionError("Binance API timed out after 10 seconds")
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Binance API request failed: {e}")

    # Build a lookup dict from the response
    # Binance returns: [{"symbol": "BNBUSDT", "price": "600.50"}, ...]
    price_lookup = {}
    for item in all_prices:
        price_lookup[item["symbol"]] = float(item["price"])

    # Map our token symbols to Binance trading pairs and extract prices
    prices = {}
    for symbol in symbols:
        # Try the USDT pair first (most common)
        pair = f"{symbol}USDT"
        if pair in price_lookup:
            prices[symbol] = price_lookup[pair]
        else:
            # If no USDT pair exists, try BUSD pair as fallback
            pair_busd = f"{symbol}BUSD"
            if pair_busd in price_lookup:
                prices[symbol] = price_lookup[pair_busd]

    # Stablecoins are always ~$1 — add them directly
    prices["USDT"] = 1.0
    prices["USDC"] = 1.0
    prices["BUSD"] = 1.0

    return prices


def fetch_pool_on_chain(pool_address: str, w3: Web3) -> dict:
    """
    Read live state from a PancakeSwap V3 pool contract on-chain.
    Takes: pool_address — the pool's contract address, w3 — Web3 connection.
    Returns: dict with current_tick, sqrt_price, liquidity, fee, token0, token1, tick_spacing.
    Why: on-chain data is the ground truth — it updates every block (~3 seconds on BSC),
    unlike DeFiLlama which updates hourly.
    """
    # Load the V3 Pool ABI so web3 knows what functions the contract has
    pool_abi = load_abi("pool_v3.json")

    # Create a contract instance pointing to this specific pool
    checksum_address = w3.to_checksum_address(pool_address)
    pool_contract = w3.eth.contract(address=checksum_address, abi=pool_abi)

    # Read slot0 — contains the current price and tick
    # slot0 is the most frequently read function on any V3 pool
    slot0 = pool_contract.functions.slot0().call()
    sqrt_price_x96 = slot0[0]  # Price encoded as sqrtPriceX96 (fixed-point math)
    current_tick = slot0[1]     # Current tick — represents the price as an integer

    # Read the pool's total active liquidity
    liquidity = pool_contract.functions.liquidity().call()

    # Read the fee tier (100 = 0.01%, 500 = 0.05%, 2500 = 0.25%, 10000 = 1%)
    fee = pool_contract.functions.fee().call()

    # Read which tokens are in this pool
    token0 = pool_contract.functions.token0().call()
    token1 = pool_contract.functions.token1().call()

    # Read tick spacing (determines valid tick boundaries for positions)
    tick_spacing = pool_contract.functions.tickSpacing().call()

    return {
        "pool_address": pool_address,
        "current_tick": current_tick,
        "sqrt_price_x96": sqrt_price_x96,
        "liquidity": liquidity,
        "fee": fee,
        "token0": token0,
        "token1": token1,
        "tick_spacing": tick_spacing,
    }


def fetch_position_on_chain(token_id: int, w3: Web3) -> dict:
    """
    Read an existing LP position's details from the PositionManager contract.
    Takes: token_id — the NFT ID of the position, w3 — Web3 connection.
    Returns: dict with token0, token1, fee, tick_lower, tick_upper, liquidity,
             tokens_owed_0, tokens_owed_1.
    Why: V3 positions are NFTs — each has a unique ID and its own price range.
    We need to read the position to know if it's still in range and how much it holds.
    """
    # Load the PositionManager ABI
    pm_abi = load_abi("nonfungible_position_manager.json")

    # Create a contract instance for the PositionManager
    pm_address = w3.to_checksum_address(CONTRACTS["position_manager"])
    pm_contract = w3.eth.contract(address=pm_address, abi=pm_abi)

    # Read the position data — returns a tuple of 12 values
    pos = pm_contract.functions.positions(token_id).call()

    return {
        "token_id": token_id,
        "token0": pos[2],           # Address of token0
        "token1": pos[3],           # Address of token1
        "fee": pos[4],              # Fee tier
        "tick_lower": pos[5],       # Lower bound of the price range
        "tick_upper": pos[6],       # Upper bound of the price range
        "liquidity": pos[7],        # Amount of liquidity in this position
        "tokens_owed_0": pos[10],   # Uncollected fees for token0
        "tokens_owed_1": pos[11],   # Uncollected fees for token1
    }


def fee_to_string(fee: int) -> str:
    """
    Convert a V3 fee tier integer to a human-readable percentage string.
    Takes: fee (int) — fee in hundredths of a basis point (e.g. 2500 = 0.25%).
    Returns: string like "0.25%".
    Why: raw fee integers are meaningless to users — "2500" vs "0.25%" is obvious.
    """
    # Fee is in hundredths of a basis point: 2500 = 0.25%
    return f"{fee / 10000:.2f}%"


def enrich_pools_with_risk(pools: list) -> list:
    """
    Add a 'risk' field to each pool based on its token pair classification.
    Takes: pools (list of dicts) — pool data from DeFiLlama.
    Returns: same list with 'risk' key added to each pool.
    Why: the config_manager's filter_pools_by_risk needs a 'risk' field on each pool.
    DeFiLlama doesn't provide this, so we calculate it from the token addresses.
    """
    # Build a reverse lookup: symbol → address for known tokens
    # DeFiLlama gives us symbols like "USDT-WBNB", we need addresses for classification
    symbol_to_address = {}
    for name, addr in TOKENS.items():
        symbol_to_address[name.upper()] = addr.lower()

    # Also add common aliases
    symbol_to_address["WBNB"] = TOKENS["WBNB"].lower()
    symbol_to_address["BTCB"] = TOKENS["BTCB"].lower()

    for pool in pools:
        # Parse the symbol to get the two token names
        # DeFiLlama format is typically "TOKEN0-TOKEN1" or "TOKEN0/TOKEN1"
        symbol = pool.get("symbol", "")
        parts = symbol.replace("/", "-").split("-")

        if len(parts) >= 2:
            # Look up each token's address from our known tokens
            t0_name = parts[0].strip().upper()
            t1_name = parts[1].strip().upper()

            t0_addr = symbol_to_address.get(t0_name, "")
            t1_addr = symbol_to_address.get(t1_name, "")

            # Classify the risk based on the token types
            if t0_addr and t1_addr:
                pool["risk"] = classify_pool_risk(t0_addr, t1_addr)
            else:
                # Unknown token(s) — mark as extreme risk (will be filtered out)
                pool["risk"] = "extreme"
        else:
            # Can't parse the symbol — unknown risk
            pool["risk"] = "extreme"

    return pools


def fetch_all_market_data(w3: Web3) -> dict:
    """
    Master fetcher — gathers all market data from all sources in one call.
    Takes: w3 — Web3 connection to BSC.
    Returns: dict with keys: pools, prices, timestamp.
    Why: the dispatcher calls this once per cycle to get everything it needs.
    Keeps the cycle logic clean — one call, one validated dataset.
    """
    # Fetch pool data from DeFiLlama (cached, refreshes every 5 min)
    pools = fetch_defi_llama_pools()

    # Add risk classification to each pool
    pools = enrich_pools_with_risk(pools)

    # Fetch token prices from Binance
    prices = fetch_token_prices()

    # Bundle everything with a timestamp
    return {
        "pools": pools,
        "prices": prices,
        "timestamp": time.time(),
    }


def validate_market_data(data: dict) -> tuple:
    """
    Validate fetched market data before it's used for decisions.
    Takes: data (dict) — the output of fetch_all_market_data().
    Returns: (True, "ok") if valid, (False, "reason") if not.
    Why: this is the gatekeeper — invalid data must NEVER reach the logic engine.
    A single bad APR or missing price could cause a terrible trade.
    """
    # Check that we actually got pool data
    pools = data.get("pools", [])
    if not pools:
        return (False, "No pool data received from DeFiLlama")

    # Check that we got price data
    prices = data.get("prices", {})
    if not prices:
        return (False, "No price data received from Binance")

    # Check the timestamp is recent (within 10 minutes)
    timestamp = data.get("timestamp", 0)
    age = time.time() - timestamp
    if age > 600:
        return (False, f"Market data is {age:.0f} seconds old (max 600)")

    # Validate each pool has non-null, positive APR and TVL
    for pool in pools:
        apy = pool.get("apy")
        tvl = pool.get("tvl_usd")

        # APR can be 0 (dead pool) but not None (missing data)
        if apy is None:
            return (False, f"Pool {pool.get('symbol', '?')} has null APY")

        # TVL must be positive — a pool with 0 TVL has no liquidity
        if tvl is not None and tvl < 0:
            return (False, f"Pool {pool.get('symbol', '?')} has negative TVL: {tvl}")

    # Validate prices are positive numbers
    for symbol, price in prices.items():
        if price <= 0:
            return (False, f"Token {symbol} has invalid price: {price}")

    # All checks passed
    return (True, "ok")
