#!/usr/bin/env python3
"""
Bale Helm Telegram Bot
A Telegram bot for the Bale Helm channel (https://t.me/bale_helm).

Features:
- Differentiated experiences for regular members and channel administrator.
- Member flow: Submit comments (with optional anonymity: With Name or Anonymous).
- Admin restriction: Administrator cannot send comments to themselves.
- Admin dashboard: Statistics, Announcement broadcasting, direct /reply command, and reply guide.
- Two-way messaging: Admin replies to forwarded comments or uses /reply <user_id> <msg>.
"""

from datetime import datetime, timezone
import html
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration & Environment
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()

try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else 0
except ValueError:
    ADMIN_ID = 0

CHANNEL_URL = "https://t.me/bale_helm"

# Configure logging (Never expose BOT_TOKEN in logs)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bale_helm_bot")

# Conversation states
CHOOSING_ANONYMITY = 1
WAITING_FOR_COMMENT = 2
WAITING_FOR_BROADCAST = 3

# Data persistence file
DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "bot_data.json"


# ---------------------------------------------------------------------------
# Lightweight Persistence Helpers
# ---------------------------------------------------------------------------
def load_data() -> dict:
    """Loads stored bot statistics and user list."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to read %s: %s", DATA_FILE, e)
    return {
        "users": [],
        "total_comments": 0,
        "named_comments": 0,
        "anonymous_comments": 0,
        "last_comment_at": None,
    }


def save_data(data: dict) -> None:
    """Saves bot statistics and user list atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(DATA_FILE)
    except Exception as e:
        logger.error("Failed to write %s: %s", DATA_FILE, e)


def register_user(user_id: int) -> None:
    """Registers a member's user ID so they can receive admin broadcasts."""
    if user_id <= 0 or user_id == ADMIN_ID:
        return
    data = load_data()
    users = set(data.get("users", []))
    if user_id not in users:
        users.add(user_id)
        data["users"] = sorted(list(users))
        save_data(data)


