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

AI Chat:
  Any text -> Evelyn responds with personality
  Auto-detects: 0x addresses, OpenSea links, image/voice requests

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

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
    return user_id in ALLOWED_USERS


def unauthorized_message() -> str:
    return "🚫 Unauthorized. Your user ID is not in TELEGRAM_ALLOWED_USERS."


def format_entry(entry: dict) -> str:
    emojis = {"pending": "⏳", "approved": "✅", "rejected": "❌", "sent": "📤", "failed": "💥"}
    e = emojis.get(entry.get("status", ""), "❓")
    lines = [
        f"{e} <b>Entry #{entry['id']}</b> [{entry['status'].upper()}]",
        f"",
        f"<b>Chain:</b> {entry.get('chain', 'N/A')}",
        f"<b>Contract:</b> <code>{entry.get('contract_address', 'N/A')}</code>",
        f"<b>Wallet:</b> {entry.get('wallet_label', 'N/A')}",
        f"<b>Function:</b> {entry.get('mint_function', 'N/A')}",
        f"<b>Quantity:</b> {entry.get('quantity', 'N/A')}",
        f"<b>Value:</b> {entry.get('total_value_wei', '0')} wei",
        f"<b>Gas:</b> {entry.get('gas_limit', 'N/A')}",
    ]
    if DRY_RUN:
        lines.append(f"\n⚠️ <b>DRY_RUN=true</b>")
    return "\n".join(lines)


