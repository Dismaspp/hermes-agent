"""
telegram_gateway/bot.py - Evelyn AI Web3 Companion Bot (Ultimate)
==================================================================
Commands:
  /start              - Welcome (Evelyn intro)
  /pending            - List pending approvals
  /approve <id>       - Approve entry
  /reject <id>        - Reject entry
  /status <id>        - Check entry status
  /contract <addr>    - NFT contract analysis
  /wallet <addr>      - Wallet summary
  /wallets            - List burner wallets
  /createwallet <lbl> - Create burner wallet
  /floor <slug>       - OpenSea floor price
  /risk <addr>        - AI risk analysis
  /generate <prompt>  - Generate image (FAL.ai/FLUX)
  /voice <text>       - Generate voice note (OpenAI TTS)
  /clear              - Clear AI chat history

AI Chat:
  Any text -> Evelyn responds with personality
  Auto-detects: 0x addresses, OpenSea links, image/voice requests

SAFETY:
- Only TELEGRAM_ALLOWED_USERS can interact
- Private keys NEVER shown
- Bot does NOT auto-execute transactions
- AI chat CANNOT trigger blockchain transactions
"""

import os
import sys
import io
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
from custom_tools.telegram_gateway.ai_chat import (
    get_ai_response_with_queue_context,
    clear_conversation,
)
from custom_tools.telegram_gateway.web3_skills import (
    analyze_contract,
    analyze_wallet,
    get_floor_price,
    analyze_risk,
    detect_address,
    detect_opensea_slug,
    detect_chain_from_text,
)
from custom_tools.telegram_gateway.image_gen import (
    generate_image,
    generate_evelyn_selfie,
    generate_evelyn_shower_selfie,
    is_image_request,
    is_selfie_request,
    is_shower_selfie_request,
    extract_image_prompt,
)
from custom_tools.telegram_gateway.voice_tts import (
    generate_voice,
    is_voice_request,
    extract_voice_text,
)
from custom_tools.telegram_gateway.nl_router import detect_intent

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


def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_USERS


def unauthorized_msg() -> str:
    return "🚫 Maaf sayang, kamu belum terdaftar di TELEGRAM_ALLOWED_USERS."


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
        [InlineKeyboardButton("👁 Preview", callback_data=f"preview_{entry_id}")],
    ])