def record_comment(is_anonymous: bool) -> None:
    """Updates submission metrics."""
    data = load_data()
    data["total_comments"] = data.get("total_comments", 0) + 1
    if is_anonymous:
        data["anonymous_comments"] = data.get("anonymous_comments", 0) + 1
    else:
        data["named_comments"] = data.get("named_comments", 0) + 1
    data["last_comment_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_data(data)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def extract_user_id_from_message(message: Optional[Message]) -> Optional[int]:
    """
    Extracts the numeric Telegram user ID from a message or its parent reply.
    Looks for the pattern 'ID: <digits>'.
    """
    if not message:
        return None

    candidates = [
        getattr(message, "text", None),
        getattr(message, "caption", None),
    ]

    # If this message was itself a reply (e.g. copied message replied to header)
    parent = getattr(message, "reply_to_message", None)
    if parent:
        candidates.extend([
            getattr(parent, "text", None),
            getattr(parent, "caption", None),
        ])

    for text in candidates:
        if not text:
            continue
        # Matches 'ID: 12345', '🆔 ID: 12345', or '🆔 12345'
        match = re.search(r"(?:ID|🆔)[:\s]*(?:<code>)?(\d+)", text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def get_member_start_keyboard() -> InlineKeyboardMarkup:
    """Returns the main inline keyboard shown to members on /start."""
    keyboard = [
        [InlineKeyboardButton("💬 Send a comment", callback_data="send_comment")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_anonymity_keyboard() -> InlineKeyboardMarkup:
    """Returns the inline keyboard for choosing anonymity."""
    keyboard = [
        [
            InlineKeyboardButton("👤 With Name", callback_data="mode_named"),
            InlineKeyboardButton("🕵️ Anonymous", callback_data="mode_anon"),
        ],
        [InlineKeyboardButton("« Cancel", callback_data="cancel_comment")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Returns the admin control panel keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("📖 Reply Guide", callback_data="admin_guide"),
            InlineKeyboardButton("🔗 Open Channel", url=CHANNEL_URL),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="admin_refresh"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_dashboard_text(user_first_name: str) -> str:
    """Generates the welcome text for the admin dashboard."""
    return (
        f"👑 <b>Admin Dashboard — Bale Helm</b>\n\n"
        f"Welcome back, <b>{html.escape(user_first_name)}</b>!\n"
        f"Channel: <a href=\"{CHANNEL_URL}\">t.me/bale_helm</a>\n"
        f"Status: 🟢 <b>Active & Listening for Comments</b>\n\n"
        f"Use the buttons below to check stats, broadcast announcements, or review instructions."
    )


# ---------------------------------------------------------------------------
# Handlers: Start & Cancel
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles /start.
    - If caller is ADMIN: presents the Admin Dashboard.
    - If caller is a regular member: registers user and presents the member comment prompt.
    """
    context.user_data.pop("is_anonymous", None)
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    # ADMIN USER VIEW
    if user.id == ADMIN_ID:
        dashboard_text = build_admin_dashboard_text(user.first_name)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=dashboard_text,
                reply_markup=get_admin_dashboard_keyboard(),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        elif update.message:
            await update.message.reply_text(
                text=dashboard_text,
                reply_markup=get_admin_dashboard_keyboard(),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        return ConversationHandler.END

    # REGULAR MEMBER VIEW
    register_user(user.id)
    user_name = user.first_name or "there"
    welcome_text = (
        f"👋 Hello, <b>{html.escape(user_name)}</b>!\n\n"
        f"Welcome to the official bot for <b>Bale Helm | ባለ ሕልም</b> "
        f"(<a href=\"{CHANNEL_URL}\">t.me/bale_helm</a>).\n\n"
        f"Have feedback, ideas, suggestions for channel content, or thoughts to share? "
        f"Tap the button below to send a message directly to the channel admin."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=welcome_text,
            reply_markup=get_member_start_keyboard(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    elif update.message:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=get_member_start_keyboard(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the active operation (comment or broadcast)."""
    context.user_data.pop("is_anonymous", None)
    if update.message:
        await update.message.reply_text(
            "❌ Action canceled.\n\n"
            "Send /start whenever you would like to begin again."
        )
    return ConversationHandler.END


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles inline 'Cancel' button taps."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("is_anonymous", None)

    await query.edit_message_text(
        "❌ Action canceled.\n\n"
        "Send /start whenever you would like to begin again."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Member Handlers: Comment Submission Flow
# ---------------------------------------------------------------------------
async def start_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Triggered when a member taps '💬 Send a comment'.
    Prevents admin from submitting comments to themselves.
    """
    query = update.callback_query
    await query.answer()

    # Restriction: Admin cannot send comments to themselves
    if update.effective_user.id == ADMIN_ID:
        await query.message.reply_text(
            "⚠️ <b>Administrator Notice:</b> You cannot send comments to yourself.\n\n"
            "Use the Admin Dashboard (/start) to check stats or broadcast messages.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    prompt_text = (
        "🔒 <b>Choose comment mode:</b>\n\n"
        "• <b>👤 With Name:</b> Your name and @username will be visible to the admin.\n"
        "• <b>🕵️ Anonymous:</b> Your identity will be hidden from the admin.\n\n"
        "How would you like to send your comment?"
    )

    await query.message.reply_text(
        text=prompt_text,
        reply_markup=get_anonymity_keyboard(),
        parse_mode=ParseMode.HTML,
    )

    return CHOOSING_ANONYMITY


async def set_comment_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Records anonymity choice and prompts for the message."""
    query = update.callback_query
    await query.answer()

    is_anon = query.data == "mode_anon"
    context.user_data["is_anonymous"] = is_anon

    mode_label = "🕵️ <b>Anonymous</b>" if is_anon else "👤 <b>With Name</b>"

    prompt_text = (
        f"Selected mode: {mode_label}\n\n"
        "✍️ <b>Please write your message or comment below.</b>\n\n"
        "You can send text, photos, voice notes, videos, documents, or any other media.\n"
        "Feel free to share suggestions to add or remove content, feedback, or any thoughts.\n\n"
        "<i>Send /cancel at any time if you change your mind.</i>"
    )

    await query.edit_message_text(
        text=prompt_text,
        parse_mode=ParseMode.HTML,
    )

    return WAITING_FOR_COMMENT


async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives member's comment and forwards it to ADMIN_ID with header + copy.
    """
    user = update.effective_user
    chat = update.effective_chat
    incoming_msg = update.effective_message

    if not user or not incoming_msg:
        return WAITING_FOR_COMMENT

    # Extra safety check against admin self-comments
    if user.id == ADMIN_ID:
        await incoming_msg.reply_text(
            "⚠️ You are the administrator of this bot. Comments cannot be sent to yourself."
        )
        return ConversationHandler.END

    if not ADMIN_ID:
        logger.error("ADMIN_ID is not configured in .env! Cannot forward comment.")
        await incoming_msg.reply_text(
            "⚠️ The bot is not fully configured to receive comments yet. Please try again later."
        )
        return ConversationHandler.END

    is_anonymous = context.user_data.get("is_anonymous", False)
    user_id = user.id
    time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if is_anonymous:
        header_text = (
            "╭─ 🕵️ <b>ANONYMOUS COMMENT</b>\n"
            "│ 👤 <i>Hidden identity</i>\n"
            f"│ 🆔 <b>ID:</b> <code>{user_id}</code>  ·  🕒 <code>{time_str}</code>\n"
            "╰────────────────────────\n"
            "<i>↩ Swipe to reply</i>"
        )
    else:
        full_name = html.escape(user.full_name or "Unknown")
        user_handle = f" (@{html.escape(user.username)})" if user.username else ""
        header_text = (
            "╭─ 💬 <b>NEW COMMENT</b>\n"
            f"│ 👤 <b>{full_name}</b>{user_handle}\n"
            f"│ 🆔 <b>ID:</b> <code>{user_id}</code>  ·  🕒 <code>{time_str}</code>\n"
            "╰────────────────────────\n"
            "<i>↩ Swipe to reply</i>"
        )

    try:
        # Step 1: Send header message to admin
        header_message = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=header_text,
            parse_mode=ParseMode.HTML,
        )

        # Step 2: Copy user message right underneath as reply to header
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=chat.id,
            message_id=incoming_msg.message_id,
            reply_parameters=ReplyParameters(message_id=header_message.message_id),
        )

        # Step 3: Record stats and confirm to user
        record_comment(is_anonymous=is_anonymous)
        register_user(user_id)

        confirmation_text = (
            "✅ <b>Thank you!</b> Your anonymous comment has been delivered to the channel admin.\n\n"
            "You can send /start anytime to submit another message."
            if is_anonymous
            else "✅ <b>Thank you!</b> Your comment has been delivered to the channel admin.\n\n"
            "You can send /start anytime to submit another message."
        )

        await incoming_msg.reply_text(
            confirmation_text,
            parse_mode=ParseMode.HTML,
        )
        logger.info(
            "Forwarded %s comment from user ID %s to admin %s",
            "anonymous" if is_anonymous else "named",
            user_id,
            ADMIN_ID,
        )

    except Forbidden:
        logger.error(
            "Cannot message ADMIN_ID %s: Bot was blocked or admin has never pressed Start on the bot.",
            ADMIN_ID,
        )
        await incoming_msg.reply_text(
            "⚠️ Delivery failed: The administrator has not activated this bot yet.\n"
            "Please try again later."
        )
    except TelegramError as e:
        logger.error("Error forwarding comment to admin: %s", e)
        await incoming_msg.reply_text(
            "⚠️ An error occurred while sending your comment. Please try again in a few moments."
        )
    finally:
        context.user_data.pop("is_anonymous", None)

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Admin Features: Dashboard, Statistics, Broadcast & Direct Reply
# ---------------------------------------------------------------------------
async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Routes callback queries initiated from the Admin Dashboard."""
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.message.reply_text("⛔ Restricted to channel administrator.")
        return ConversationHandler.END

    action = query.data

    if action in ("admin_dashboard", "admin_refresh"):
        await query.edit_message_text(
            text=build_admin_dashboard_text(update.effective_user.first_name),
            reply_markup=get_admin_dashboard_keyboard(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    elif action == "admin_stats":
        data = load_data()
        total_users = len(data.get("users", []))
        total_comments = data.get("total_comments", 0)
        named_comments = data.get("named_comments", 0)
        anon_comments = data.get("anonymous_comments", 0)
        last_comment = data.get("last_comment_at") or "None yet"

        stats_text = (
            "📊 <b>Bale Helm Bot — Statistics</b>\n\n"
            f"👥 <b>Active Members Tracked:</b> <code>{total_users}</code>\n"
            f"📬 <b>Total Comments Received:</b> <code>{total_comments}</code>\n"
            f"  • 👤 <b>With Name:</b> <code>{named_comments}</code>\n"
            f"  • 🕵️ <b>Anonymous:</b> <code>{anon_comments}</code>\n"
            f"🕒 <b>Last Submission:</b> <code>{last_comment}</code>\n"
        )
        keyboard = [[InlineKeyboardButton("« Back to Dashboard", callback_data="admin_dashboard")]]
        await query.edit_message_text(
            text=stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    elif action == "admin_guide":
        guide_text = (
            "📖 <b>Admin Instructions & Features</b>\n\n"
            "<b>1. Replying to Comments:</b>\n"
            "• Swipe or reply directly to any comment header or message in this chat.\n"
            "• Your response is sent to the user prefixed with <code>💌 Reply from the channel admin:</code>.\n"
            "• Works for both named and anonymous comments.\n\n"
            "<b>2. Direct Message Command:</b>\n"
            "• Send <code>/reply &lt;user_id&gt; &lt;message&gt;</code> to message a member directly.\n\n"
            "<b>3. Broadcast Announcements:</b>\n"
            "• Tap <b>Broadcast</b> in the dashboard or send <code>/broadcast</code> to message all members.\n\n"
            "<b>4. Quick Stats:</b>\n"
            "• Use <code>/stats</code> anytime to view real-time comment metrics."
        )
        keyboard = [[InlineKeyboardButton("« Back to Dashboard", callback_data="admin_dashboard")]]
        await query.edit_message_text(
            text=guide_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    elif action == "admin_broadcast":
        data = load_data()
        member_count = len(data.get("users", []))
        prompt_text = (
            "📢 <b>Broadcast Announcement</b>\n\n"
            f"Currently <b>{member_count}</b> member(s) are registered to receive broadcasts.\n\n"
            "<b>Send the message or media you want to broadcast now.</b>\n"
            "It will be forwarded to all active bot users.\n\n"
            "<i>Send /cancel at any time to abort.</i>"
        )
        await query.message.reply_text(
            text=prompt_text,
            parse_mode=ParseMode.HTML,
        )
        return WAITING_FOR_BROADCAST

    return ConversationHandler.END


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Command shortcut (/broadcast) for channel admin."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Restricted to channel administrator.")
        return ConversationHandler.END

    data = load_data()
    member_count = len(data.get("users", []))
    prompt_text = (
        "📢 <b>Broadcast Announcement</b>\n\n"
        f"Currently <b>{member_count}</b> member(s) are registered to receive broadcasts.\n\n"
        "<b>Send the message or media you want to broadcast now.</b>\n\n"
        "<i>Send /cancel at any time to abort.</i>"
    )
    await update.message.reply_text(text=prompt_text, parse_mode=ParseMode.HTML)
    return WAITING_FOR_BROADCAST


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Broadcasts admin's message to all registered users."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    admin_msg = update.effective_message
    data = load_data()
    recipients = data.get("users", [])

    if not recipients:
        await admin_msg.reply_text("ℹ️ No members have interacted with the bot yet to broadcast to.")
        return ConversationHandler.END

    status_msg = await admin_msg.reply_text(
        f"⏳ Broadcasting message to {len(recipients)} member(s)..."
    )

    success_count = 0
    fail_count = 0

    for user_id in recipients:
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=update.effective_chat.id,
                message_id=admin_msg.message_id,
            )
            success_count += 1
        except Exception:
            fail_count += 1

    await status_msg.edit_text(
        f"📢 <b>Broadcast Completed!</b>\n\n"
        f"✅ <b>Delivered:</b> <code>{success_count}</code>\n"
        f"❌ <b>Blocked / Failed:</b> <code>{fail_count}</code>\n"
        f"👥 <b>Total Attempted:</b> <code>{len(recipients)}</code>",
        parse_mode=ParseMode.HTML,
    )

    return ConversationHandler.END


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command shortcut (/stats) for channel admin."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Restricted to channel administrator.")
        return

    data = load_data()
    total_users = len(data.get("users", []))
    total_comments = data.get("total_comments", 0)
    named_comments = data.get("named_comments", 0)
    anon_comments = data.get("anonymous_comments", 0)
    last_comment = data.get("last_comment_at") or "None yet"

    stats_text = (
        "📊 <b>Bale Helm Bot — Statistics</b>\n\n"
        f"👥 <b>Active Members Tracked:</b> <code>{total_users}</code>\n"
        f"📬 <b>Total Comments Received:</b> <code>{total_comments}</code>\n"
        f"  • 👤 <b>With Name:</b> <code>{named_comments}</code>\n"
        f"  • 🕵️ <b>Anonymous:</b> <code>{anon_comments}</code>\n"
        f"🕒 <b>Last Submission:</b> <code>{last_comment}</code>\n"
    )
    await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Direct message command (/reply <user_id> <message>) for channel admin.
    Allows admin to message any user directly by their numeric Telegram ID.
    """
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Restricted to channel administrator.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "ℹ️ <b>Usage:</b> <code>/reply &lt;user_id&gt; &lt;your message&gt;</code>\n\n"
            "Example: <code>/reply 12345678 Thank you for your feedback!</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. It must be numeric.")
        return

    reply_body = " ".join(context.args[1:])
    prefix = "💌 Reply from the channel admin:\n\n"
    full_text = f"{prefix}{reply_body}"

    try:
        await context.bot.send_message(chat_id=target_id, text=full_text)
        await update.message.reply_text(f"✅ Delivered to user <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
    except Forbidden:
        await update.message.reply_text(
            f"❌ Delivery failed: User <code>{target_id}</code> has blocked the bot or not started it.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Delivery failed: {html.escape(str(e))}")


# ---------------------------------------------------------------------------
# Admin Reply Handler (Reply-to-Message in Chat)
# ---------------------------------------------------------------------------
async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    When ADMIN_ID replies to either the header message or the copied user message,
    parses the user ID and delivers the admin's reply to that user prefixed with
    '💌 Reply from the channel admin:'.
    """
    admin_msg = update.effective_message
    if not admin_msg or not admin_msg.reply_to_message:
        return

    # Extract target user ID from the header or the parent of the copied message
    target_user_id = extract_user_id_from_message(admin_msg.reply_to_message)
    if not target_user_id:
        return

    prefix = "💌 Reply from the channel admin:"

    try:
        if admin_msg.text:
            full_reply_text = f"{prefix}\n\n{admin_msg.text}"
            await context.bot.send_message(
                chat_id=target_user_id,
                text=full_reply_text,
            )
        else:
            if admin_msg.caption:
                new_caption = f"{prefix}\n\n{admin_msg.caption}"
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=update.effective_chat.id,
                    message_id=admin_msg.message_id,
                    caption=new_caption,
                )
            else:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=prefix,
                )
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=update.effective_chat.id,
                    message_id=admin_msg.message_id,
                )

        await admin_msg.reply_text("✓ <b>Sent.</b>", parse_mode=ParseMode.HTML)
        logger.info("Admin replied successfully to user ID %s", target_user_id)

    except Forbidden:
        logger.warning("Delivery failed: user %s has blocked the bot.", target_user_id)
        await admin_msg.reply_text("❌ Delivery failed: The user has blocked the bot.")
    except TelegramError as e:
        logger.error("Delivery failed to user %s: %s", target_user_id, e)
        await admin_msg.reply_text(f"❌ Delivery failed: {html.escape(str(e))}")


# ---------------------------------------------------------------------------
# Application Initialization & Entry Point
# ---------------------------------------------------------------------------
def main() -> None:
    """Initializes and runs the bot using long polling."""
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        logger.error(
            "BOT_TOKEN is not configured! Please open your .env file and set BOT_TOKEN to your actual token."
        )
        sys.exit(1)

    if not ADMIN_ID:
        logger.warning("ADMIN_ID is not configured in .env. Incoming comments cannot be delivered.")

    application = Application.builder().token(BOT_TOKEN).build()

    # 1. Admin reply handler (MUST be registered first to take priority over conversation handler)
    admin_reply_filter = (
        filters.User(user_id=ADMIN_ID)
        & filters.REPLY
        & ~filters.COMMAND
    )
    application.add_handler(
        MessageHandler(admin_reply_filter, handle_admin_reply)
    )

    # 2. Admin direct commands
    application.add_handler(CommandHandler("reply", reply_command))
    application.add_handler(CommandHandler("stats", stats_command))

    # 3. Main Conversation Handler (Comment submission & Broadcast)
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CommandHandler("broadcast", broadcast_command),
            CallbackQueryHandler(start_comment_callback, pattern="^send_comment$"),
            CallbackQueryHandler(admin_callback_router, pattern="^admin_"),
        ],
        states={
            CHOOSING_ANONYMITY: [
                CallbackQueryHandler(set_comment_mode, pattern="^mode_(named|anon)$"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel_comment$"),
            ],
            WAITING_FOR_COMMENT: [
                CallbackQueryHandler(start_comment_callback, pattern="^send_comment$"),
                CallbackQueryHandler(set_comment_mode, pattern="^mode_(named|anon)$"),
                MessageHandler(~filters.COMMAND, handle_comment),
            ],
            WAITING_FOR_BROADCAST: [
                MessageHandler(
                    ~filters.COMMAND & filters.User(user_id=ADMIN_ID),
                    handle_broadcast_message,
                ),
            ],
        },
        fallbacks=[
            CommandHandler("start", start_command),
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(cancel_callback, pattern="^cancel_comment$"),
        ],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)

    # 4. Global cancel command fallback
    application.add_handler(CommandHandler("cancel", cancel_command))

    logger.info("Starting Bale Helm bot with long polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
