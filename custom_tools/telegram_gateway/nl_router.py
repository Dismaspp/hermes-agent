"""
nl_router.py - Natural Language Router for Wallet & Mint Commands
==================================================================
Detects Indonesian natural language intent from Telegram messages
and routes to appropriate tool functions.

Supported intents:
- WALLET_CREATE: "buat wallet baru", "bikin burner wallet", etc
- WALLET_LIST: "lihat wallet aku", "list wallet", "wallet aku"
- WALLET_BALANCE: "cek balance wallet", "balance burner1"
- WALLET_DELETE: "hapus wallet burner5", "delete wallet burner5"
- MINT_ANALYZE: "mint ini 0x...", "cek mint 0x...", "analyze mint"
- MINT_PLAN: "buat mint plan", "queue mint", "gas mint"
- MINT_ALL: "mint semua wallet", "mint all wallets"

Returns:
    dict with 'intent', 'params', and optionally 'address', 'label', 'chain'
    or None if no intent matched (falls through to AI chat)

SAFETY:
- NEVER executes transactions
- Only analyzes, plans, and queues
- All mint operations create PENDING approval entries
"""

import re


# === Intent Detection Patterns ===

WALLET_CREATE_PATTERNS = [
    r"buat\s*wallet\s*baru",
    r"bikin\s*wallet\s*baru",
    r"buatin\s*(?:burner\s*)?wallet",
    r"create\s*wallet\s*baru",
    r"buat\s*burner\s*wallet",
    r"bikin\s*burner\s*wallet",
    r"tambah\s*wallet",
]

WALLET_LIST_PATTERNS = [
    r"lihat\s*wallet\s*(?:aku|ku|gw|gue)?",
    r"list\s*wallet",
    r"wallet\s*(?:aku|ku|gw|gue)",
    r"cek\s*semua\s*wallet",
    r"semua\s*wallet",
    r"daftar\s*wallet",
]

WALLET_BALANCE_PATTERNS = [
    r"(?:cek|check)\s*balance\s*(?:wallet\s*)?(\w+)?",
    r"balance\s*(?:wallet\s*)?(\w+)",
    r"saldo\s*(?:wallet\s*)?(\w+)?",
]

WALLET_DELETE_PATTERNS = [
    r"(?:hapus|delete|remove)\s*wallet\s*(\w+)",
]

MINT_ANALYZE_PATTERNS = [
    r"(?:mint|cek\s*mint|analyze\s*mint|gas\s*mint)\s*(?:ini\s*)?(0x[a-fA-F0-9]{40})",
    r"(?:mint|cek\s*mint)\s*(?:contract\s*)?(0x[a-fA-F0-9]{40})",
    r"(?:free\s*mint|public\s*mint)\s*(?:ini\s*)?(0x[a-fA-F0-9]{40})",
    r"(?:buat\s*mint\s*plan|queue\s*mint)\s*(?:ini\s*)?(0x[a-fA-F0-9]{40})",
]

MINT_ALL_PATTERNS = [
    r"mint\s*(?:semua|all)\s*wallet\s*(0x[a-fA-F0-9]{40})?",
    r"mint\s*(?:pakai|pake)\s*semua\s*wallet\s*(0x[a-fA-F0-9]{40})?",
]

# Wallet label extraction from create commands
WALLET_LABEL_PATTERN = r"(?:label|nama)\s*(\w+)|wallet\s+(\w+)$"


def detect_intent(text: str) -> dict | None:
    """
    Detect natural language intent from user message.

    Args:
        text: User message text

    Returns:
        dict with 'intent' and 'params' or None if no match
    """
    text_lower = text.lower().strip()

    # --- WALLET CREATE ---
    for pattern in WALLET_CREATE_PATTERNS:
        if re.search(pattern, text_lower):
            # Try to extract label
            label = _extract_wallet_label(text_lower)
            return {
                "intent": "WALLET_CREATE",
                "label": label,
                "raw_text": text,
            }

    # --- WALLET LIST ---
    for pattern in WALLET_LIST_PATTERNS:
        if re.search(pattern, text_lower):
            return {
                "intent": "WALLET_LIST",
                "raw_text": text,
            }

    # --- WALLET DELETE ---
    for pattern in WALLET_DELETE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            label = match.group(1)
            return {
                "intent": "WALLET_DELETE",
                "label": label,
                "raw_text": text,
            }

    # --- WALLET BALANCE ---
    for pattern in WALLET_BALANCE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            label = match.group(1) if match.lastindex and match.group(1) else None
            return {
                "intent": "WALLET_BALANCE",
                "label": label,
                "raw_text": text,
            }

    # --- MINT ALL WALLETS ---
    for pattern in MINT_ALL_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            address = match.group(1) if match.lastindex and match.group(1) else None
            return {
                "intent": "MINT_ALL",
                "address": address,
                "raw_text": text,
            }

    # --- MINT ANALYZE / PLAN ---
    for pattern in MINT_ANALYZE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            address = match.group(1)
            # Detect if user specified wallet
            wallet_label = _extract_mint_wallet(text_lower)
            # Detect gas preference
            gas_gwei = _extract_gas_preference(text_lower)
            return {
                "intent": "MINT_ANALYZE",
                "address": address,
                "wallet_label": wallet_label,
                "gas_gwei": gas_gwei,
                "chain": _detect_chain(text_lower),
                "raw_text": text,
            }

    return None


def _extract_wallet_label(text: str) -> str | None:
    """Extract wallet label from create wallet message."""
    # Pattern: "buat wallet baru label burner5"
    match = re.search(r"(?:label|nama)\s+(\w+)", text)
    if match:
        return match.group(1)

    # Pattern: "buat wallet burner5"
    match = re.search(r"(?:buat|bikin|create)\s*(?:burner\s*)?wallet\s*(?:baru\s*)?(\w+)", text)
    if match:
        label = match.group(1)
        # Filter out noise words
        noise = {"baru", "aku", "dong", "ya", "sayang", "beb", "gw", "gue", "ini"}
        if label not in noise:
            return label

    return None


def _extract_mint_wallet(text: str) -> str | None:
    """Extract wallet label from mint command."""
    # "mint pake wallet test1" / "mint pakai wallet burner2"
    match = re.search(r"(?:pake|pakai|wallet|with)\s+(\w+)", text)
    if match:
        label = match.group(1)
        noise = {"wallet", "semua", "all", "ini", "itu"}
        if label not in noise:
            return label
    return None


def _extract_gas_preference(text: str) -> int | None:
    """Extract gas/gwei preference from text."""
    # "gas 5 gwei" / "pake 10 gwei" / "max gas 8 gwei"
    match = re.search(r"(?:gas|gwei)\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*gwei", text)
    if match:
        return int(match.group(1))
    return None


def _detect_chain(text: str) -> str:
    """Detect chain from text."""
    if "base" in text:
        return "base"
    elif "arb" in text or "arbitrum" in text:
        return "arbitrum"
    elif "polygon" in text or "matic" in text:
        return "polygon"
    return "ethereum"
