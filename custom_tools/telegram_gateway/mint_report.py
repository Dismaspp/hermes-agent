"""
mint_report.py - Mint Execution Report System for Evelyn
==========================================================
Generates Telegram-formatted mint execution reports.

Report types:
- Success report (all wallets succeeded)
- Partial failed report (some wallets failed)
- Full failed report (all wallets failed)

Features:
- Collection name from contract analyzer (never hardcoded)
- Wallet label format (W1, W2, etc.)
- Gas cost summary
- Speed tracking
- Common failure detection and categorization
- Compact Telegram-friendly formatting

SAFETY:
- Never expose private keys in reports
- Never fake success
- Report actual on-chain results only

Usage:
    from custom_tools.telegram_gateway.mint_report import (
        generate_execution_report,
        format_report_telegram,
    )
"""

import json
import time
from datetime import datetime
from typing import Optional

from custom_tools.approval_queue import get_entry
from custom_tools.nft_contract_check import check_nft_contract


# Common failure reasons to detect and humanize
FAILURE_REASONS = {
    "insufficient funds": "insufficient funds",
    "insufficient balance": "insufficient funds",
    "execution reverted": "reverted",
    "sold out": "sold out",
    "max per wallet": "max wallet reached",
    "max mint": "max wallet reached",
    "exceeds max": "max wallet reached",
    "not started": "mint belum mulai",
    "paused": "paused",
    "not active": "mint belum aktif",
    "invalid proof": "WL only / invalid proof",
    "not whitelisted": "WL only",
    "nonce too low": "nonce issue",
    "replacement transaction": "nonce issue",
    "gas too low": "gas too low",
    "underpriced": "gas too low",
    "out of gas": "out of gas",
}


def detect_failure_reason(error_msg: str) -> str:
    """Detect and humanize failure reason from error message."""
    if not error_msg:
        return "unknown error"

    error_lower = error_msg.lower()
    for pattern, reason in FAILURE_REASONS.items():
        if pattern in error_lower:
            return reason

    # Truncate unknown errors
    return error_msg[:60] if len(error_msg) > 60 else error_msg


def get_collection_name(contract_address: str, chain: str = "ethereum") -> str:
    """
    Get collection name from contract. Never hardcode.
    Falls back to 'NFT' if unavailable.
    """
    try:
        info = check_nft_contract(contract_address, chain)
        name = info.get("name", "")
        symbol = info.get("symbol", "")

        if name and name != "Unknown":
            return name
        elif symbol and symbol != "Unknown":
            return symbol
    except Exception:
        pass

    return "NFT"


def wallet_label_short(label: str, index: int = None) -> str:
    """
    Format wallet label to short format.
    'burner1' -> 'W1' style, or use provided index.
    """
    if index is not None:
        return f"W{index + 1}"

    # Try to extract number from label
    import re
    num_match = re.search(r'(\d+)', label)
    if num_match:
        return f"W{num_match.group(1)}"

    # Fallback: use first 6 chars
    return label[:6]


