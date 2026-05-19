"""
eth_distributor.py - ETH Distribution System for Evelyn
=========================================================
Distribute ETH from one wallet to multiple burner wallets safely.

Commands: /distribute, /spreadeth, /fundwallets
NL triggers: "bagi rata eth", "spread eth", "fund semua wallet"

Features:
- Equal distribution across all/selected wallets
- Gas estimation before execution
- Preview with full breakdown
- Approval queue integration (PENDING first)
- DRY_RUN=true default
- Minimum balance reserve support
- Never auto-sends without approval

SAFETY:
- All distributions go through approval_queue as PENDING
- Never executes directly from chat
- Private keys never exposed
- Warns on insufficient balance or high gas
"""

import os
import json
import re
from datetime import datetime
from web3 import Web3

from custom_tools.check_wallet import get_web3, validate_address
from custom_tools.wallet_manager import list_wallets, get_wallet_key, WALLETS_DIR
from custom_tools.approval_queue import add_to_queue


DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DEFAULT_GAS_LIMIT = 21000  # Standard ETH transfer


def build_distribution_plan(
    from_label: str,
    to_labels: list = None,
    total_amount_eth: float = None,
    per_wallet_eth: float = None,
    chain: str = "ethereum",
    reserve_eth: float = 0.0,
) -> dict:
    """
    Build an ETH distribution plan.

    Args:
        from_label: Source wallet label
        to_labels: Destination wallet labels (None = all except source)
        total_amount_eth: Total ETH to distribute (split equally)
        per_wallet_eth: Fixed amount per wallet (overrides total split)
        chain: Chain name
        reserve_eth: Keep this much in source wallet

    Returns:
        dict with full distribution preview
    """
    w3 = get_web3(chain)

    # Get source wallet
    source_file = WALLETS_DIR / f"{from_label}.json"
    if not source_file.exists():
        raise FileNotFoundError(f"Source wallet '{from_label}' not found")

    with open(source_file) as f:
        source_data = json.load(f)
    source_address = Web3.to_checksum_address(source_data["address"])

    # Get source balance
    source_balance = w3.eth.get_balance(source_address)
    source_balance_eth = float(Web3.from_wei(source_balance, "ether"))

    # Get destination wallets
    all_wallets = list_wallets()
    if to_labels:
        dest_wallets = [w for w in all_wallets if w["label"] in to_labels]
    else:
        # All wallets except source
        dest_wallets = [w for w in all_wallets if w["label"] != from_label]

    if not dest_wallets:
        raise ValueError("No destination wallets found")

    num_destinations = len(dest_wallets)

    # Get gas price
    try:
        gas_price = w3.eth.gas_price
    except Exception:
        gas_price = Web3.to_wei(30, "gwei")

    gas_cost_per_tx = DEFAULT_GAS_LIMIT * gas_price
    gas_cost_per_tx_eth = float(Web3.from_wei(gas_cost_per_tx, "ether"))
    total_gas_eth = gas_cost_per_tx_eth * num_destinations

    # Calculate distribution
    if per_wallet_eth:
        amount_per_wallet = per_wallet_eth
        total_send = per_wallet_eth * num_destinations
    elif total_amount_eth:
        total_send = total_amount_eth
        amount_per_wallet = total_amount_eth / num_destinations
    else:
        # Distribute all available (minus gas + reserve)
        available = source_balance_eth - total_gas_eth - reserve_eth
        if available <= 0:
            raise ValueError(f"Insufficient balance. Available after gas+reserve: {available:.6f} ETH")
        total_send = available
        amount_per_wallet = available / num_destinations

    # Check if enough balance
    total_needed = total_send + total_gas_eth + reserve_eth
    sufficient = source_balance_eth >= total_needed

    # Warnings
    warnings = []
    if not sufficient:
        warnings.append(f"CRITICAL: Insufficient balance! Need {total_needed:.6f} ETH, have {source_balance_eth:.6f}")
    if total_gas_eth > total_send * 0.1:
        warnings.append(f"HIGH: Gas cost is >10% of send amount ({total_gas_eth:.6f} ETH)")
    if amount_per_wallet < 0.0001:
        warnings.append("LOW: Per-wallet amount very small, may not cover future gas")

    # Build plan
    plan = {
        "type": "eth_distribution",
        "chain": chain,
        "from_wallet": from_label,
        "from_address": source_address,
        "source_balance_eth": f"{source_balance_eth:.6f}",
        "destinations": [
            {"label": w["label"], "address": w["address"]}
            for w in dest_wallets
        ],
        "num_destinations": num_destinations,
        "amount_per_wallet_eth": f"{amount_per_wallet:.6f}",
        "total_send_eth": f"{total_send:.6f}",
        "gas_per_tx_eth": f"{gas_cost_per_tx_eth:.6f}",
        "total_gas_eth": f"{total_gas_eth:.6f}",
        "total_needed_eth": f"{total_needed:.6f}",
        "reserve_eth": f"{reserve_eth:.6f}",
        "sufficient_balance": sufficient,
        "warnings": warnings,
        "gas_price_gwei": str(Web3.from_wei(gas_price, "gwei")),
        "dry_run": DRY_RUN,
        "created_at": datetime.utcnow().isoformat(),
    }

    return plan


