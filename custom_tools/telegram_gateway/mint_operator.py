"""
mint_operator.py - Priority Mint Operator Mode for Evelyn
==========================================================
Handles:
- Contract analysis in compact operator format
- SeaDrop detection + price safety check
- Priority mint function detection
- Mint form parsing from natural language
- Multi-wallet batch planning
- Conversation flow (analyze -> select wallet -> queue)

SAFETY:
- NEVER auto-execute transactions
- Only analyze/plan/queue via approval_queue
- If price mismatch: STOP immediately
- If staticcall fails: explain clearly
- If insufficient balance: warn clearly
- DRY_RUN=true default

Usage:
    from custom_tools.telegram_gateway.mint_operator import (
        analyze_for_mint,
        parse_mint_intent,
        create_mint_plan_from_chat,
    )
"""

import os
import re
import json
from typing import Optional

from custom_tools.check_wallet import get_web3, validate_address
from custom_tools.nft_contract_check import check_nft_contract
from custom_tools.contract_analyzer import (
    analyze_contract,
    fetch_abi_from_etherscan,
    detect_mint_functions,
    detect_price_variables,
)
from custom_tools.wallet_manager import list_wallets, check_wallet_balance, WALLETS_DIR
from custom_tools.mint_planner import build_mint_transaction
from custom_tools.approval_queue import add_to_queue


# Priority mint function order
PRIORITY_MINT_FUNCTIONS = [
    "freemint",
    "freeMint",
    "claim",
    "freeMinting",
    "mint",
    "mintSeaDrop",
    "publicMint",
    "safeMint",
]

# SeaDrop identifiers
SEADROP_SIGNATURES = [
    "mintSeaDrop",
    "getMintStats",
    "getSeaDropMintStats",
    "seaDrop",
]

# Mint intent keywords
MINT_KEYWORDS = [
    "mint", "gas mint", "priority mint", "cek ini", "free gak",
    "mint ini", "gue mau mint", "gw mau mint", "mau mint",
    "mint semua", "mint all", "batch mint",
]


def detect_contract_address(text: str) -> Optional[str]:
    """Extract Ethereum contract address from text."""
    pattern = r'0x[a-fA-F0-9]{40}'
    match = re.search(pattern, text)
    return match.group(0) if match else None