def generate_execution_report(
    execution_results: list,
    contract_address: str = None,
    chain: str = "ethereum",
    start_time: float = None,
    end_time: float = None,
) -> dict:
    """
    Generate structured execution report from results.

    Args:
        execution_results: List of dicts from mint_executor
            Each: {id, status, tx_hash, wallet_label, ...}
        contract_address: NFT contract address
        chain: Chain name
        start_time: Execution start timestamp
        end_time: Execution end timestamp

    Returns:
        Structured report dict
    """
    now = time.time()
    if not start_time:
        start_time = now
    if not end_time:
        end_time = now

    # Categorize results
    success = []
    failed = []
    dry_run = []

    for i, result in enumerate(execution_results):
        # Try to get wallet label from approval queue entry
        wallet_label = result.get("wallet_label", "")
        if not wallet_label and result.get("id"):
            try:
                entry = get_entry(result["id"])
                wallet_label = entry.get("wallet_label", f"wallet_{i+1}")
                if not contract_address:
                    contract_address = entry.get("contract_address", "")
            except Exception:
                wallet_label = f"wallet_{i+1}"

        item = {
            "wallet_label": wallet_label,
            "short_label": wallet_label_short(wallet_label, i),
            "approval_id": result.get("id"),
            "tx_hash": result.get("tx_hash", ""),
            "gas_used": result.get("gas_used", 0),
            "block_number": result.get("block_number"),
            "status": result.get("status", "unknown"),
        }

        if result.get("status") == "sent" and result.get("success", True):
            success.append(item)
        elif result.get("status") == "dry_run":
            dry_run.append(item)
        else:
            item["error"] = detect_failure_reason(result.get("error", ""))
            failed.append(item)

    # Get collection name
    collection_name = "NFT"
    if contract_address:
        collection_name = get_collection_name(contract_address, chain)

    # Calculate stats
    total_gas_used = sum(s.get("gas_used", 0) for s in success)
    duration = end_time - start_time
    speed_str = f"{duration:.0f}s" if duration > 0 else "instant"

    # Determine report type
    total = len(execution_results)
    if len(success) == total:
        report_type = "full_success"
    elif len(failed) == total:
        report_type = "full_failed"
    elif len(dry_run) == total:
        report_type = "dry_run"
    elif success and failed:
        report_type = "partial_failed"
    else:
        report_type = "mixed"

    report = {
        "type": report_type,
        "collection_name": collection_name,
        "contract_address": contract_address or "unknown",
        "chain": chain,
        "total": total,
        "success_count": len(success),
        "failed_count": len(failed),
        "dry_run_count": len(dry_run),
        "success": success,
        "failed": failed,
        "dry_run": dry_run,
        "total_gas_used": total_gas_used,
        "duration_seconds": duration,
        "speed": speed_str,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return report


def format_report_telegram(report: dict) -> str:
    """
    Format execution report for Telegram message.
    Compact operator style matching Evelyn's personality.
    """
    report_type = report.get("type", "unknown")
    collection = report.get("collection_name", "NFT")
    success = report.get("success", [])
    failed = report.get("failed", [])
    dry_run = report.get("dry_run", [])
    total = report.get("total", 0)
    gas_used = report.get("total_gas_used", 0)
    speed = report.get("speed", "?")

    lines = []

    # === FULL SUCCESS ===
    if report_type == "full_success":
        # Compact format
        success_count = len(success)

        # Wallet range
        if success_count > 3:
            w_first = success[0]["short_label"]
            w_last = success[-1]["short_label"]
            wallet_range = f"{w_first}-{w_last}"
        else:
            wallet_range = ", ".join(s["short_label"] for s in success)

        lines.append(f"✅ <b>Mint Success</b>")
        lines.append(f"")
        lines.append(f"Contract : {collection}")
        lines.append(f"Wallets  : {wallet_range}")
        lines.append(f"Success  : {success_count}")
        lines.append(f"Failed   : 0")
        lines.append(f"")

        if gas_used > 0:
            from web3 import Web3
            gas_eth = Web3.from_wei(gas_used, "ether")
            gas_per_wallet = gas_used / success_count if success_count > 0 else 0
            gas_per_eth = Web3.from_wei(int(gas_per_wallet), "ether")
            lines.append(f"Gas Total: {gas_eth:.6f} ETH")
            lines.append(f"Gas/wallet: {gas_per_eth:.6f} ETH")

        lines.append(f"Speed    : {speed}")
        lines.append(f"Mode     : Parallel")
        lines.append(f"")
        lines.append(f"NFT : {success_count} {collection}")

        # Show tx hashes (compact)
        if success_count <= 5:
            lines.append(f"")
            for s in success:
                tx = s.get("tx_hash", "")
                tx_short = f"{tx[:10]}..." if tx else "N/A"
                lines.append(f"• {s['short_label']} ✅ <code>{tx_short}</code>")

    # === PARTIAL FAILED ===
    elif report_type == "partial_failed":
        lines.append(f"⚠️ <b>Mint Partial Failed</b>")
        lines.append(f"")
        lines.append(f"Contract : {collection}")
        lines.append(f"")

        # Success list
        lines.append(f"✅ Success ({len(success)}):")
        for s in success[:10]:
            lines.append(f"  {s['short_label']}")
        if len(success) > 10:
            lines.append(f"  ...+{len(success) - 10} more")

        lines.append(f"")

        # Failed list with reasons
        lines.append(f"❌ Failed ({len(failed)}):")
        for f in failed[:10]:
            lines.append(f"  {f['short_label']} - {f.get('error', 'unknown')}")
        if len(failed) > 10:
            lines.append(f"  ...+{len(failed) - 10} more")

        lines.append(f"")
        if gas_used > 0:
            from web3 import Web3
            lines.append(f"Gas Used: {Web3.from_wei(gas_used, 'ether'):.6f} ETH")

    # === FULL FAILED ===
    elif report_type == "full_failed":
        lines.append(f"💥 <b>Mint Failed</b>")
        lines.append(f"")
        lines.append(f"Contract : {collection}")
        lines.append(f"Total    : {total} wallets")
        lines.append(f"Success  : 0")
        lines.append(f"")
        lines.append(f"❌ All failed:")

        # Group by failure reason
        reason_groups = {}
        for f in failed:
            reason = f.get("error", "unknown")
            if reason not in reason_groups:
                reason_groups[reason] = []
            reason_groups[reason].append(f["short_label"])

        for reason, wallets in reason_groups.items():
            wallet_str = ", ".join(wallets[:5])
            if len(wallets) > 5:
                wallet_str += f" +{len(wallets) - 5}"
            lines.append(f"  • {reason}: {wallet_str}")

    # === DRY RUN ===
    elif report_type == "dry_run":
        lines.append(f"🧪 <b>Dry Run Complete</b>")
        lines.append(f"")
        lines.append(f"Contract : {collection}")
        lines.append(f"Wallets  : {len(dry_run)}")
        lines.append(f"Mode     : DRY_RUN (no tx sent)")
        lines.append(f"")
        lines.append(f"Set DRY_RUN=false to execute for real.")

    # === UNKNOWN/MIXED ===
    else:
        lines.append(f"📊 <b>Execution Report</b>")
        lines.append(f"")
        lines.append(f"Contract : {collection}")
        lines.append(f"Total    : {total}")
        lines.append(f"Success  : {len(success)}")
        lines.append(f"Failed   : {len(failed)}")
        lines.append(f"Dry Run  : {len(dry_run)}")

    return "\n".join(lines)


def format_single_mint_report(result: dict, contract_address: str = None, chain: str = "ethereum") -> str:
    """Format a single mint execution result for quick Telegram feedback."""
    status = result.get("status", "unknown")

    if status == "sent" and result.get("success", True):
        collection = get_collection_name(contract_address, chain) if contract_address else "NFT"
        tx_hash = result.get("tx_hash", "")
        tx_short = f"{tx_hash[:14]}..." if tx_hash else "N/A"
        gas = result.get("gas_used", 0)

        lines = [
            f"✅ <b>Mint Success</b>",
            f"",
            f"NFT      : 1 {collection}",
            f"Tx       : <code>{tx_short}</code>",
            f"Block    : {result.get('block_number', '?')}",
        ]
        if gas:
            from web3 import Web3
            lines.append(f"Gas      : {Web3.from_wei(gas, 'ether'):.6f} ETH")
        return "\n".join(lines)

    elif status == "dry_run":
        return "🧪 <b>Dry Run</b> - Transaction simulated, not sent.\nSet DRY_RUN=false to execute."

    elif status == "failed":
        error = detect_failure_reason(result.get("error", ""))
        return f"❌ <b>Mint Failed</b>\nReason: {error}"

    else:
        return f"❓ Status: {status}"