def queue_distribution(plan: dict) -> int:
    """
    Add distribution plan to approval queue as PENDING.

    Returns approval queue entry ID.
    """
    # Convert to approval_queue compatible format
    preview = {
        "contract": "ETH_TRANSFER",
        "chain": plan["chain"],
        "from_wallet": plan["from_wallet"],
        "from_address": plan["from_address"],
        "mint_function": "distribute_eth",
        "quantity": plan["num_destinations"],
        "total_value_wei": str(Web3.to_wei(float(plan["total_send_eth"]), "ether")),
        "estimated_gas": DEFAULT_GAS_LIMIT * plan["num_destinations"],
        "gas_price_wei": str(Web3.to_wei(float(plan["gas_price_gwei"]), "gwei")),
        "total_cost_wei": str(Web3.to_wei(float(plan["total_needed_eth"]), "ether")),
        "risk_warnings": plan["warnings"],
        "tx_data": {
            "type": "eth_distribution",
            "plan": plan,
        },
    }

    return add_to_queue(preview)


def format_distribution_preview(plan: dict, approval_id: int = None) -> str:
    """Format distribution plan for Telegram display."""
    dest_list = "\n".join(
        f"  • {d['label']}" for d in plan["destinations"][:10]
    )
    if plan["num_destinations"] > 10:
        dest_list += f"\n  ... +{plan['num_destinations'] - 10} more"

    lines = [
        f"💸 <b>Evelyn ETH Distribution Plan</b>",
        f"",
        f"<b>From:</b> {plan['from_wallet']}",
        f"<code>{plan['from_address']}</code>",
        f"<b>Balance:</b> {plan['source_balance_eth']} ETH",
        f"",
        f"<b>To:</b> {plan['num_destinations']} wallets",
        f"<b>Per wallet:</b> {plan['amount_per_wallet_eth']} ETH",
        f"<b>Total send:</b> {plan['total_send_eth']} ETH",
        f"",
        f"<b>Gas:</b> {plan['total_gas_eth']} ETH ({plan['gas_price_gwei']} gwei)",
        f"<b>Reserve:</b> {plan['reserve_eth']} ETH",
        f"<b>Total needed:</b> {plan['total_needed_eth']} ETH",
        f"<b>Sufficient:</b> {'✅ YES' if plan['sufficient_balance'] else '❌ NO'}",
        f"",
        f"<b>Destinations:</b>",
        dest_list,
    ]

    if plan["warnings"]:
        lines.append(f"")
        lines.append(f"⚠️ <b>Warnings:</b>")
        for w in plan["warnings"]:
            lines.append(f"  • {w}")

    if approval_id:
        lines.append(f"")
        lines.append(f"<b>Status:</b> PENDING APPROVAL")
        lines.append(f"<b>Approval ID:</b> #{approval_id}")
        lines.append(f"")
        lines.append(f"Approve: /approve {approval_id}")

    return "\n".join(lines)


# === NL Parsing Helpers ===

def parse_distribute_params(text: str) -> dict:
    """
    Parse natural language distribute command.

    Examples:
    - "bagi rata 0.01 ETH dari test1 ke semua wallet"
    - "spread 0.05 eth dari main"
    - "fund semua wallet 0.002 eth dari test1"
    - "transfer 0.01 eth ke semua wallet"
    """
    params = {
        "from_label": None,
        "amount_eth": None,
        "per_wallet": False,
    }

    text_lower = text.lower()

    # Extract amount
    amount_match = re.search(r'(\d+\.?\d*)\s*eth', text_lower)
    if amount_match:
        params["amount_eth"] = float(amount_match.group(1))

    # Extract source wallet (dari <label>)
    from_match = re.search(r'(?:dari|from)\s+(\w+)', text_lower)
    if from_match:
        label = from_match.group(1)
        noise = {"semua", "wallet", "eth", "semua"}
        if label not in noise:
            params["from_label"] = label

    # Detect per-wallet vs total
    if "per wallet" in text_lower or "masing" in text_lower or "tiap" in text_lower:
        params["per_wallet"] = True

    return params
