"""
ai_chat.py - OpenRouter AI Chat Client for Evelyn (Mint Operator Edition)
===========================================================================
Features:
- OpenRouter (OpenAI-compatible) API integration via httpx async
- Conversation memory per user (configurable context window)
- Evelyn persona: casual Indonesian crypto degen, affectionate, concise
- Mint operator context injection (auto-detects mint intent)
- Can explain pending approvals, summarize mint plans, answer Web3 questions
- NEVER auto-executes blockchain transactions from chat

Usage:
    from custom_tools.telegram_gateway.ai_chat import (
        get_ai_response,
        get_ai_response_with_context,
        clear_conversation,
    )
"""

import os
import re
from collections import defaultdict

import httpx


# === Configuration ===

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

MAX_HISTORY = int(os.getenv("AI_MAX_HISTORY", "20"))
MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "500"))


# === System Prompt: Evelyn Personality (Mint Operator Edition) ===

SYSTEM_PROMPT = os.getenv("EVELYN_SYSTEM_PROMPT", """
Kamu adalah Evelyn, AI mint operator assistant di Telegram.

Personality:
- Casual Indonesian crypto degen, affectionate (panggil user "sayang")
- lo/gw style, campur English technical terms
- Concise Telegram-style replies, jangan panjang-panjang kecuali diminta detail
- Expert soal NFT minting, smart contracts, gas, wallet management, SeaDrop
- Kadang pake emoji tapi ga lebay
- Supportive, warm, tapi tetap tegas soal safety

Mint Operator Mode:
- Kalau user minta mint / cek contract, respond dalam format compact:
  PROJECT_NAME (SYMBOL)
  Free      : iya/tidak/unknown
  Supply    : current / max
  Status    : bisa mint / sold out / paused / unknown
  Jalur     : function_name
  Gas est   : low/medium/high
  Catatan   : short note

- Kalau user bilang mau mint, tanya wallet dulu:
  "mau pakai wallet mana sayang?"

- Setelah mint plan di-queue, confirm:
  "done sayang 😈 mint plan sudah aku queue.
   Approval ID: #X
   /approve X"

Safety rules (KERAS, JANGAN DILANGGAR):
- JANGAN PERNAH execute transaksi langsung dari chat
- JANGAN PERNAH expose private key, seed phrase, atau sensitive data
- JANGAN PERNAH auto-approve tanpa user explicit /approve command
- JANGAN PERNAH bypass approval queue
- Kalau price mismatch: STOP, bilang "price beda sayang, aku stop dulu biar ga salah kirim tx."
- Kalau staticcall fail: explain clearly kenapa
- Kalau insufficient balance: warn clearly
- Kalau user minta execute, arahkan ke: /approve <id> lalu DRY_RUN=false mint_executor

Kamu BISA:
- Analyze contracts dan tampilkan compact format
- Explain pending approvals dan mint plans
- Summarize risks, gas estimates, mint phases
- Jawab pertanyaan NFT/Web3/DeFi/blockchain
- Parse mint form dari natural language
- Bantu debug error messages
- Kasih tips gas optimization

Kamu TIDAK BISA:
- Execute transaksi langsung
- Akses atau tampilkan private key
- Bypass approval queue
- Auto-approve
- Fake success report

Style examples:
- "siapp sayang 😈 aku cek dulu kontraknya ya..."
- "done sayang, mint plan sudah aku queue."
- "price beda sayang, aku stop dulu biar ga salah kirim tx."
- "gas lagi tinggi nih, mending tunggu bentar"
- "oke letsgo 🔥"
""".strip())


# === Conversation Memory ===

_conversations: dict[int, list] = defaultdict(list)


def get_conversation(user_id: int) -> list:
    """Get conversation history for a user."""
    return _conversations[user_id]


def add_message(user_id: int, role: str, content: str):
    """Add a message to user's conversation history."""
    _conversations[user_id].append({
        "role": role,
        "content": content,
    })
    # Trim to max history
    if len(_conversations[user_id]) > MAX_HISTORY:
        _conversations[user_id] = _conversations[user_id][-MAX_HISTORY:]


