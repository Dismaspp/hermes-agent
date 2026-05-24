"""
telegram_gateway/bot.py - Evelyn: Telegram AI Mint Operator Bot
================================================================
Commands:
  /start        - Welcome message
  /pending      - List pending approval entries
  /approve <id> - Approve a pending entry
  /reject <id>  - Reject a pending entry
  /status <id>  - Check entry status
  /clear        - Clear AI conversation history
  /wallets      - List stored wallets
  /mint <addr>  - Quick mint analysis

AI Chat (Mint Operator Mode):
  Any text message gets processed by Evelyn:
  - Detects mint intent ("mint ini 0x...", "cek free gak")
  - Shows compact operator analysis
  - Guides wallet selection -> plan -> queue
  - Answers Web3/NFT questions

Inline buttons:
  Approve / Reject / Dry Run preview

SAFETY:
- Only TELEGRAM_ALLOWED_USERS can interact
- Private keys are NEVER shown or logged
- Bot does NOT auto-execute transactions
- AI chat CANNOT trigger blockchain transactions
- All mint plans go through approval_queue

Usage:
    python -m custom_tools.telegram_gateway.bot
"""

import os
import sys
import logging
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from custom_tools.approval_queue import (
    list_queue,
    approve,
    reject,
    get_entry,
    count_pending,
)
from custom_tools.wallet_manager import list_wallets
from custom_tools.telegram_gateway.ai_chat import (
    get_ai_response_with_context,
    clear_conversation,
)
from custom_tools.telegram_gateway.mint_operator import (
    is_mint_intent,
    detect_contract_address,
    analyze_for_mint,
    format_compact_analysis,
    parse_mint_form,
    create_mint_plan_from_chat,
    format_mint_plan_result,
    format_wallet_selection_prompt,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Configuration from environment
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
]
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Per-user state for mint conversation flow
_user_mint_state: dict[int, dict] = {}


def is_authorized(user_id: int) -> bool:
    """Check if user is in allowed list."""
    return user_id in ALLOWED_USERS


def unauthorized_message() -> str:
    return "🚫 Unauthorized. Your user ID is not in TELEGRAM_ALLOWED_USERS."


def format_entry_preview(entry: dict) -> str:
    """Format a queue entry for Telegram display."""
    status_emoji = {
        "pending": "⏳",
        "approved": "✅",
        "rejected": "❌",
        "sent": "📤",
        "failed": "💥",
    }
    emoji = status_emoji.get(entry.get("status", ""), "❓")

    lines = [
        f"{emoji} <b>Entry #{entry['id']}</b> [{entry['status'].upper()}]",
        f"",
        f"<b>Chain:</b> {entry.get('chain', 'N/A')}",
        f"<b>Contract:</b> <code>{entry.get('contract_address', 'N/A')}</code>",
        f"<b>Wallet:</b> {entry.get('wallet_label', 'N/A')}",
        f"<b>Address:</b> <code>{entry.get('from_address', 'N/A')}</code>",
        f"<b>Function:</b> {entry.get('mint_function', 'N/A')}",
        f"<b>Quantity:</b> {entry.get('quantity', 'N/A')}",
        f"<b>Value:</b> {entry.get('total_value_wei', '0')} wei",
        f"<b>Gas Limit:</b> {entry.get('gas_limit', 'N/A')}",
        f"<b>Created:</b> {entry.get('created_at', 'N/A')}",
    ]

    if DRY_RUN:
        lines.append("")
        lines.append("⚠️ <b>DRY_RUN=true</b> - Execution will simulate only")

    return "\n".join(lines)


