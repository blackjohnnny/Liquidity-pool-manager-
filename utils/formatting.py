"""
Formatting Module
=================
Formats numbers, addresses, and Telegram messages into clean, readable strings.
Every display-facing value in the bot goes through one of these functions
so the output is consistent everywhere.

Takes: raw numbers, addresses, data dicts.
Returns: formatted strings ready for Telegram messages.
Why: centralises all formatting so we don't have f-string formatting scattered
across 10 different files — change the format once, it updates everywhere.
"""


def format_address(address: str) -> str:
    """
    Shorten a wallet address for display: 0xAbCd...EfGh
    Takes: address (str) — full 42-char address.
    Returns: truncated string showing first 6 and last 4 chars.
    Why: full addresses are too long and clutter Telegram messages.
    """
    # Guard against None or too-short strings
    if not address or len(address) < 10:
        return address or "Unknown"

    # Show the 0x prefix + first 4 hex chars, then last 4
    return f"{address[:6]}...{address[-4:]}"


def format_usd(amount: float) -> str:
    """
    Format a dollar amount: $1,234.56 or -$1,234.56
    Takes: amount (float) — the USD value.
    Returns: formatted string with $ sign and commas.
    Why: makes financial numbers instantly readable.
    """
    # Handle negative amounts with a leading minus
    if amount < 0:
        return f"-${abs(amount):,.2f}"

    return f"${amount:,.2f}"


def format_percent(value: float) -> str:
    """
    Format a percentage with sign: +3.45% or -1.20%
    Takes: value (float) — the percentage (e.g. 3.45 means 3.45%).
    Returns: formatted string with + or - prefix.
    Why: PnL and APR changes need the sign to show direction at a glance.
    """
    # Add explicit + sign for positive values
    if value >= 0:
        return f"+{value:.2f}%"

    # Negative values already have the minus sign
    return f"{value:.2f}%"


def format_bnb(amount: float) -> str:
    """
    Format a BNB amount to 4 decimal places.
    Takes: amount (float) — BNB value.
    Returns: formatted string like "1.2345 BNB".
    Why: 4 decimals is enough precision for display without being noisy.
    """
    return f"{amount:.4f} BNB"


def format_token_amount(amount: float, symbol: str) -> str:
    """
    Format any token amount with its symbol.
    Takes: amount (float) — token quantity, symbol (str) — token ticker.
    Returns: formatted string like "1,234.56 USDT".
    Why: generic formatter for any ERC-20 token display.
    """
    # Use 2 decimals for stablecoins, 6 for small amounts, 4 otherwise
    if amount == 0:
        return f"0 {symbol}"

    # Large amounts get commas and 2 decimals
    if abs(amount) >= 1:
        return f"{amount:,.2f} {symbol}"

    # Small amounts get more precision so you can see the value
    return f"{amount:.6f} {symbol}"


def format_tvl(tvl: float) -> str:
    """
    Format Total Value Locked into a short readable form.
    Takes: tvl (float) — TVL in USD.
    Returns: short string like "$12.3M" or "$456.7K".
    Why: raw TVL numbers like $12345678.90 are hard to scan quickly.
    """
    # Billions
    if tvl >= 1_000_000_000:
        return f"${tvl / 1_000_000_000:.1f}B"

    # Millions
    if tvl >= 1_000_000:
        return f"${tvl / 1_000_000:.1f}M"

    # Thousands
    if tvl >= 1_000:
        return f"${tvl / 1_000:.1f}K"

    # Small values get full dollar format
    return format_usd(tvl)


def format_pool_name(symbol: str) -> str:
    """
    Clean up a pool symbol string for display.
    Takes: symbol (str) — raw pool name like "USDT-WBNB" or "Cake/BNB".
    Returns: cleaned string.
    Why: different APIs return pool names in different formats — this normalises them.
    """
    # Replace common separators with a consistent one
    if not symbol:
        return "Unknown Pool"

    # Normalise to forward slash separator
    return symbol.replace("-", "/").replace("_", "/")


def format_pool_row(rank: int, symbol: str, fee: str, apr: float, tvl: float) -> str:
    """
    Format a single pool as one line in a ranked list.
    Takes: rank — position number, symbol — pool pair, fee — fee tier,
           apr — annual percentage rate, tvl — total value locked.
    Returns: a formatted string like "1. USDT/BNB (0.25%) | APR: 45.2% | TVL: $12.3M"
    Why: used in /update to show the top pools in a clean, scannable list.
    """
    # Build the pool line with consistent spacing
    pool_name = format_pool_name(symbol)
    tvl_display = format_tvl(tvl)

    return f"{rank}. {pool_name} ({fee}) | APR: {apr:.1f}% | TVL: {tvl_display}"