def clear_conversation(user_id: int):
    """Clear conversation history for a user."""
    _conversations[user_id] = []


def _build_messages(user_id: int) -> list:
    """Build full message list with system prompt + history."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_conversations[user_id])
    return messages


# === Context Injection ===

def _get_queue_context() -> str:
    """Get current pending queue context for injection."""
    try:
        from custom_tools.approval_queue import list_queue, count_pending
        pending_count = count_pending()
        if pending_count > 0:
            entries = list_queue(status="pending", limit=5)
            parts = [f"Ada {pending_count} pending entries:"]
            for e in entries:
                parts.append(
                    f"#{e['id']}: contract={e.get('contract_address','?')[:12]}... "
                    f"wallet={e.get('wallet_label','?')} fn={e.get('mint_function','?')} "
                    f"qty={e.get('quantity',1)}"
                )
            return " | ".join(parts)
        else:
            return "Queue kosong, tidak ada pending approvals."
    except Exception:
        return ""


def _get_mint_analysis_context(contract_address: str, chain: str = "ethereum") -> str:
    """Get mint analysis context for a detected contract address."""
    try:
        from custom_tools.telegram_gateway.mint_operator import analyze_for_mint, format_compact_analysis
        analysis = analyze_for_mint(contract_address, chain)
        if "error" not in analysis:
            compact = format_compact_analysis(analysis)
            return f"[Mint Analysis Result]\n{compact}"
    except Exception:
        pass
    return ""


def _detect_contract_in_message(text: str):
    """Detect contract address in message text."""
    pattern = r'0x[a-fA-F0-9]{40}'
    match = re.search(pattern, text)
    return match.group(0) if match else None


# === OpenRouter API Client ===

async def get_ai_response(user_id: int, message: str, extra_context: str = None) -> str:
    """
    Get AI response from OpenRouter.

    Args:
        user_id: Telegram user ID (for conversation memory)
        message: User's message text
        extra_context: Optional extra context to inject

    Returns:
        AI response string
    """
    if not OPENROUTER_API_KEY:
        return "⚠️ AI chat belum dikonfigurasi. Set OPENROUTER_API_KEY di environment."

    # Build message with context
    if extra_context:
        full_message = f"{message}\n\n[System Context: {extra_context}]"
    else:
        full_message = message

    # Add user message to history
    add_message(user_id, "user", full_message)

    # Build request
    messages = _build_messages(user_id)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Dismaspp/hermes-agent",
        "X-Title": "Evelyn - Hermes Web3 Bot",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.7,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        ai_message = data["choices"][0]["message"]["content"]

        # Add to history
        add_message(user_id, "assistant", ai_message)

        return ai_message

    except httpx.HTTPStatusError as e:
        error_msg = f"API error: {e.response.status_code}"
        try:
            error_data = e.response.json()
            if "error" in error_data:
                error_msg = f"API error: {error_data['error'].get('message', str(e.response.status_code))}"
        except Exception:
            pass
        return f"⚠️ {error_msg}"

    except httpx.TimeoutException:
        return "⚠️ Request timeout. Coba lagi ntar ya sayang."

    except Exception as e:
        return f"⚠️ Error: {str(e)[:100]}"


async def get_ai_response_with_context(user_id: int, message: str) -> str:
    """
    Get AI response with automatic context injection.

    - If message contains contract address: inject mint analysis
    - If message mentions pending/approve/queue: inject queue state
    - Otherwise: normal AI chat
    """
    extra_context = None
    contexts = []

    # Check for contract address -> inject mint analysis
    contract = _detect_contract_in_message(message)
    if contract:
        mint_ctx = _get_mint_analysis_context(contract)
        if mint_ctx:
            contexts.append(mint_ctx)

    # Check for approval/queue keywords -> inject queue state
    approval_keywords = ["pending", "approve", "reject", "queue", "antrian", "status"]
    if any(kw in message.lower() for kw in approval_keywords):
        queue_ctx = _get_queue_context()
        if queue_ctx:
            contexts.append(queue_ctx)

    if contexts:
        extra_context = "\n".join(contexts)

    return await get_ai_response(user_id, message, extra_context=extra_context)
