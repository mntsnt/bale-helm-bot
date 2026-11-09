#!/usr/bin/env python3
"""
Bale Helm Telegram Bot
Official bot for the Bale Helm Telegram channel (https://t.me/bale_helm).

Features:
1. Member Comments: Collects feedback & suggestions, forwards to admin, and routes admin replies.
2. Photo Campaigns: Contributor photo collection with on/off switching, quota limits, album deduplication, and export.
"""

import html
import sys

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from comments import get_comments_conversation_handler, handle_admin_reply
from config import ADMIN_ID, BOT_TOKEN, CHANNEL_URL, logger
import db
from photos import (
    credits_command,
    get_photos_conversation_handler,
    handle_photo_outside_flow,
    photos_export_command,
    photos_off_command,
    photos_on_command,
    photos_status_command,
)


async def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Builds the main menu inline keyboard.
    '💬 Send a comment' is always shown.
    '📸 Send a photo' is shown only while a photo campaign is active.
    """
    keyboard = [
        [InlineKeyboardButton("💬 Send a comment", callback_data="send_comment")]
    ]

    active_campaign = await db.get_active_campaign()
    if active_campaign:
        keyboard.append(
            [InlineKeyboardButton("📸 Send a photo", callback_data="send_photo")]
        )

    return InlineKeyboardMarkup(keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /start command.
    - Regular users: Greets them, mentions channel link, and shows available action buttons.
    - Admin: Displays admin greeting, active campaign status, and available admin commands.
    """
    user = update.effective_user
    if not user:
        return

    # -----------------------------------------------------------------------
    # Admin View
    # -----------------------------------------------------------------------
    if user.id == ADMIN_ID:
        active = await db.get_active_campaign()
        if active:
            status_text = (
                f"🟢 <b>Active Campaign:</b> #{active['id']} — <i>{html.escape(active['theme'])}</i>"
            )
        else:
            status_text = "⚪ <b>No photo campaign currently active.</b>"

        admin_welcome = (
            f"👑 <b>Bale Helm Admin Console</b>\n\n"
            f"Welcome back, <b>{html.escape(user.first_name)}</b>!\n"
            f"Channel: <a href=\"{CHANNEL_URL}\">t.me/bale_helm</a>\n\n"
            f"Current Status: {status_text}\n\n"
            "<b>Admin Commands:</b>\n"
            "• <code>/photos_on &lt;theme&gt;</code> — Start a new photo campaign\n"
            "• <code>/photos_off</code> — Close the active photo campaign\n"
            "• <code>/photos_status</code> — Check campaign metrics\n"
            "• <code>/credits [id]</code> — Get copyable contributor credits list\n"
            "• <code>/photos_export [id]</code> — Download submissions as zip\n\n"
            "<i>💡 Reply directly to any comment or photo header in this chat to message that member.</i>"
        )

        keyboard = await get_main_menu_keyboard()
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=admin_welcome,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        elif update.message:
            await update.message.reply_text(
                text=admin_welcome,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        return

    # -----------------------------------------------------------------------
    # Regular Member View
    # -----------------------------------------------------------------------
    user_name = user.first_name or "there"
    welcome_text = (
        f"👋 Hello, <b>{html.escape(user_name)}</b>!\n\n"
        f"Welcome to the official bot for <b>Bale Helm | ባለ ሕልም</b> "
        f"(<a href=\"{CHANNEL_URL}\">t.me/bale_helm</a>).\n\n"
        "How can we help you today? Choose an option below:"
    )

    keyboard = await get_main_menu_keyboard()

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=welcome_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    elif update.message:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global fallback /cancel command."""
    if update.message:
        await update.message.reply_text(
            "❌ Action canceled.\n\n"
            "Send /start whenever you would like to begin again."
        )


async def on_startup(application: Application) -> None:
    """Initializes SQLite database and tables on startup."""
    await db.init_db()


def main() -> None:
    """Initializes and runs the bot with long polling."""
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        logger.error(
            "BOT_TOKEN is not configured! Please open your .env file and set BOT_TOKEN to your actual token."
        )
        sys.exit(1)

    if not ADMIN_ID:
        logger.warning("ADMIN_ID is not configured in .env. Admin features will be unavailable.")

    # Build Telegram application
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # 1. Admin reply handler (MUST be registered first to take priority over conversation handlers)
    admin_reply_filter = (
        filters.User(user_id=ADMIN_ID)
        & filters.REPLY
        & ~filters.COMMAND
    )
    application.add_handler(MessageHandler(admin_reply_filter, handle_admin_reply))

    # 2. Admin Photo Campaign Commands (Restricted to ADMIN_ID, silently ignored for others)
    admin_filter = filters.User(user_id=ADMIN_ID)
    application.add_handler(CommandHandler("photos_on", photos_on_command, filters=admin_filter))
    application.add_handler(CommandHandler("photos_off", photos_off_command, filters=admin_filter))
    application.add_handler(CommandHandler("photos_status", photos_status_command, filters=admin_filter))
    application.add_handler(CommandHandler("credits", credits_command, filters=admin_filter))
    application.add_handler(CommandHandler("photos_export", photos_export_command, filters=admin_filter))

    # 3. Feature Conversation Handlers
    application.add_handler(get_comments_conversation_handler())
    application.add_handler(get_photos_conversation_handler())

    # 4. Global navigation handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # 5. Fallback handler for photos sent outside the photo flow / when no campaign is active
    photo_filter = (filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND
    application.add_handler(MessageHandler(photo_filter, handle_photo_outside_flow))

    logger.info("Starting Bale Helm bot with long polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