# ═══════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())

    pc = count_pending()
    pending_txt = f"\n\n⏳ Ada {pc} pending approval nih." if pc > 0 else ""

    await update.message.reply_text(
        f"hai sayang 💕\n\n"
        f"aku <b>Evelyn</b>, AI companion kamu buat Web3/NFT.\n\n"
        f"<b>Commands:</b>\n"
        f"  /pending - Antrian approval\n"
        f"  /approve &lt;id&gt; / /reject &lt;id&gt;\n"
        f"  /contract &lt;addr&gt; - Scan contract\n"
        f"  /wallet &lt;addr&gt; - Cek wallet\n"
        f"  /wallets - List burner wallets\n"
        f"  /walletbalance &lt;label&gt;\n"
        f"  /createwallet &lt;label&gt;\n"
        f"  /floor &lt;slug&gt; - Floor price\n"
        f"  /risk &lt;addr&gt; - Risk analysis\n"
        f"  /generate &lt;prompt&gt; - Generate image\n"
        f"  /voice &lt;text&gt; - Voice note\n"
        f"  /clear - Reset memory\n\n"
        f"atau ketik apa aja, aku bales kok 😊\n"
        f"mau selfie? ketik 'pap' atau 'selfie dong' 📸{pending_txt}",
        parse_mode="HTML",
    )


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())

    entries = list_queue(status="pending", limit=10)
    if not entries:
        return await update.message.reply_text("✅ Queue kosong sayang, santai dulu~")

    await update.message.reply_text(f"⏳ ada {len(entries)} pending nih beb 😈", parse_mode="HTML")
    for entry in entries:
        await update.message.reply_text(format_entry(entry), parse_mode="HTML", reply_markup=approval_kb(entry["id"]))


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("kasih ID-nya dong sayang~\nUsage: /approve <id>")
    try:
        eid = int(context.args[0])
        approve(eid, approved_by=f"telegram:{uid}")
        await update.message.reply_text(f"siapp cintaaa 😈\nentry #{eid} udah aku approve ya ✅")
    except Exception as e:
        await update.message.reply_text(f"❌ gagal beb: {e}")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /reject <id> [reason]")
    try:
        eid = int(context.args[0])
        reason = " ".join(context.args[1:]) or f"Rejected by telegram:{uid}"
        reject(eid, reason=reason)
        await update.message.reply_text(f"oke sayang, entry #{eid} aku reject ❌\nreason: {reason}")
    except Exception as e:
        await update.message.reply_text(f"❌ error: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /status <id>")
    try:
        eid = int(context.args[0])
        entry = get_entry(eid)
        await update.message.reply_text(format_entry(entry), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("kasih address-nya dong~\n/contract <0x...> [chain]")
    addr = context.args[0]
    chain = context.args[1] if len(context.args) > 1 else "ethereum"
    await update.message.chat.send_action("typing")
    result = await analyze_contract(addr, chain)
    await update.message.reply_text(result, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    if not context.args:
        return await update.message.reply_text("Usage: /wallet <0x...> [chain]")
    addr = context.args[0]
    chain = context.args[1] if len(context.args) > 1 else "ethereum"
    await update.message.chat.send_action("typing")
    result = await analyze_wallet(addr, chain)
    await update.message.reply_text(result, parse_mode="HTML")


async def cmd_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        return await update.message.reply_text(unauthorized_msg())
    try:
        from custom_tools.wallet_manager import list_wallets
        wallets = list_wallets()
        if not wallets:
            return await update.message.reply_text("belum ada wallet sayang. Bikin dulu pake /createwallet <label>")
        lines = ["👛 <b>Burner Wallets:</b>\n"]
        for w in wallets:
            lines.append(f"• <b>{w['label']}</b>: <code>{w['address']}</code>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


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

    data = query.data
    parts = data.split("_", 1)
    if len(parts) != 2:
        return await query.answer("Invalid action")

    action, param = parts

    try:
        # Approval actions
        if action == "approve":
            approve(int(param), approved_by=f"telegram:{uid}")
            await query.answer(f"✅ #{param} approved!")
            await query.edit_message_text(f"✅ <b>APPROVED</b> - Entry #{param}\napproved by sayang 😈", parse_mode="HTML")

        elif action == "reject":
            reject(int(param), reason=f"Rejected via button by user {uid}")
            await query.answer(f"❌ #{param} rejected")
            await query.edit_message_text(f"❌ <b>REJECTED</b> - Entry #{param}", parse_mode="HTML")

        elif action == "preview":
            entry = get_entry(int(param))
            text = format_entry(entry) + "\n\n🔍 <b>Preview only. No tx sent.</b>"
            await query.answer("Preview loaded")
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=approval_kb(int(param)))

        elif action == "contract":
            await query.answer("Analyzing...")
            result = await analyze_contract(param, "ethereum")
            await query.edit_message_text(result, parse_mode="HTML", disable_web_page_preview=True)

        elif action == "wallet":
            await query.answer("Checking...")
            result = await analyze_wallet(param, "ethereum")
            await query.edit_message_text(result, parse_mode="HTML")

        elif action == "risk":
            await query.answer("Analyzing risk...")
            result = await analyze_risk(param, uid, "ethereum")
            await query.edit_message_text(result[:4096], parse_mode="HTML", disable_web_page_preview=True)

        elif action == "delwallet":
            if param == "cancel":
                await query.answer("Dibatalin~")
                await query.edit_message_text("oke sayang, ga jadi dihapus 💕")
            else:
                try:
                    from custom_tools.wallet_manager import WALLETS_DIR
                    wallet_file = WALLETS_DIR / f"{param}.json"
                    if wallet_file.exists():
                        wallet_file.unlink()
                        await query.answer(f"Wallet {param} deleted")
                        await query.edit_message_text(f"✅ Wallet <b>{param}</b> udah dihapus sayang.", parse_mode="HTML")
                    else:
                        await query.answer("Wallet not found")
                        await query.edit_message_text(f"❌ Wallet {param} ga ketemu beb.")
                except Exception as e:
                    await query.answer(f"Error: {e}", show_alert=True)

        else:
            await query.answer("Unknown")

    except Exception as e:
        await query.answer(f"Error: {str(e)[:100]}", show_alert=True)


# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set"); sys.exit(1)
    if not ALLOWED_USERS:
        print("ERROR: TELEGRAM_ALLOWED_USERS not set"); sys.exit(1)

    personality = os.getenv("AI_PERSONALITY_MODE", "deep_waifu")
    ai_key = os.getenv("OPENROUTER_API_KEY", "")
    fal_key = os.getenv("FAL_KEY", "")
    tts_key = os.getenv("OPENAI_API_KEY", "")

    print(f"╔══════════════════════════════════════════╗")
    print(f"║    Evelyn - AI Web3 Companion (Ultimate) ║")
    print(f"╠══════════════════════════════════════════╣")
    print(f"║  Personality: {personality:<26}║")
    print(f"║  AI Chat:     {'ON' if ai_key else 'OFF':<26}║")
    print(f"║  Image Gen:   {'ON' if fal_key else 'OFF':<26}║")
    print(f"║  Voice TTS:   {'ON' if tts_key else 'OFF':<26}║")
    print(f"║  DRY_RUN:     {str(DRY_RUN):<26}║")
    print(f"║  Users:       {str(ALLOWED_USERS):<26}║")
    print(f"╚══════════════════════════════════════════╝\n")

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("contract", cmd_contract))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("wallets", cmd_wallets))
    app.add_handler(CommandHandler("createwallet", cmd_createwallet))
    app.add_handler(CommandHandler("walletbalance", cmd_walletbalance))
    app.add_handler(CommandHandler("deletewallet", cmd_deletewallet))
    app.add_handler(CommandHandler("mint", cmd_mint))
    app.add_handler(CommandHandler("mintall", cmd_mintall))
    app.add_handler(CommandHandler("analyzemint", cmd_analyzemint))
    app.add_handler(CommandHandler("floor", cmd_floor))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("clear", cmd_clear))

    # Buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    # AI chat (all non-command text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Evelyn is online 💕 Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