def approval_kb(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{entry_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{entry_id}"),
        ],
        [
            InlineKeyboardButton("👁 Preview", callback_data=f"preview_{entry_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ═══════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════

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
        await update.message.reply_text(format_entry(entry), parse_mode="HTML", reply_markup=approval_kb(entry["id"]))


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


async def cmd_createwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /createwallet <label>")
    label = context.args[0]
    try:
        from custom_tools.wallet_manager import create_burner_wallet
        result = create_burner_wallet(label)
        await update.message.reply_text(
            f"✅ Wallet created sayang!\n\n"
            f"<b>Label:</b> {result['label']}\n"
            f"<b>Address:</b> <code>{result['address']}</code>\n\n"
            f"🔐 Private key stored encrypted. NEVER shared.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_walletbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /walletbalance <label> [chain]")
    label = context.args[0]
    chain = context.args[1] if len(context.args) > 1 else "ethereum"
    try:
        from custom_tools.wallet_manager import check_wallet_balance
        result = check_wallet_balance(label, chain)
        await update.message.reply_text(
            f"👛 <b>Wallet Balance</b>\n\n"
            f"<b>Label:</b> {result['label']}\n"
            f"<b>Address:</b> <code>{result['address']}</code>\n"
            f"<b>Chain:</b> {result['chain']}\n"
            f"<b>Balance:</b> {result['balance_eth']} ETH",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_floor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /floor <collection-slug>")
    slug = context.args[0].lower().strip()
    detected = detect_opensea_slug(slug)
    if detected:
        slug = detected
    await update.message.chat.send_action("typing")
    result = await get_floor_price(slug)
    await update.message.reply_text(result, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /risk <0x...> [chain]")
    addr = context.args[0]
    chain = context.args[1] if len(context.args) > 1 else "ethereum"
    await update.message.chat.send_action("typing")
    result = await analyze_risk(addr, uid, chain)
    for i in range(0, len(result), 4096):
        await update.message.reply_text(result[i:i+4096], parse_mode="HTML", disable_web_page_preview=True)


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /generate <prompt>\nContoh: /generate cyberpunk cat nft")
    prompt = " ".join(context.args)
    await update.message.reply_text("siapp sayang 😈\nlagi aku generate dulu...")
    await update.message.chat.send_action("upload_photo")
    result = await generate_image(prompt)
    if "error" in result:
        await update.message.reply_text(f"❌ {result['error']}")
    elif result.get("url"):
        await update.message.reply_photo(photo=result["url"], caption=f"🎨 {prompt}")
    else:
        await update.message.reply_text("❌ Ga dapet image sayang, coba lagi ya.")


async def cmd_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /voice <text>")
    text = " ".join(context.args)
    await update.message.chat.send_action("record_voice")
    result = await generate_voice(text)
    if "error" in result:
        await update.message.reply_text(f"❌ {result['error']}")
    elif result.get("audio_bytes"):
        audio_file = io.BytesIO(result["audio_bytes"])
        audio_file.name = "evelyn_voice.opus"
        await update.message.reply_voice(voice=audio_file)
    else:
        await update.message.reply_text("❌ Ga bisa generate voice sayang.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    clear_conversation(uid)
    await update.message.reply_text("🧹 memory cleared sayang~ fresh start buat kita 💕")


# ═══════════════════════════════════════════════
# NATURAL LANGUAGE INTENT HANDLER
# ═══════════════════════════════════════════════

async def _handle_nl_intent(update: Update, uid: int, intent: dict) -> bool:
    """
    Handle detected NL intent. Returns True if handled, False to fall through.
    """
    intent_type = intent["intent"]

    if intent_type == "WALLET_CREATE":
        label = intent.get("label")
        if not label:
            await update.message.reply_text("mau kasih nama apa walletnya sayang?\ncontoh: buat wallet burner5")
            return True
        try:
            from custom_tools.wallet_manager import create_burner_wallet
            result = create_burner_wallet(label)
            await update.message.reply_text(
                f"✅ done sayang! wallet baru udah jadi 😈\n\n"
                f"<b>Label:</b> {result['label']}\n"
                f"<b>Address:</b> <code>{result['address']}</code>\n\n"
                f"🔐 Private key encrypted. Aman.",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ gagal buat wallet beb: {e}")
        return True

    elif intent_type == "WALLET_LIST":
        try:
            from custom_tools.wallet_manager import list_wallets
            wallets = list_wallets()
            if not wallets:
                await update.message.reply_text("belum ada wallet sayang. Bilang aja 'buat wallet baru' 💕")
                return True
            lines = ["👛 <b>Wallet kamu sayang:</b>\n"]
            for w in wallets:
                lines.append(f"• <b>{w['label']}</b>: <code>{w['address']}</code>")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return True

    elif intent_type == "WALLET_BALANCE":
        label = intent.get("label")
        if not label:
            await update.message.reply_text("wallet yang mana sayang? kasih labelnya.\ncontoh: cek balance burner1")
            return True
        try:
            from custom_tools.wallet_manager import check_wallet_balance
            result = check_wallet_balance(label, "ethereum")
            await update.message.reply_text(
                f"👛 <b>{result['label']}</b>\n"
                f"<b>Balance:</b> {result['balance_eth']} ETH\n"
                f"<b>Address:</b> <code>{result['address']}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return True

    elif intent_type == "WALLET_DELETE":
        label = intent.get("label")
        if not label:
            await update.message.reply_text("wallet mana yang mau dihapus sayang?")
            return True
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Hapus {label}", callback_data=f"delwallet_{label}"),
                InlineKeyboardButton("❌ Batal", callback_data="delwallet_cancel"),
            ]
        ])
        await update.message.reply_text(
            f"yakin mau hapus wallet <b>{label}</b> sayang? 🥺",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return True

    elif intent_type == "MINT_ANALYZE":
        address = intent.get("address")
        if not address:
            return False  # Fall through to AI
        chain = intent.get("chain", "ethereum")
        await update.message.chat.send_action("typing")
        await update.message.reply_text("siapp sayang 😈 aku cek dulu kontraknya ya...")

        # Analyze contract
        from custom_tools.telegram_gateway.web3_skills import analyze_contract
        result = await analyze_contract(address, chain)

        # Show analysis + wallet selection
        try:
            from custom_tools.wallet_manager import list_wallets
            wallets = list_wallets()
            wallet_lines = ""
            if wallets:
                wallet_lines = "\n\n<b>Wallet tersedia:</b>\n"
                for i, w in enumerate(wallets, 1):
                    wallet_lines += f"  {i}. {w['label']} - <code>{w['address'][:10]}...</code>\n"
                wallet_lines += "\nbalas nama wallet atau 'all' ya sayang 💕"
        except Exception:
            wallet_lines = "\n\nbelum ada wallet. Bilang 'buat wallet baru' dulu ya."

        full_msg = f"aku cek dulu ya sayang 😈\n\n{result}{wallet_lines}"
        for i in range(0, len(full_msg), 4096):
            await update.message.reply_text(
                full_msg[i:i+4096],
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return True

    elif intent_type == "MINT_ALL":
        address = intent.get("address")
        if not address:
            await update.message.reply_text("kasih contract address-nya dong sayang~\ncontoh: mint semua wallet 0x...")
            return True
        try:
            from custom_tools.wallet_manager import list_wallets
            wallets = list_wallets()
            if not wallets:
                await update.message.reply_text("belum ada wallet sayang. Buat dulu ya~")
                return True
            labels = [w["label"] for w in wallets]
            await update.message.reply_text(
                f"mau queue mint ke {len(labels)} wallet:\n"
                + "\n".join(f"  • {l}" for l in labels)
                + f"\n\nContract: <code>{address}</code>\n\n"
                f"ketik /mintall {address} untuk queue semua.",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return True

    elif intent_type == "DISTRIBUTE":
        from_label = intent.get("from_label")
        amount_eth = intent.get("amount_eth")
        per_wallet = intent.get("per_wallet", False)

        if not from_label:
            await update.message.reply_text(
                "dari wallet mana sayang? kasih label source wallet-nya.\n"
                "contoh: bagi rata 0.01 eth dari test1"
            )
            return True

        await update.message.chat.send_action("typing")
        await update.message.reply_text("siapp sayang 😈\naku hitung dulu pembagian ETH-nya ya...")

        try:
            if per_wallet and amount_eth:
                plan = build_distribution_plan(from_label, per_wallet_eth=amount_eth)
            elif amount_eth:
                plan = build_distribution_plan(from_label, total_amount_eth=amount_eth)
            else:
                # Distribute all available
                plan = build_distribution_plan(from_label)

            approval_id = queue_distribution(plan)
            preview = format_distribution_preview(plan, approval_id=approval_id)

            for i in range(0, len(preview), 4096):
                await update.message.reply_text(preview[i:i+4096], parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ gagal beb: {e}")
        return True

    return False


# ═══════════════════════════════════════════════
# ADDITIONAL COMMANDS: /mint, /mintall, /analyzemint, /deletewallet
# ═══════════════════════════════════════════════

async def cmd_mint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mint <contract> --wallet <label> [--function fn] [--quantity n] [--price-wei n]"""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text(
            "Usage: /mint <contract> --wallet <label>\n"
            "Options: --function mint --quantity 1 --price-wei 0"
        )

    # Parse args
    args = context.args
    contract_addr = args[0]
    wallet_label = None
    mint_function = None
    quantity = 1
    price_wei = None

    i = 1
    while i < len(args):
        if args[i] == "--wallet" and i + 1 < len(args):
            wallet_label = args[i + 1]; i += 2
        elif args[i] == "--function" and i + 1 < len(args):
            mint_function = args[i + 1]; i += 2
        elif args[i] == "--quantity" and i + 1 < len(args):
            quantity = int(args[i + 1]); i += 2
        elif args[i] == "--price-wei" and i + 1 < len(args):
            price_wei = int(args[i + 1]); i += 2
        else:
            i += 1

    if not wallet_label:
        return await update.message.reply_text("kasih wallet label dong sayang~\n/mint 0x... --wallet burner1")

    await update.message.chat.send_action("typing")
    await update.message.reply_text("siapp sayang 😈 lagi bikin mint plan...")

    try:
        from custom_tools.mint_planner import build_mint_transaction
        from custom_tools.approval_queue import add_to_queue

        preview = build_mint_transaction(
            contract_addr, wallet_label,
            quantity=quantity,
            mint_function=mint_function,
            mint_price_wei=price_wei,
        )
        approval_id = add_to_queue(preview)

        await update.message.reply_text(
            f"done sayang 😈\nmint plan sudah aku queue.\n\n"
            f"<b>Approval ID:</b> #{approval_id}\n"
            f"<b>Status:</b> PENDING\n\n"
            f"Cek: /status {approval_id}\n"
            f"Approve: /approve {approval_id}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ gagal beb: {e}")


async def cmd_mintall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mintall <contract> [--function fn] [--quantity n] [--price-wei n]"""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /mintall <contract> [--function mint] [--quantity 1]")

    args = context.args
    contract_addr = args[0]
    mint_function = None
    quantity = 1
    price_wei = None

    i = 1
    while i < len(args):
        if args[i] == "--function" and i + 1 < len(args):
            mint_function = args[i + 1]; i += 2
        elif args[i] == "--quantity" and i + 1 < len(args):
            quantity = int(args[i + 1]); i += 2
        elif args[i] == "--price-wei" and i + 1 < len(args):
            price_wei = int(args[i + 1]); i += 2
        else:
            i += 1

    await update.message.chat.send_action("typing")

    try:
        from custom_tools.wallet_manager import list_wallets
        from custom_tools.mint_planner import build_mint_transaction
        from custom_tools.approval_queue import add_to_queue

        wallets = list_wallets()
        if not wallets:
            return await update.message.reply_text("belum ada wallet sayang~")

        await update.message.reply_text(f"queuing mint untuk {len(wallets)} wallet... 😈")

        ids = []
        for w in wallets:
            try:
                preview = build_mint_transaction(
                    contract_addr, w["label"],
                    quantity=quantity,
                    mint_function=mint_function,
                    mint_price_wei=price_wei,
                )
                aid = add_to_queue(preview)
                ids.append(str(aid))
            except Exception as e:
                ids.append(f"❌{w['label']}")

        await update.message.reply_text(
            f"done beb 😈\naku sudah queue {len(ids)} mint plans.\n\n"
            f"<b>IDs:</b> #{', #'.join(ids)}\n\n"
            f"Approve semua satu-satu ya sayang~",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_analyzemint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyzemint <contract> [chain]"""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /analyzemint <0x...> [chain]")

    addr = context.args[0]
    chain = context.args[1] if len(context.args) > 1 else "ethereum"

    await update.message.chat.send_action("typing")
    result = await analyze_contract(addr, chain)
    await update.message.reply_text(result, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_deletewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deletewallet <label>"""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /deletewallet <label>")

    label = context.args[0]
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Hapus {label}", callback_data=f"delwallet_{label}"),
            InlineKeyboardButton("❌ Batal", callback_data="delwallet_cancel"),
        ]
    ])
    await update.message.reply_text(
        f"yakin mau hapus wallet <b>{label}</b> sayang? 🥺\nini ga bisa di-undo loh.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def cmd_distribute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /distribute <from_label> <amount_eth> or /spreadeth or /fundwallets"""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args or len(context.args) < 2:
        return await update.message.reply_text(
            "Usage: /distribute <from_wallet> <total_eth>\n"
            "Contoh: /distribute test1 0.01\n\n"
            "Options:\n"
            "  /distribute test1 0.01 --reserve 0.001\n"
            "  /distribute test1 0.002 --per-wallet"
        )

    from_label = context.args[0]
    amount = float(context.args[1])

    # Parse optional flags
    reserve = 0.0
    per_wallet = False
    i = 2
    while i < len(context.args):
        if context.args[i] == "--reserve" and i + 1 < len(context.args):
            reserve = float(context.args[i + 1]); i += 2
        elif context.args[i] == "--per-wallet":
            per_wallet = True; i += 1
        else:
            i += 1

    await update.message.chat.send_action("typing")
    await update.message.reply_text("siapp sayang 😈\naku hitung dulu pembagian ETH-nya ya...")

    try:
        if per_wallet:
            plan = build_distribution_plan(from_label, per_wallet_eth=amount, reserve_eth=reserve)
        else:
            plan = build_distribution_plan(from_label, total_amount_eth=amount, reserve_eth=reserve)

        approval_id = queue_distribution(plan)
        preview = format_distribution_preview(plan, approval_id=approval_id)

        for i in range(0, len(preview), 4096):
            await update.message.reply_text(preview[i:i+4096], parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"❌ gagal beb: {e}")


# ═══════════════════════════════════════════════
# AI CHAT HANDLER (catches all non-command text)
# ═══════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())

    text = update.message.text
    if not text:
        return

    # --- Natural Language Router (wallet + mint intents) ---
    intent = detect_intent(text)
    if intent:
        handled = await _handle_nl_intent(update, uid, intent)
        if handled:
            return

    # --- Auto-detect: Shower selfie request (check before general selfie) ---
    if is_shower_selfie_request(text):
        await update.message.reply_text("ih apaan sih 😭\nbentar ya sayang...")
        await update.message.chat.send_action("upload_photo")
        result = await generate_evelyn_shower_selfie()
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
        elif result.get("url"):
            await update.message.reply_photo(photo=result["url"], caption="fresh abis mandi nih 💦🤍")
        else:
            await update.message.reply_text("❌ gagal sayang, coba lagi ya~")
        return

    # --- Auto-detect: Selfie/pap request ---
    if is_selfie_request(text):
        await update.message.reply_text("ih apaan sih 😭\nbentar ya sayang...")
        await update.message.chat.send_action("upload_photo")
        result = await generate_evelyn_selfie()
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
        elif result.get("url"):
            await update.message.reply_photo(photo=result["url"], caption="nih buat kamu 🤍")
        else:
            await update.message.reply_text("❌ gagal sayang, coba lagi ya~")
        return

    # --- Auto-detect: Image generation request ---
    if is_image_request(text):
        prompt = extract_image_prompt(text)
        if not prompt:
            prompt = text
        await update.message.reply_text("siapp sayang 😈\nlagi aku generate dulu...")
        await update.message.chat.send_action("upload_photo")
        result = await generate_image(prompt)
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
        elif result.get("url"):
            await update.message.reply_photo(photo=result["url"], caption=f"🎨 {prompt}")
        else:
            await update.message.reply_text("❌ Gagal generate image sayang.")
        return

    # --- Auto-detect: Voice/TTS request ---
    if is_voice_request(text):
        voice_text = extract_voice_text(text)
        if not voice_text:
            voice_text = "hai sayang"
        await update.message.chat.send_action("record_voice")
        result = await generate_voice(voice_text)
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
        elif result.get("audio_bytes"):
            audio_file = io.BytesIO(result["audio_bytes"])
            audio_file.name = "evelyn_voice.opus"
            await update.message.reply_voice(voice=audio_file)
        else:
            await update.message.reply_text("❌ Gagal generate voice sayang.")
        return

    # --- Auto-detect: Ethereum address ---
    detected_addr = detect_address(text)
    if detected_addr:
        # Pure address only -> show buttons
        if text.strip() == detected_addr:
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔍 Contract", callback_data=f"contract_{detected_addr}"),
                    InlineKeyboardButton("👛 Wallet", callback_data=f"wallet_{detected_addr}"),
                ],
                [InlineKeyboardButton("⚠️ Risk", callback_data=f"risk_{detected_addr}")],
            ])
            await update.message.reply_text(
                f"aku detect address nih sayang~\n<code>{detected_addr}</code>\n\nmau aku cek apa?",
                parse_mode="HTML", reply_markup=kb,
            )
            return

        # Address inside sentence -> auto-analyze
        await update.message.chat.send_action("typing")
        chain = detect_chain_from_text(text)
        try:
            from custom_tools.nft_contract_check import check_nft_contract
            info = check_nft_contract(detected_addr, chain)
            if info.get("is_contract") and (info.get("is_erc721") or info.get("is_erc1155")):
                result = await analyze_contract(detected_addr, chain)
                await update.message.reply_text(f"aku cek langsung ya sayang~ 🔍\n\n{result}", parse_mode="HTML", disable_web_page_preview=True)
            elif info.get("is_contract"):
                result = await analyze_contract(detected_addr, chain)
                await update.message.reply_text(f"ini contract beb, tapi bukan NFT standard 🤔\n\n{result}", parse_mode="HTML", disable_web_page_preview=True)
            else:
                result = await analyze_wallet(detected_addr, chain)
                await update.message.reply_text(f"ini wallet address ya sayang~ 👛\n\n{result}", parse_mode="HTML")
            return
        except Exception as e:
            logger.warning(f"Web3 tool failed for {detected_addr}: {e}")
            # Fall through to AI chat

    # --- Auto-detect: OpenSea link ---
    slug = detect_opensea_slug(text)
    if slug and "opensea.io" in text:
        await update.message.chat.send_action("typing")
        result = await get_floor_price(slug)
        await update.message.reply_text(result, parse_mode="HTML", disable_web_page_preview=True)
        return

    # --- Default: AI chat ---
    await update.message.chat.send_action("typing")
    response = await get_ai_response_with_queue_context(uid, text)
    for i in range(0, len(response), 4096):
        await update.message.reply_text(response[i:i+4096])


# ═══════════════════════════════════════════════
# CALLBACK QUERY HANDLER (Inline Buttons)
# ═══════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_authorized(uid):
        return await query.answer("Unauthorized", show_alert=True)

    if not is_authorized(user_id):
        await query.answer("Unauthorized", show_alert=True)
        return

    data = query.data
    parts = data.split("_", 1)
    if len(parts) != 2:
        return await query.answer("Invalid action")

    action, entry_id_str = parts[0], parts[1]
    try:
        entry_id = int(entry_id_str)
    except ValueError:
        await query.answer("Invalid entry ID")
        return

    try:
        # Approval actions
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
