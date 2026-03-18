"""
Validation Module
=================
Validates user inputs: private keys, wallet addresses, and raw input sanitisation.
Used primarily during Telegram onboarding to verify the user's wallet key is real
before the bot tries to use it for transactions.

Takes: raw strings from user input.
Returns: (valid: bool, result: str) tuples — result is either the derived address or an error message.
Why: catching bad input here prevents cryptic web3 errors deeper in the system.
"""

from eth_account import Account


def sanitize_key_input(raw_input: str) -> str:
    """
    Clean up a raw private key string from the user.
    Takes: raw_input (str) — whatever the user typed/pasted.
    Returns: cleaned 64-char hex string (no 0x prefix, no whitespace).
    Why: users often paste keys with spaces, newlines, or the 0x prefix.
    """
    # Strip any whitespace or newlines from both ends
    cleaned = raw_input.strip()

    # Remove the 0x prefix if present — web3 expects raw hex
    if cleaned.startswith("0x") or cleaned.startswith("0X"):
        cleaned = cleaned[2:]

    # Lowercase for consistency — hex is case-insensitive
    cleaned = cleaned.lower()

    return cleaned


def validate_private_key(key_string: str) -> tuple:
    """
    Check if a string is a valid Ethereum/BSC private key.
    Takes: key_string (str) — the raw key from user input.
    Returns: (True, wallet_address) if valid, (False, error_message) if not.
    Why: we need to verify the key actually produces a real wallet before using it.
    """
    # First clean up the input (spaces, 0x prefix, etc.)
    cleaned = sanitize_key_input(key_string)

    # Private keys must be exactly 64 hex characters (32 bytes)
    if len(cleaned) != 64:
        return (False, "Private key must be exactly 64 hex characters (32 bytes).")

    # Check every character is valid hex (0-9, a-f)
    try:
        int(cleaned, 16)
    except ValueError:
        return (False, "Private key contains invalid characters. Must be hexadecimal (0-9, a-f).")

    # Try to derive the wallet address from the key
    # This is the ultimate validation — if web3 can produce an address, the key is real
    try:
        # Add 0x prefix back for web3's from_key method
        account = Account.from_key("0x" + cleaned)

        # Return success with the derived public address
        return (True, account.address)

    except Exception as e:
        # If web3 rejects the key for any reason, it's invalid
        return (False, f"Invalid private key: {str(e)}")


def validate_address(address_string: str) -> bool:
    """
    Check if a string is a valid Ethereum/BSC wallet address.
    Takes: address_string (str) — an address like 0xABC...
    Returns: True if valid, False if not.
    Why: used to verify addresses before sending transactions to them.
    """
    # Must start with 0x
    if not address_string.startswith("0x"):
        return False

    # Must be exactly 42 characters (0x + 40 hex chars)
    if len(address_string) != 42:
        return False

    # All characters after 0x must be valid hex
    try:
        int(address_string[2:], 16)
        return True
    except ValueError:
        return False
