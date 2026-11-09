#!/usr/bin/env python3
"""
Bale Helm Telegram Bot
A Telegram bot for the Bale Helm channel (https://t.me/bale_helm).
Collects comments, feedback, and suggestions from members and forwards them to the channel admin.
Enables the admin to reply directly to user comments from Telegram.
"""

import html
import logging
import os
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
WAITING_FOR_COMMENT = 1


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
        # Matches 'ID: 12345' or 'ID: <code>12345</code>'
        match = re.search(r"ID:\s*(?:<code>)?(\d+)", text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Returns the main inline keyboard shown on /start."""
    keyboard = [
        [InlineKeyboardButton("💬 Send a comment", callback_data="send_comment")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# User Handlers: Start, Cancel & Comment Submission
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles /start command. Greets the user, links to @bale_helm,
    and displays the '💬 Send a comment' button.
    """
    user_name = update.effective_user.first_name if update.effective_user else "there"
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
            reply_markup=get_start_keyboard(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    elif update.message:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=get_start_keyboard(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    return ConversationHandler.END


async def start_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Triggered when the user taps '💬 Send a comment'.
    Prompts the user to enter their message.
    """
    query = update.callback_query
    await query.answer()

    prompt_text = (
        "✍️ <b>Please write your message or comment below.</b>\n\n"
        "You can send text, photos, voice notes, videos, documents, or any other media.\n"
        "Feel free to share suggestions to add or remove content, feedback, or any thoughts.\n\n"
        "<i>Send /cancel at any time if you change your mind.</i>"
    )

    await query.message.reply_text(
        text=prompt_text,
        parse_mode=ParseMode.HTML,
    )

    return WAITING_FOR_COMMENT


async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the user's comment (any message type) and forwards it to ADMIN_ID.
    Sends:
      1. An HTML-formatted header with user details.
      2. The user's original message copied right underneath as a reply to the header.
    """
    user = update.effective_user
    chat = update.effective_chat
    incoming_msg = update.effective_message

    if not user or not incoming_msg:
        return WAITING_FOR_COMMENT

    if not ADMIN_ID:
        logger.error("ADMIN_ID is not configured in .env! Cannot forward comment.")
        await incoming_msg.reply_text(
            "⚠️ The bot is not fully configured to receive comments yet. Please try again later."
        )
        return ConversationHandler.END

    # Escape user input for safe HTML inclusion
    full_name = html.escape(user.full_name or "Unknown")
    username = f"@{html.escape(user.username)}" if user.username else "no username"
    user_id = user.id

    header_text = (
        f"📬 <b>New Comment Received</b>\n\n"
        f"<b>From:</b> {full_name}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>ID:</b> <code>{user_id}</code>\n\n"
        f"💡 <i>Reply to this header or the copied message below to answer the user.</i>"
    )

    try:
        # Step 1: Send the header message to the admin
        header_message = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=header_text,
            parse_mode=ParseMode.HTML,
        )

        # Step 2: Copy the user's message right underneath as a reply to the header
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=chat.id,
            message_id=incoming_msg.message_id,
            reply_parameters=ReplyParameters(message_id=header_message.message_id),
        )

        # Step 3: Confirm to user
        await incoming_msg.reply_text(
            "✅ <b>Thank you!</b> Your comment has been delivered to the channel admin.\n\n"
            "You can send /start anytime to submit another message.",
            parse_mode=ParseMode.HTML,
        )
        logger.info("Successfully forwarded comment from user ID %s to admin %s", user_id, ADMIN_ID)

    except Forbidden:
        logger.error(
            "Cannot message ADMIN_ID %s: Bot was blocked or the admin has never pressed Start on the bot.",
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


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels the comment submission process and resets the state.
    """
    if update.message:
        await update.message.reply_text(
            "❌ Comment submission canceled.\n\n"
            "Send /start whenever you'd like to share something with us!"
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
        # Not a reply to a user comment header/copied message; ignore quietly
        return

    prefix = "💌 Reply from the channel admin:"

    try:
        if admin_msg.text:
            # Text reply
            full_reply_text = f"{prefix}\n\n{admin_msg.text}"
            await context.bot.send_message(
                chat_id=target_user_id,
                text=full_reply_text,
            )
        else:
            # Media reply (photo, video, voice, document, etc.)
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

    # 2. Conversation Handler for user comment submission
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CallbackQueryHandler(start_comment_callback, pattern="^send_comment$"),
        ],
        states={
            WAITING_FOR_COMMENT: [
                CallbackQueryHandler(start_comment_callback, pattern="^send_comment$"),
                MessageHandler(~filters.COMMAND, handle_comment),
            ],
        },
        fallbacks=[
            CommandHandler("start", start_command),
            CommandHandler("cancel", cancel_command),
        ],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)

    # 3. Global cancel command fallback (for users not currently in conversation state)
    application.add_handler(CommandHandler("cancel", cancel_command))

    logger.info("Starting Bale Helm bot with long polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
