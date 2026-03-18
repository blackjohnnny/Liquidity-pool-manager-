"""
Web3 Helper Module
==================
Manages the connection to the BSC blockchain and provides helper functions
for common on-chain operations: checking balances, reading token data, etc.

Think of this as the "translator" between our Python code and the blockchain —
every time we need to read or write data on-chain, it goes through here.

Takes: addresses, private keys, token addresses.
Returns: balances, account objects, contract instances.
Why: centralises all web3 setup so other modules don't duplicate connection logic.
"""

import json
import os
from web3 import Web3
from eth_account import Account
from config.settings import BSC_RPC_URL, ABI_DIR


def get_web3() -> Web3:
    """
    Create and return a Web3 instance connected to BSC.
    Takes: nothing (reads RPC URL from settings).
    Returns: a connected Web3 object.
    Why: every blockchain operation needs a web3 instance — this is the single factory for it.
    """
    # Create the web3 connection using our RPC endpoint
    w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))

    # Verify we can actually reach the blockchain
    if not w3.is_connected():
        raise ConnectionError(
            f"Cannot connect to BSC at {BSC_RPC_URL}. "
            "Check your internet connection and RPC URL in .env"
        )

    return w3


def get_account(private_key: str):
    """
    Derive a wallet account from a private key.
    Takes: private_key (str) — the raw hex key (with or without 0x prefix).
    Returns: a LocalAccount object that can sign transactions.
    Why: needed every time we want to send a transaction on-chain.
    """
    # Ensure the key has the 0x prefix that web3 expects
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Derive and return the account object
    return Account.from_key(private_key)


def get_balance(w3: Web3, address: str) -> float:
    """
    Get the native BNB balance for a wallet address.
    Takes: w3 (Web3) — blockchain connection, address (str) — wallet address.
    Returns: balance as a float in BNB (not wei).
    Why: used to show the user their balance and to check if they have enough gas.
    """
    # Fetch balance in wei (smallest unit — like cents to dollars)
    balance_wei = w3.eth.get_balance(address)

    # Convert from wei to BNB (1 BNB = 10^18 wei)
    balance_bnb = w3.from_wei(balance_wei, "ether")

    return float(balance_bnb)


def load_abi(filename: str) -> list:
    """
    Load a smart contract ABI from the config/abi/ directory.
    Takes: filename (str) — the ABI JSON filename (e.g. "erc20.json").
    Returns: the ABI as a Python list of dicts.
    Why: ABIs tell web3 how to talk to a specific smart contract — like an API schema.
    """
    # Build the full path to the ABI file
    abi_path = os.path.join(ABI_DIR, filename)

    # Read and parse the JSON
    with open(abi_path, "r") as f:
        return json.load(f)


def get_token_contract(w3: Web3, token_address: str):
    """
    Create a contract instance for an ERC-20 token.
    Takes: w3 (Web3) — connection, token_address (str) — the token's contract address.
    Returns: a web3 Contract object we can call functions on (balanceOf, approve, etc).
    Why: needed to read token balances and approve spending before swaps/LP.
    """
    # Load the standard ERC-20 ABI (same interface for all tokens)
    abi = load_abi("erc20.json")

    # Convert the address to checksum format (mixed-case) that web3 requires
    checksum_address = w3.to_checksum_address(token_address)

    # Create and return the contract instance
    return w3.eth.contract(address=checksum_address, abi=abi)


def get_token_balance(w3: Web3, wallet_address: str, token_address: str) -> float:
    """
    Get the balance of a specific ERC-20 token for a wallet.
    Takes: w3 — connection, wallet_address — who to check, token_address — which token.
    Returns: balance as a float (adjusted for token decimals).
    Why: need to know how much of each token the user holds before allocating.
    """
    # Get the contract instance for this token
    contract = get_token_contract(w3, token_address)

    # Convert wallet to checksum format
    checksum_wallet = w3.to_checksum_address(wallet_address)

    # Read the raw balance (in the token's smallest unit)
    raw_balance = contract.functions.balanceOf(checksum_wallet).call()

    # Read how many decimal places this token uses (USDT=18, some tokens=6, etc.)
    decimals = contract.functions.decimals().call()

    # Convert from raw units to human-readable (e.g. 1000000 with 6 decimals = 1.0)
    return raw_balance / (10 ** decimals)