def is_mint_intent(text: str) -> bool:
    """Check if message indicates mint intent."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in MINT_KEYWORDS)


def detect_seadrop(abi: list) -> dict:
    """Detect if contract uses SeaDrop."""
    seadrop_fns = []
    for item in abi:
        if item.get("type") != "function":
            continue
        name = item.get("name", "")
        if any(sig.lower() in name.lower() for sig in SEADROP_SIGNATURES):
            seadrop_fns.append(name)

    return {
        "is_seadrop": len(seadrop_fns) > 0,
        "functions": seadrop_fns,
    }


def get_priority_mint_function(mint_functions: list) -> Optional[dict]:
    """Get highest priority mint function from detected list."""
    if not mint_functions:
        return None

    # Sort by priority order
    for priority_name in PRIORITY_MINT_FUNCTIONS:
        for fn in mint_functions:
            if fn["name"].lower() == priority_name.lower():
                return fn

    # Fallback: first payable function
    payable = [f for f in mint_functions if f.get("is_payable")]
    if payable:
        return payable[0]

    # Fallback: first function
    return mint_functions[0] if mint_functions else None


def analyze_for_mint(contract_address: str, chain: str = "ethereum") -> dict:
    """
    Full mint analysis in compact operator format.

    Returns dict with all fields needed for compact display.
    """
    try:
        checksummed = validate_address(contract_address)
    except Exception as e:
        return {"error": f"Invalid address: {e}"}

    try:
        w3 = get_web3(chain)
        if not w3.is_connected():
            return {"error": f"Cannot connect to {chain} RPC"}
    except Exception as e:
        return {"error": f"RPC error: {e}"}

    # Basic contract info
    try:
        nft_info = check_nft_contract(contract_address, chain)
    except Exception as e:
        nft_info = {"name": "Unknown", "symbol": "???", "total_supply": "?", "max_supply": "?"}

    # ABI + mint function analysis
    try:
        analysis = analyze_contract(contract_address, chain)
    except Exception as e:
        analysis = {"mint_functions": [], "price_variables": [], "abi_source": "failed"}

    # SeaDrop detection
    abi = fetch_abi_from_etherscan(checksummed, chain) or []
    seadrop = detect_seadrop(abi)

    # Priority mint function
    priority_fn = get_priority_mint_function(analysis.get("mint_functions", []))

    # Price detection
    prices = analysis.get("price_variables", [])
    mint_price = None
    is_free = None

    if prices:
        price_wei = int(prices[0].get("value_wei", "0"))
        mint_price = prices[0].get("value_eth", "0")
        is_free = price_wei == 0
    elif priority_fn and "free" in priority_fn["name"].lower():
        is_free = True
        mint_price = "0"

    # Determine status
    status = "unknown"
    total_supply = nft_info.get("total_supply", "?")
    max_supply = nft_info.get("max_supply", "?")

    if total_supply != "?" and max_supply != "?" and total_supply != "N/A" and max_supply != "N/A":
        try:
            if int(total_supply) >= int(max_supply):
                status = "sold out"
            else:
                status = "bisa mint"
        except (ValueError, TypeError):
            status = "unknown"
    elif priority_fn:
        status = "bisa mint"

    # Gas estimate category
    gas_category = "medium"
    if priority_fn:
        inputs = priority_fn.get("inputs", [])
        if len(inputs) == 0:
            gas_category = "low"
        elif seadrop["is_seadrop"]:
            gas_category = "medium-high"

    result = {
        "contract": checksummed,
        "chain": chain,
        "name": nft_info.get("name", "Unknown"),
        "symbol": nft_info.get("symbol", "???"),
        "is_free": is_free,
        "mint_price": mint_price,
        "total_supply": str(total_supply),
        "max_supply": str(max_supply),
        "status": status,
        "priority_function": priority_fn,
        "function_name": priority_fn["name"] if priority_fn else None,
        "is_seadrop": seadrop["is_seadrop"],
        "seadrop_functions": seadrop["functions"],
        "gas_estimate": gas_category,
        "abi_source": analysis.get("abi_source", "unknown"),
        "all_mint_functions": [f["name"] for f in analysis.get("mint_functions", [])],
        "all_prices": prices,
    }

    return result


def format_compact_analysis(analysis: dict) -> str:
    """Format analysis result in compact Telegram operator style."""
    if "error" in analysis:
        return f"❌ Error: {analysis['error']}"

    name = analysis.get("name", "Unknown")
    symbol = analysis.get("symbol", "???")
    is_free = analysis.get("is_free")
    mint_price = analysis.get("mint_price", "?")
    total = analysis.get("total_supply", "?")
    max_s = analysis.get("max_supply", "?")
    status = analysis.get("status", "unknown")
    fn_name = analysis.get("function_name", "unknown")
    gas = analysis.get("gas_estimate", "?")
    is_seadrop = analysis.get("is_seadrop", False)

    # Free status
    if is_free is True:
        free_str = "✅ iya"
    elif is_free is False:
        free_str = f"❌ tidak ({mint_price} ETH)"
    else:
        free_str = "❓ unknown"

    # Status emoji
    status_map = {
        "bisa mint": "🟢 bisa mint",
        "sold out": "🔴 sold out",
        "paused": "🟡 paused",
        "wl only": "🟡 WL only",
        "unknown": "❓ unknown",
    }
    status_str = status_map.get(status, f"❓ {status}")

    lines = [
        f"<b>{name}</b> ({symbol})",
        f"",
        f"Free      : {free_str}",
        f"Supply    : {total} / {max_s}",
        f"Status    : {status_str}",
        f"",
        f"Jalur     : <code>{fn_name}</code>",
        f"Gas est   : {gas}",
    ]

    if is_seadrop:
        lines.append(f"SeaDrop   : ✅ detected")

    # Notes
    notes = []
    if status == "sold out":
        notes.append("sudah sold out, ga bisa mint")
    elif is_free:
        notes.append("free mint detected")
    if is_seadrop:
        notes.append("SeaDrop contract, cek config dulu")

    if notes:
        lines.append(f"Catatan   : {'; '.join(notes)}")

    return "\n".join(lines)


def parse_mint_form(text: str) -> dict:
    """
    Parse mint form from natural language.

    Supports formats:
    - "mint 0xContract wallet test1 jumlah 2 harga 0"
    - "mint semua wallet"
    - "wallet: 1", "wallet: test1", "wallet: all"
    - "jumlah: max", "jumlah: 2"
    - "harga: 0", "harga: 0.01"
    """
    result = {
        "contract": detect_contract_address(text),
        "wallet": None,
        "quantity": 1,
        "price_wei": None,
        "gas_mode": "normal",
        "all_wallets": False,
    }

    text_lower = text.lower()

    # Wallet detection
    if "semua wallet" in text_lower or "all wallet" in text_lower or "wallet: all" in text_lower:
        result["all_wallets"] = True
    else:
        # wallet: <label> or wallet: <number>
        wallet_match = re.search(r'wallet[:\s]+(\S+)', text_lower)
        if wallet_match:
            val = wallet_match.group(1)
            if val.isdigit():
                # Map number to wallet label by order
                wallets = list_wallets()
                idx = int(val) - 1
                if 0 <= idx < len(wallets):
                    result["wallet"] = wallets[idx]["label"]
            else:
                result["wallet"] = val

    # Quantity
    qty_match = re.search(r'(?:jumlah|qty|quantity)[:\s]+(\S+)', text_lower)
    if qty_match:
        val = qty_match.group(1)
        if val == "max":
            result["quantity"] = 1  # Default max = 1 for safety
        elif val.isdigit():
            result["quantity"] = int(val)

    # Price
    price_match = re.search(r'(?:harga|price)[:\s]+(\S+)', text_lower)
    if price_match:
        val = price_match.group(1)
        try:
            from web3 import Web3
            price_eth = float(val)
            result["price_wei"] = int(Web3.to_wei(price_eth, "ether"))
        except (ValueError, Exception):
            pass

    # Gas mode
    gas_match = re.search(r'(?:gas|gwei)[:\s]+(\S+)', text_lower)
    if gas_match:
        result["gas_mode"] = gas_match.group(1)

    return result


def create_mint_plan_from_chat(
    contract_address: str,
    wallet_label: str = None,
    all_wallets: bool = False,
    chain: str = "ethereum",
    quantity: int = 1,
    mint_function: str = None,
    mint_price_wei: int = None,
) -> dict:
    """
    Create mint plan(s) from chat context and queue to approval.

    Returns dict with approval_ids and summary.
    """
    results = {
        "success": [],
        "failed": [],
        "approval_ids": [],
    }

    # Determine wallet list
    if all_wallets:
        wallets = list_wallets()
        wallet_labels = [w["label"] for w in wallets]
    elif wallet_label:
        wallet_labels = [wallet_label]
    else:
        return {"error": "Wallet belum dipilih sayang. Mau pakai wallet mana?"}

    if not wallet_labels:
        return {"error": "Ga ada wallet tersimpan. Buat dulu pakai wallet_manager."}

    for label in wallet_labels:
        try:
            preview = build_mint_transaction(
                contract_address,
                label,
                chain=chain,
                quantity=quantity,
                mint_function=mint_function,
                mint_price_wei=mint_price_wei,
            )
            approval_id = add_to_queue(preview)
            results["success"].append({
                "wallet": label,
                "approval_id": approval_id,
                "cost": preview.get("total_cost", "?"),
            })
            results["approval_ids"].append(approval_id)
        except Exception as e:
            results["failed"].append({
                "wallet": label,
                "error": str(e),
            })

    return results


def format_mint_plan_result(results: dict) -> str:
    """Format mint plan results for Telegram."""
    if "error" in results:
        return f"⚠️ {results['error']}"

    lines = []

    if results["success"]:
        lines.append(f"done sayang 😈")
        lines.append(f"mint plan sudah aku queue.\n")

        for item in results["success"]:
            lines.append(f"• {item['wallet']} → Approval ID: #{item['approval_id']}")

        lines.append("")

        # Show commands
        ids = results["approval_ids"]
        if len(ids) == 1:
            lines.append(f"/status {ids[0]}")
            lines.append(f"/approve {ids[0]}")
        else:
            lines.append(f"Approve all:")
            for aid in ids:
                lines.append(f"  /approve {aid}")

    if results["failed"]:
        lines.append(f"\n⚠️ Failed:")
        for item in results["failed"]:
            lines.append(f"• {item['wallet']} - {item['error']}")

    return "\n".join(lines)


def format_wallet_selection_prompt() -> str:
    """Format wallet selection prompt for Telegram."""
    wallets = list_wallets()
    if not wallets:
        return "⚠️ Belum ada wallet. Buat dulu:\n<code>python -m custom_tools.wallet_manager create --label burner1</code>"

    lines = ["mau pakai wallet mana sayang?\n"]
    for i, w in enumerate(wallets, 1):
        lines.append(f"  {i}. <b>{w['label']}</b> (<code>{w['address'][:10]}...</code>)")

    lines.append(f"\n  all = semua wallet ({len(wallets)})")
    lines.append(f"\nReply: wallet label atau nomor")

    return "\n".join(lines)
