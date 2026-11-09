"""
Comments feature module for Bale Helm Telegram Bot.
Handles user comment collection and two-way admin replies.
"""

from datetime import datetime, timezone
import html
import re
from typing import Optional

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
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_ID, CHANNEL_URL, logger

# Conversation state
WAITING_FOR_COMMENT = 1


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

    parent = getattr(message, "reply_to_message", None)
    if parent:
        candidates.extend([
            getattr(parent, "text", None),
            getattr(parent, "caption", None),
        ])

    for text in candidates:
        if not text:
            continue
        match = re.search(r"(?:ID|🆔)[:\s]*(?:<code>)?(\d+)", text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


async def start_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Triggered when a user taps '💬 Send a comment'.
    Prompts the user to write their message.
    """
    query = update.callback_query
    if query:
        await query.answer()

    # Admin restriction: cannot send comments to themselves
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        msg = (
            "⚠️ <b>Administrator Notice:</b> You cannot send comments to yourself.\n\n"
            "Use the admin commands to manage campaigns and review submissions."
        )
        if query:
            await query.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    prompt_text = (
        "✍️ <b>Please write your message or comment below.</b>\n\n"
        "You can send text, photos, voice notes, videos, documents, or any other media.\n"
        "Feel free to share suggestions to add or remove content, feedback, or any thoughts.\n\n"
        "<i>Send /cancel at any time if you change your mind.</i>"
    )

    if query:
        await query.message.reply_text(prompt_text, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(prompt_text, parse_mode=ParseMode.HTML)

    return WAITING_FOR_COMMENT


async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives member's comment and forwards it to ADMIN_ID as:
      1. An HTML header message containing full name, username/no username, ID, and reply note.
      2. The user's original message copied right underneath as a reply to the header.
    """
    user = update.effective_user
    chat = update.effective_chat
    incoming_msg = update.effective_message

    if not user or not incoming_msg:
        return WAITING_FOR_COMMENT

    # Admin cannot comment to self
    if user.id == ADMIN_ID:
        await incoming_msg.reply_text("⚠️ You cannot send comments to yourself.")
        return ConversationHandler.END

    if not ADMIN_ID:
        logger.error("ADMIN_ID is not configured in .env! Cannot forward comment.")
        await incoming_msg.reply_text(
            "⚠️ The bot is not fully configured to receive comments yet. Please try again later."
        )
        return ConversationHandler.END

    full_name = html.escape(user.full_name or "Unknown")
    username_str = f"@{html.escape(user.username)}" if user.username else "no username"
    user_id = user.id
    time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    header_text = (
        "╭─ 💬 <b>NEW COMMENT</b> ───────────────\n"
        f"│ 👤 <b>From:</b> {full_name}\n"
        f"│ 🏷 <b>Username:</b> {username_str}\n"
        f"│ 🆔 <b>ID:</b> <code>{user_id}</code>  ·  🕒 <code>{time_str}</code>\n"
        "╰────────────────────────────────\n"
        "<i>💡 Reply to this header or the copied message below to answer the user.</i>"
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

        # Step 3: Confirm to user
        await incoming_msg.reply_text(
            "✅ <b>Thank you!</b> Your comment has been delivered to the channel admin.\n\n"
            "You can use /start anytime to send another.",
            parse_mode=ParseMode.HTML,
        )
        logger.info("Forwarded comment from user ID %s to admin %s", user_id, ADMIN_ID)

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

    return ConversationHandler.END


async def cancel_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels comment submission."""
    if update.message:
        await update.message.reply_text(
            "❌ Comment submission canceled.\n\n"
            "Send /start whenever you would like to begin again."
        )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Admin Reply Handler
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

        await admin_msg.reply_text("✅ Sent.")
        logger.info("Admin replied successfully to user ID %s", target_user_id)

    except Forbidden:
        logger.warning("Delivery failed: user %s has blocked the bot.", target_user_id)
        await admin_msg.reply_text("❌ Delivery failed: The user has blocked the bot.")
    except TelegramError as e:
        logger.error("Delivery failed to user %s: %s", target_user_id, e)
        await admin_msg.reply_text(f"❌ Delivery failed: {html.escape(str(e))}")


def get_comments_conversation_handler() -> ConversationHandler:
    """Creates and returns the ConversationHandler for the comment flow."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_comment_callback, pattern="^send_comment$"),
        ],
        states={
            WAITING_FOR_COMMENT: [
                MessageHandler(~filters.COMMAND, handle_comment),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_comment),
        ],
        allow_reentry=True,
    )
