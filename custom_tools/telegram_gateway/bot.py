"""
telegram_gateway/bot.py - Telegram AI Approval Bot (Evelyn)
=============================================================
Commands:
  /start        - Welcome message
  /pending      - List pending approval entries
  /approve <id> - Approve a pending entry
  /reject <id>  - Reject a pending entry
  /status <id>  - Check entry status
  /clear        - Clear AI conversation history

AI Chat:
  Any normal text message gets an AI response from Evelyn
  (casual Indonesian crypto degen assistant via OpenRouter)

Inline buttons:
  Approve / Reject / Dry Run preview

SAFETY:
- Only TELEGRAM_ALLOWED_USERS can approve/reject
- Private keys are NEVER shown or logged
- Bot does NOT auto-execute transactions
- AI chat CANNOT trigger blockchain transactions

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
)
from custom_tools.telegram_gateway.ai_chat import (
    get_ai_response_with_queue_context,
    clear_conversation,
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
            InlineKeyboardButton("👁 Dry Run Preview", callback_data=f"preview_{entry_id}"),
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

    msg = (
        "👋 <b>Halo! Gw Evelyn.</b>\n\n"
        "AI assistant lo buat Web3/NFT approval workflow.\n\n"
        "<b>Commands:</b>\n"
        "  /pending - List pending approvals\n"
        "  /approve &lt;id&gt; - Approve entry\n"
        "  /reject &lt;id&gt; - Reject entry\n"
        "  /status &lt;id&gt; - Check entry status\n"
        "  /clear - Clear chat history\n\n"
        "<b>AI Chat:</b>\n"
        "Ketik apa aja — gw bisa bantu soal NFT, contracts, "
        "mint plans, gas, atau sekedar ngobrol.\n\n"
        f"DRY_RUN: <b>{'ON' if DRY_RUN else 'OFF'}</b>\n"
        f"Your ID: <code>{user_id}</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pending command - list pending entries."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    entries = list_queue(status="pending", limit=10)

    if not entries:
        await update.message.reply_text("✅ Queue kosong, ga ada pending approvals.")
        return

    await update.message.reply_text(
        f"⏳ <b>{len(entries)} Pending Approval(s):</b>",
        parse_mode="HTML",
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
            f"✅ Entry #{entry_id} <b>APPROVED</b> by user {user_id}",
            parse_mode="HTML",
        )
    except ValueError as e:
        await update.message.reply_text(f"❌ Error: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


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
            f"❌ Entry #{entry_id} <b>REJECTED</b>\nReason: {reason}",
            parse_mode="HTML",
        )
    except ValueError as e:
        await update.message.reply_text(f"❌ Error: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


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
        text = format_entry_preview(entry)
        await update.message.reply_text(text, parse_mode="HTML")
    except ValueError as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear command - clear AI conversation history."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    clear_conversation(user_id)
    await update.message.reply_text("🧹 Chat history cleared. Fresh start!")


# === AI Chat Handler ===

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle normal text messages with AI response."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text(unauthorized_message())
        return

    message_text = update.message.text
    if not message_text:
        return

    # Show typing indicator
    await update.message.chat.send_action("typing")

    # Get AI response
    response = await get_ai_response_with_queue_context(user_id, message_text)

    # Send response (split if too long for Telegram's 4096 char limit)
    if len(response) <= 4096:
        await update.message.reply_text(response)
    else:
        # Split into chunks
        for i in range(0, len(response), 4096):
            await update.message.reply_text(response[i:i + 4096])


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
            await query.answer(f"✅ Entry #{entry_id} APPROVED")
            await query.edit_message_text(
                f"✅ <b>APPROVED</b> - Entry #{entry_id}\nBy: user {user_id}",
                parse_mode="HTML",
            )

        elif action == "reject":
            reject(entry_id, reason=f"Rejected via Telegram button by user {user_id}")
            await query.answer(f"❌ Entry #{entry_id} REJECTED")
            await query.edit_message_text(
                f"❌ <b>REJECTED</b> - Entry #{entry_id}\nBy: user {user_id}",
                parse_mode="HTML",
            )

        elif action == "preview":
            entry = get_entry(entry_id)
            text = format_entry_preview(entry)
            text += "\n\n🔍 <b>Preview only. No transaction sent.</b>"
            await query.answer("Preview loaded")
            keyboard = get_approval_keyboard(entry_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

        else:
            await query.answer("Unknown action")

    except ValueError as e:
        await query.answer(f"Error: {e}", show_alert=True)
    except Exception as e:
        await query.answer(f"Error: {e}", show_alert=True)


# === Main ===

def main():
    """Start the Telegram bot with AI chat support."""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in environment")
        print("Set it in .env: TELEGRAM_BOT_TOKEN=your-bot-token")
        sys.exit(1)

    if not ALLOWED_USERS:
        print("ERROR: TELEGRAM_ALLOWED_USERS not set in environment")
        print("Set it in .env: TELEGRAM_ALLOWED_USERS=123456789,987654321")
        sys.exit(1)

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

    print(f"Starting Evelyn - Hermes Web3 AI Bot...")
    print(f"Allowed users: {ALLOWED_USERS}")
    print(f"DRY_RUN: {DRY_RUN}")
    print(f"AI Chat: {'ENABLED' if openrouter_key else 'DISABLED (no OPENROUTER_API_KEY)'}")
    print(f"Model: {os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini')}")
    print()

    app = Application.builder().token(BOT_TOKEN).build()

    # Register command handlers (higher priority)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("clear", cmd_clear))

    # Inline button handler
    app.add_handler(CallbackQueryHandler(button_callback))

    # AI chat handler (catches all non-command text messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    # Start polling
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