def get_approval_keyboard(entry_id: int) -> InlineKeyboardMarkup:
    """Get inline keyboard with Approve/Reject buttons."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{entry_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{entry_id}"),
        ],
        [
            InlineKeyboardButton("👁 Preview", callback_data=f"preview_{entry_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# === Command Handlers ===

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    pending = count_pending()
    msg = (
        "👋 <b>Halo sayang! Gw Evelyn.</b>\n\n"
        "AI mint operator lo buat Web3/NFT workflow.\n\n"
        "<b>Commands:</b>\n"
        "  /pending - List pending approvals\n"
        "  /approve &lt;id&gt; - Approve entry\n"
        "  /reject &lt;id&gt; - Reject entry\n"
        "  /status &lt;id&gt; - Check entry\n"
        "  /mint &lt;0x...&gt; - Quick mint analysis\n"
        "  /wallets - List wallets\n"
        "  /clear - Reset chat history\n\n"
        "<b>AI Chat:</b>\n"
        "Ketik apa aja — mint analysis, contract check, "
        "gas tips, atau sekedar ngobrol.\n\n"
        f"Pending: <b>{pending}</b> | DRY_RUN: <b>{'ON' if DRY_RUN else 'OFF'}</b>\n"
        f"Your ID: <code>{user_id}</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pending command."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    entries = list_queue(status="pending", limit=10)
    if not entries:
        await update.message.reply_text("✅ Queue kosong sayang, ga ada pending.")
        return

    await update.message.reply_text(
        f"⏳ <b>{len(entries)} Pending:</b>", parse_mode="HTML",
    )
    for entry in entries:
        text = format_entry_preview(entry)
        keyboard = get_approval_keyboard(entry["id"])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /approve <id> command."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <id>")
        return
    try:
        entry_id = int(context.args[0])
        approve(entry_id, approved_by=f"telegram:{user_id}")
        await update.message.reply_text(
            f"✅ Entry #{entry_id} <b>APPROVED</b>", parse_mode="HTML",
        )
    except (ValueError, Exception) as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reject <id> command."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return
    if not context.args:
        await update.message.reply_text("Usage: /reject <id> [reason]")
        return
    try:
        entry_id = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else f"Rejected by telegram:{user_id}"
        reject(entry_id, reason=reason)
        await update.message.reply_text(
            f"❌ Entry #{entry_id} <b>REJECTED</b>\n{reason}", parse_mode="HTML",
        )
    except (ValueError, Exception) as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status <id> command."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return
    if not context.args:
        await update.message.reply_text("Usage: /status <id>")
        return
    try:
        entry_id = int(context.args[0])
        entry = get_entry(entry_id)
        await update.message.reply_text(format_entry_preview(entry), parse_mode="HTML")
    except (ValueError, Exception) as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /wallets command - list stored wallets."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    wallets = list_wallets()
    if not wallets:
        await update.message.reply_text("Belum ada wallet sayang. Buat dulu via CLI.")
        return

    lines = [f"<b>Wallets ({len(wallets)}):</b>\n"]
    for i, w in enumerate(wallets, 1):
        lines.append(f"  W{i}. <b>{w['label']}</b> <code>{w['address'][:14]}...</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_mint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mint <address> - quick mint analysis."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    if not context.args:
        await update.message.reply_text("Usage: /mint <contract_address> [--chain base]")
        return

    contract = context.args[0]
    chain = "ethereum"
    if "--chain" in context.args:
        idx = context.args.index("--chain")
        if idx + 1 < len(context.args):
            chain = context.args[idx + 1]

    await update.message.reply_text("🔍 aku cek dulu ya sayang...")
    await update.message.chat.send_action("typing")

    analysis = analyze_for_mint(contract, chain)
    text = format_compact_analysis(analysis)

    # Store state for follow-up
    if "error" not in analysis:
        _user_mint_state[user_id] = {
            "contract": analysis.get("contract"),
            "chain": chain,
            "analysis": analysis,
            "awaiting": "wallet_selection",
        }
        text += "\n\nmau pakai wallet mana sayang?"
        wallets = list_wallets()
        if wallets:
            for i, w in enumerate(wallets, 1):
                text += f"\n  {i}. {w['label']}"
            text += f"\n  all = semua ({len(wallets)})"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear command."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return
    clear_conversation(user_id)
    _user_mint_state.pop(user_id, None)
    await update.message.reply_text("🧹 Chat + mint state cleared!")


# === AI Chat + Mint Operator Handler ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle all non-command text messages.
    Priority:
    1. Check if user is in mint conversation flow (awaiting wallet selection)
    2. Check if message is mint intent with contract address -> operator mode
    3. Fallback to AI chat via OpenRouter
    """
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    text = update.message.text
    if not text:
        return

    # --- FLOW 1: Mint conversation state (awaiting wallet selection) ---
    state = _user_mint_state.get(user_id)
    if state and state.get("awaiting") == "wallet_selection":
        await _handle_wallet_selection(update, user_id, text, state)
        return

    # --- FLOW 2: Mint intent with contract address ---
    contract = detect_contract_address(text)
    if contract and is_mint_intent(text):
        await _handle_mint_intent(update, user_id, text, contract)
        return

    # --- FLOW 3: AI chat fallback ---
    await update.message.chat.send_action("typing")
    response = await get_ai_response_with_context(user_id, text)

    # Split long messages
    if len(response) <= 4096:
        await update.message.reply_text(response)
    else:
        for i in range(0, len(response), 4096):
            await update.message.reply_text(response[i:i + 4096])


