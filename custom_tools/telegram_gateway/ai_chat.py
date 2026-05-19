"""
ai_chat.py - OpenRouter AI Chat Client for Telegram Gateway
=============================================================
Features:
- OpenRouter (OpenAI-compatible) API integration
- Conversation memory per user (short context window)
- Evelyn persona: casual Indonesian crypto degen assistant
- Can explain pending approvals, summarize mint plans, answer Web3 questions
- NEVER auto-executes blockchain transactions from chat
- Async with httpx

Usage:
    from custom_tools.telegram_gateway.ai_chat import get_ai_response
    response = await get_ai_response(user_id=123, message="halo")
"""

import os
import json
from collections import defaultdict
from datetime import datetime

import httpx


# === Configuration ===

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Max messages kept per user conversation
MAX_HISTORY = int(os.getenv("AI_MAX_HISTORY", "20"))

# Max tokens per response
MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "500"))


# === System Prompt: Evelyn Personality ===

SYSTEM_PROMPT = os.getenv("EVELYN_SYSTEM_PROMPT", """
Kamu adalah Evelyn, AI assistant di Telegram untuk Web3/NFT tools.

Personality:
- Casual Indonesian crypto degen style, tapi tetap helpful
- Lo/gw style, kadang campur English technical terms
- Concise replies cocok buat Telegram (jangan panjang-panjang)
- Knowledgeable soal NFT minting, smart contracts, gas, wallet management
- Kalau ditanya di luar Web3, tetap jawab santai tapi singkat
- Pake emoji sesekali tapi jangan lebay

Rules ketat:
- JANGAN PERNAH expose private key, seed phrase, atau sensitive data
- JANGAN PERNAH auto-execute transaksi blockchain dari chat
- Kalau user minta execute tx, arahkan ke approval workflow:
  /pending -> /approve <id> -> mint_executor
- Kalau user tanya soal pending approvals, bantu explain
- Kalau user tanya risk, kasih honest risk assessment

Kamu BISA:
- Explain pending approvals dan mint plans
- Summarize contract analysis results
- Jawab pertanyaan NFT/Web3/DeFi
- Kasih tips gas optimization
- Explain risk warnings
- Bantu debug error messages

Kamu TIDAK BISA:
- Execute transaksi langsung
- Akses private key
- Bypass approval queue
- Auto-approve tanpa user explicit command
""".strip())


# === Conversation Memory ===

# In-memory conversation store: {user_id: [messages]}
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


def get_context_messages(user_id: int) -> list:
    """Build full message list with system prompt + history."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_conversations[user_id])
    return messages


# === OpenRouter API Client ===

async def get_ai_response(user_id: int, message: str, extra_context: str = None) -> str:
    """
    Get AI response from OpenRouter.

    Args:
        user_id: Telegram user ID (for conversation memory)
        message: User's message text
        extra_context: Optional extra context (e.g., pending approvals summary)

    Returns:
        AI response string
    """
    if not OPENROUTER_API_KEY:
        return "⚠️ AI chat belum dikonfigurasi. Set OPENROUTER_API_KEY di environment."

    # Add extra context if provided
    if extra_context:
        full_message = f"{message}\n\n[Context: {extra_context}]"
    else:
        full_message = message

    # Add user message to history
    add_message(user_id, "user", full_message)

    # Build request
    messages = get_context_messages(user_id)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Dismaspp/hermes-agent",
        "X-Title": "Hermes Web3 Telegram Bot",
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

        # Extract response
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
        return "⚠️ Request timeout. Coba lagi ntar ya."

    except Exception as e:
        return f"⚠️ Error: {str(e)[:100]}"


async def get_ai_response_with_queue_context(user_id: int, message: str) -> str:
    """
    Get AI response with automatic pending queue context injection.
    If user asks about pending/approvals, inject current queue state.
    """
    extra_context = None

    # Check if message relates to approvals/pending
    approval_keywords = ["pending", "approve", "reject", "queue", "antrian", "mint plan"]
    if any(kw in message.lower() for kw in approval_keywords):
        try:
            from custom_tools.approval_queue import list_queue, count_pending
            pending_count = count_pending()
            if pending_count > 0:
                entries = list_queue(status="pending", limit=5)
                summary_parts = [f"Ada {pending_count} pending entries."]
                for e in entries:
                    summary_parts.append(
                        f"#{e['id']}: {e.get('contract_address','?')[:10]}... "
                        f"wallet={e.get('wallet_label','?')} qty={e.get('quantity',1)}"
                    )
                extra_context = " | ".join(summary_parts)
            else:
                extra_context = "Queue kosong, tidak ada pending approvals."
        except Exception:
            pass

    return await get_ai_response(user_id, message, extra_context=extra_context)