async def _handle_mint_intent(update: Update, user_id: int, text: str, contract: str):
    """Handle detected mint intent - analyze and prompt wallet selection."""
    chain = "ethereum"
    if "base" in text.lower():
        chain = "base"
    elif "arb" in text.lower():
        chain = "arbitrum"
    elif "polygon" in text.lower() or "matic" in text.lower():
        chain = "polygon"

    await update.message.reply_text("siapp sayang 😈 aku cek dulu kontraknya ya...")
    await update.message.chat.send_action("typing")

    analysis = analyze_for_mint(contract, chain)
    compact = format_compact_analysis(analysis)

    if "error" in analysis:
        await update.message.reply_text(compact, parse_mode="HTML")
        return

    # Store state
    _user_mint_state[user_id] = {
        "contract": analysis.get("contract"),
        "chain": chain,
        "analysis": analysis,
        "function_name": analysis.get("function_name"),
        "mint_price_wei": 0 if analysis.get("is_free") else None,
        "awaiting": "wallet_selection",
    }

    reply = compact + "\n\n" + format_wallet_selection_prompt()
    await update.message.reply_text(reply, parse_mode="HTML")


async def _handle_wallet_selection(update: Update, user_id: int, text: str, state: dict):
    """Handle wallet selection in mint conversation flow."""
    text_lower = text.lower().strip()

    all_wallets = False
    wallet_label = None

    # Parse wallet choice
    if text_lower in ("all", "semua", "semua wallet"):
        all_wallets = True
    elif text_lower.isdigit():
        # Numeric selection
        wallets = list_wallets()
        idx = int(text_lower) - 1
        if 0 <= idx < len(wallets):
            wallet_label = wallets[idx]["label"]
        else:
            await update.message.reply_text(f"❌ Wallet #{text_lower} ga ada sayang. Pilih lagi.")
            return
    else:
        # Assume label name
        wallet_label = text_lower

    # Create mint plan
    await update.message.reply_text("oke letsgo 🔥 preparing mint plan...")
    await update.message.chat.send_action("typing")

    results = create_mint_plan_from_chat(
        contract_address=state["contract"],
        wallet_label=wallet_label,
        all_wallets=all_wallets,
        chain=state.get("chain", "ethereum"),
        quantity=1,
        mint_function=state.get("function_name"),
        mint_price_wei=state.get("mint_price_wei"),
    )

    reply = format_mint_plan_result(results)
    await update.message.reply_text(reply, parse_mode="HTML")

    # Clear state
    _user_mint_state.pop(user_id, None)


# === Callback Query Handler (Inline Buttons) ===

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_authorized(user_id):
        await query.answer("Unauthorized", show_alert=True)
        return

    data = query.data
    parts = data.split("_", 1)
    if len(parts) != 2:
        await query.answer("Invalid action")
        return

    action, entry_id_str = parts[0], parts[1]
    try:
        entry_id = int(entry_id_str)
    except ValueError:
        await query.answer("Invalid entry ID")
        return

    try:
        if action == "approve":
            approve(entry_id, approved_by=f"telegram:{user_id}")
            await query.answer(f"✅ #{entry_id} APPROVED")
            await query.edit_message_text(
                f"✅ <b>APPROVED</b> - Entry #{entry_id}\nBy: user {user_id}",
                parse_mode="HTML",
            )
        elif action == "reject":
            reject(entry_id, reason=f"Rejected via button by user {user_id}")
            await query.answer(f"❌ #{entry_id} REJECTED")
            await query.edit_message_text(
                f"❌ <b>REJECTED</b> - Entry #{entry_id}\nBy: user {user_id}",
                parse_mode="HTML",
            )
        elif action == "preview":
            entry = get_entry(entry_id)
            text = format_entry_preview(entry)
            text += "\n\n🔍 <b>Preview only. No tx sent.</b>"
            await query.answer("Preview loaded")
            keyboard = get_approval_keyboard(entry_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await query.answer("Unknown action")
    except (ValueError, Exception) as e:
        await query.answer(f"Error: {e}", show_alert=True)


# === Main ===

def main():
    """Start Evelyn - Telegram AI Mint Operator Bot."""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)
    if not ALLOWED_USERS:
        print("ERROR: TELEGRAM_ALLOWED_USERS not set")
        sys.exit(1)

    openrouter = os.getenv("OPENROUTER_API_KEY", "")
    print(f"Starting Evelyn - AI Mint Operator Bot 😈")
    print(f"Allowed users: {ALLOWED_USERS}")
    print(f"DRY_RUN: {DRY_RUN}")
    print(f"AI Chat: {'ON' if openrouter else 'OFF (no OPENROUTER_API_KEY)'}")
    print(f"Model: {os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini')}")
    print()

    app = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("wallets", cmd_wallets))
    app.add_handler(CommandHandler("mint", cmd_mint))
    app.add_handler(CommandHandler("clear", cmd_clear))

    # Inline button handler
    app.add_handler(CallbackQueryHandler(button_callback))

    # AI chat + mint operator (catches all non-command text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot running. Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
