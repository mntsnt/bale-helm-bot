"""
Photo Campaigns module for Bale Helm Telegram Bot.
Handles photo submissions, album grouping, contributor crediting,
and admin campaign management (/photos_on, /photos_off, /photos_status, /credits, /photos_export).
"""

import asyncio
from datetime import datetime, timezone
import html
import io
import re
from typing import Any, Dict, List, Optional
import zipfile

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

from config import ADMIN_ID, CHANNEL_URL, MAX_PHOTOS_PER_USER, logger
import db

# Conversation states for photo flow
WAITING_FOR_CREDIT_NAME = 1
WAITING_FOR_PHOTO = 2

# Global album buffer storage: media_group_id -> dict
ALBUM_BUFFERS: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Admin Guard Decorator / Helper
# ---------------------------------------------------------------------------
def is_admin(update: Update) -> bool:
    """Verifies that the update originated from ADMIN_ID."""
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


# ---------------------------------------------------------------------------
# Admin Commands: /photos_on, /photos_off, /photos_status, /credits, /photos_export
# ---------------------------------------------------------------------------
async def photos_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /photos_on <theme>
    Starts a new photo campaign with the specified theme.
    Restricted to ADMIN_ID (silently ignored for anyone else).
    """
    if not is_admin(update):
        return

    theme = " ".join(context.args).strip() if context.args else ""
    if not theme:
        await update.message.reply_text(
            "ℹ️ <b>Usage:</b> <code>/photos_on &lt;theme&gt;</code>\n\n"
            "Example: <code>/photos_on Minimalist Workspace Setups</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    active = await db.get_active_campaign()
    if active:
        await update.message.reply_text(
            f"⚠️ <b>A campaign is already active!</b>\n\n"
            f"🆔 <b>Campaign ID:</b> <code>{active['id']}</code>\n"
            f"🎨 <b>Theme:</b> {html.escape(active['theme'])}\n\n"
            "Please close it first with <code>/photos_off</code> before starting a new one.",
            parse_mode=ParseMode.HTML,
        )
        return

    campaign = await db.create_campaign(theme)
    if campaign:
        await update.message.reply_text(
            f"✅ <b>Photo campaign started!</b>\n\n"
            f"🆔 <b>Campaign ID:</b> <code>{campaign['id']}</code>\n"
            f"🎨 <b>Theme:</b> {html.escape(campaign['theme'])}\n"
            f"🕒 <b>Started:</b> <code>{campaign['started_at']}</code>\n\n"
            f"<i>The '📸 Send a photo' button is now enabled for members on /start.</i>",
            parse_mode=ParseMode.HTML,
        )
        logger.info("Admin started photo campaign #%s: %s", campaign["id"], theme)
    else:
        await update.message.reply_text("❌ Could not start campaign. Please try again.")


async def photos_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /photos_off
    Closes the active photo campaign and shows photo and contributor count.
    Restricted to ADMIN_ID (silently ignored for anyone else).
    """
    if not is_admin(update):
        return

    closed = await db.close_active_campaign()
    if not closed:
        await update.message.reply_text("ℹ️ No photo campaign is currently active.")
        return

    await update.message.reply_text(
        f"🏁 <b>Photo campaign closed!</b>\n\n"
        f"🆔 <b>Campaign ID:</b> <code>{closed['id']}</code>\n"
        f"🎨 <b>Theme:</b> {html.escape(closed['theme'])}\n"
        f"📸 <b>Total Photos:</b> <code>{closed['photos_count']}</code>\n"
        f"👥 <b>Total Contributors:</b> <code>{closed['contributors_count']}</code>\n"
        f"🕒 <b>Closed At:</b> <code>{closed['ended_at']}</code>\n\n"
        f"<i>Use <code>/credits {closed['id']}</code> to generate contributor credits, "
        f"or <code>/photos_export {closed['id']}</code> to download all files.</i>",
        parse_mode=ParseMode.HTML,
    )
    logger.info("Admin closed photo campaign #%s", closed["id"])


async def photos_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /photos_status
    Shows status of active photo campaign (or reports none active).
    Restricted to ADMIN_ID (silently ignored for anyone else).
    """
    if not is_admin(update):
        return

    active = await db.get_active_campaign()
    if not active:
        await update.message.reply_text(
            "ℹ️ <b>No photo campaign is currently active.</b>\n\n"
            "Start one with <code>/photos_on &lt;theme&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    stats = await db.get_campaign_stats(active["id"])
    await update.message.reply_text(
        f"📸 <b>Active Photo Campaign Status:</b>\n\n"
        f"🆔 <b>Campaign ID:</b> <code>{active['id']}</code>\n"
        f"🎨 <b>Theme:</b> {html.escape(active['theme'])}\n"
        f"📸 <b>Photos Received:</b> <code>{stats['photos_count']}</code>\n"
        f"👥 <b>Contributors:</b> <code>{stats['contributors_count']}</code>\n"
        f"🕒 <b>Started:</b> <code>{active['started_at']}</code>\n\n"
        f"Commands:\n"
        f"• <code>/photos_off</code> - Close this campaign\n"
        f"• <code>/credits</code> - View contributor credits list\n"
        f"• <code>/photos_export</code> - Download submission zip",
        parse_mode=ParseMode.HTML,
    )


async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /credits [campaign_id]
    Sends a ready-to-paste list of contributor credits for the campaign (active by default).
    Restricted to ADMIN_ID (silently ignored for anyone else).
    """
    if not is_admin(update):
        return

    campaign_id: Optional[int] = None
    if context.args:
        try:
            campaign_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Campaign ID must be a number.")
            return

    if campaign_id is None:
        active = await db.get_active_campaign()
        if not active:
            await update.message.reply_text(
                "ℹ️ No active campaign. Specify an ID: <code>/credits &lt;campaign_id&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        campaign_id = active["id"]

    campaign = await db.get_campaign_by_id(campaign_id)
    if not campaign:
        await update.message.reply_text(f"❌ Campaign #{campaign_id} not found.")
        return

    credits_list = await db.get_campaign_credits(campaign_id)
    if not credits_list:
        await update.message.reply_text(
            f"ℹ️ No contributors found for Campaign #{campaign_id} ({html.escape(campaign['theme'])})."
        )
        return

    credits_text = "\n".join(credits_list)
    response = (
        f"📋 <b>Contributor Credits</b> — <i>Campaign #{campaign_id}</i>\n"
        f"🎨 <b>Theme:</b> {html.escape(campaign['theme'])}\n\n"
        f"<code>{html.escape(credits_text)}</code>"
    )
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)


async def photos_export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /photos_export [campaign_id]
    Downloads all photos of the campaign and sends them to admin as a zip file.
    Handles Bot API 20 MB per-file download limit gracefully.
    Restricted to ADMIN_ID (silently ignored for anyone else).
    """
    if not is_admin(update):
        return

    campaign_id: Optional[int] = None
    if context.args:
        try:
            campaign_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Campaign ID must be a number.")
            return

    if campaign_id is None:
        active = await db.get_active_campaign()
        if not active:
            await update.message.reply_text(
                "ℹ️ No active campaign. Specify an ID: <code>/photos_export &lt;campaign_id&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        campaign_id = active["id"]

    campaign = await db.get_campaign_by_id(campaign_id)
    if not campaign:
        await update.message.reply_text(f"❌ Campaign #{campaign_id} not found.")
        return

    submissions = await db.get_campaign_submissions(campaign_id)
    if not submissions:
        await update.message.reply_text(
            f"ℹ️ No photos found for Campaign #{campaign_id} ({html.escape(campaign['theme'])})."
        )
        return

    progress_msg = await update.message.reply_text(
        f"⏳ <b>Preparing export for Campaign #{campaign_id}...</b>\n"
        f"Found {len(submissions)} file(s). Downloading...",
        parse_mode=ParseMode.HTML,
    )

    zip_buffer = io.BytesIO()
    skipped_large_files: List[str] = []
    failed_files: List[str] = []
    downloaded_count = 0

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for idx, sub in enumerate(submissions, start=1):
            file_id = sub["file_id"]
            credit_name = sub["credit_name"]
            sub_id = sub["id"]

            try:
                tg_file = await context.bot.get_file(file_id)
                # Check Bot API 20 MB download limit
                if tg_file.file_size and tg_file.file_size > 20 * 1024 * 1024:
                    mb_size = round(tg_file.file_size / (1024 * 1024), 1)
                    skipped_large_files.append(
                        f"• Sub #{sub_id} ({credit_name}): {mb_size} MB exceeds 20 MB limit"
                    )
                    continue

                file_bytes = await tg_file.download_as_bytearray()

                # Determine file extension
                clean_name = re.sub(r"[^\w\-]", "_", credit_name) or f"user_{sub['user_id']}"
                ext = ".jpg"
                if tg_file.file_path and "." in tg_file.file_path:
                    ext = "." + tg_file.file_path.rsplit(".", 1)[1]
                elif sub.get("is_document"):
                    ext = ".png"

                filename = f"campaign_{campaign_id}/sub_{sub_id}_{clean_name}_{sub['file_unique_id'][:8]}{ext}"
                zip_file.writestr(filename, bytes(file_bytes))
                downloaded_count += 1

            except Exception as e:
                logger.error("Failed to download file for submission %s: %s", sub_id, e)
                failed_files.append(f"• Sub #{sub_id} ({credit_name}): {str(e)}")

    zip_buffer.seek(0)
    zip_size_mb = round(zip_buffer.getbuffer().nbytes / (1024 * 1024), 2)

    caption_lines = [
        f"📦 <b>Export: Campaign #{campaign_id}</b>",
        f"🎨 <b>Theme:</b> {html.escape(campaign['theme'])}",
        f"✅ <b>Downloaded:</b> {downloaded_count}/{len(submissions)} files ({zip_size_mb} MB)",
    ]

    if skipped_large_files:
        caption_lines.append("\n⚠️ <b>Skipped (exceeded 20 MB limit):</b>")
        caption_lines.extend(skipped_large_files[:10])

    if failed_files:
        caption_lines.append("\n❌ <b>Failed to download:</b>")
        caption_lines.extend(failed_files[:5])

    caption = "\n".join(caption_lines)
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    await progress_msg.edit_text("📤 Uploading zip archive to Telegram...")

    try:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=zip_buffer,
            filename=f"bale_helm_campaign_{campaign_id}.zip",
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        await progress_msg.delete()
    except Exception as e:
        logger.error("Failed to upload export zip: %s", e)
        await progress_msg.edit_text(f"❌ Failed to send zip archive: {html.escape(str(e))}")


# ---------------------------------------------------------------------------
# User Photo Submission Flow (ConversationHandler)
# ---------------------------------------------------------------------------
async def start_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Triggered when a member taps '📸 Send a photo'.
    Checks if a campaign is active, resolves credit name, and prompts for photo(s).
    """
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    # Admin cannot submit to self
    if user.id == ADMIN_ID:
        msg = (
            "⚠️ <b>Administrator Notice:</b> You manage photo campaigns.\n\n"
            "Use <code>/photos_status</code>, <code>/photos_on</code>, or <code>/photos_off</code>."
        )
        if query:
            await query.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    active = await db.get_active_campaign()
    if not active:
        msg = (
            "📸 <b>No active photo campaign right now.</b>\n\n"
            f"Please watch our channel <a href=\"{CHANNEL_URL}\">t.me/bale_helm</a> "
            "for the next photo round!"
        )
        if query:
            await query.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return ConversationHandler.END

    context.user_data["active_campaign_id"] = active["id"]
    context.user_data["active_campaign_theme"] = active["theme"]

    # Credit Name Check
    if user.username:
        # Use @username automatically
        credit_name = f"@{user.username.lstrip('@')}"
        context.user_data["credit_name"] = credit_name
        return await show_photo_prompt(update, context, active["theme"], credit_name)
    else:
        # Check if saved in DB
        saved_credit = await db.get_user_credit_name(user.id)
        if saved_credit:
            context.user_data["credit_name"] = saved_credit
            return await show_photo_prompt(update, context, active["theme"], saved_credit)
        else:
            # Ask once what name they want to be credited as
            prompt = (
                "🎨 <b>Credit Name:</b>\n\n"
                "You don't have a public Telegram @username. "
                "Please reply with the <b>display name</b> you would like to be credited with "
                "on the channel when your photo is featured.\n\n"
                "<i>Send /cancel to abort.</i>"
            )
            if query:
                await query.message.reply_text(prompt, parse_mode=ParseMode.HTML)
            return WAITING_FOR_CREDIT_NAME


async def handle_credit_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives and stores preferred credit name for users without @username."""
    user = update.effective_user
    text = update.message.text.strip() if update.message and update.message.text else ""

    if not text:
        await update.message.reply_text("Please enter a valid text name for your credit:")
        return WAITING_FOR_CREDIT_NAME

    # Save to SQLite users table
    await db.set_user_credit_name(user.id, text)
    context.user_data["credit_name"] = text
    theme = context.user_data.get("active_campaign_theme", "Active Campaign")

    await update.message.reply_text(
        f"✓ Credit name saved as <b>{html.escape(text)}</b>.",
        parse_mode=ParseMode.HTML,
    )
    return await show_photo_prompt(update, context, theme, text)


async def show_photo_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, theme: str, credit_name: str
) -> int:
    """Displays the legal notice, tips, and prompts for photo upload."""
    notice_text = (
        f"📸 <b>Campaign:</b> {html.escape(theme)}\n"
        f"👤 <b>Your Credit:</b> {html.escape(credit_name)}\n\n"
        "By sending a photo you confirm it's yours and agree that it may be edited "
        "and published on the channel with your credit. "
        "Tip: send it as a File (not a compressed photo) to keep the original quality.\n\n"
        f"<i>Limit: Maximum {MAX_PHOTOS_PER_USER} photos per user.\n"
        "Send your photo(s) now, or /cancel to finish.</i>"
    )

    if update.callback_query:
        await update.callback_query.message.reply_text(notice_text, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(notice_text, parse_mode=ParseMode.HTML)

    return WAITING_FOR_PHOTO


# ---------------------------------------------------------------------------
# Album Finalizer Task
# ---------------------------------------------------------------------------
async def finalize_album_confirmation(context: ContextTypes.DEFAULT_TYPE, media_group_id: str) -> None:
    """
    Waits briefly for all updates of an album to arrive, then sends
    ONE consolidated confirmation message to the user.
    """
    await asyncio.sleep(1.0)  # Wait for subsequent media group updates

    buffer = ALBUM_BUFFERS.pop(media_group_id, None)
    if not buffer:
        return

    chat_id = buffer["chat_id"]
    accepted = buffer["accepted_count"]
    duplicates = buffer["duplicate_count"]
    limit_hit = buffer["limit_hit"]
    remaining = buffer["remaining"]

    lines = []
    if accepted > 0:
        lines.append("✅ Received! Thank you.")
        lines.append(f"📸 <b>{accepted} photo(s) accepted.</b>")
    if duplicates > 0:
        lines.append(f"⚠️ {duplicates} duplicate photo(s) were ignored.")
    if limit_hit:
        lines.append(f"⚠️ Limit reached ({MAX_PHOTOS_PER_USER} photos max).")

    lines.append(f"\nRemaining quota: <b>{remaining}</b> photos.")
    lines.append("You can send more photos or /cancel to finish.")

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Could not send album confirmation to %s: %s", chat_id, e)


# ---------------------------------------------------------------------------
# Photo & Document Handler
# ---------------------------------------------------------------------------
async def handle_photo_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Processes photos and image documents sent by members.
    Enforces campaign activity, per-user limits, and deduplication.
    Forwards accepted files to ADMIN_ID with a header and copies the media underneath.
    Groups album confirmations into a single message.
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user:
        return WAITING_FOR_PHOTO

    # Check active campaign
    campaign = await db.get_active_campaign()
    if not campaign:
        await msg.reply_text(
            "📸 There is no active photo campaign right now.\n"
            f"Watch <a href=\"{CHANNEL_URL}\">t.me/bale_helm</a> for announcements!",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    campaign_id = campaign["id"]
    theme = campaign["theme"]

    # Extract photo or image document
    file_id: Optional[str] = None
    file_unique_id: Optional[str] = None
    is_doc = False

    if msg.photo:
        # Highest resolution photo is last in the list
        photo_obj = msg.photo[-1]
        file_id = photo_obj.file_id
        file_unique_id = photo_obj.file_unique_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        file_id = msg.document.file_id
        file_unique_id = msg.document.file_unique_id
        is_doc = True
    else:
        await msg.reply_text(
            "⚠️ Please send an image (photo or image file).\n"
            "Send /cancel if you wish to exit."
        )
        return WAITING_FOR_PHOTO

    media_group_id = msg.media_group_id

    # Resolve credit name
    credit_name = context.user_data.get("credit_name")
    if not credit_name:
        if user.username:
            credit_name = f"@{user.username.lstrip('@')}"
        else:
            credit_name = await db.get_user_credit_name(user.id) or user.full_name or "Anonymous"
        context.user_data["credit_name"] = credit_name

    # Check user submission limit
    current_count = await db.get_user_submission_count(campaign_id, user.id)

    # Check if duplicate
    is_duplicate = await db.is_file_unique_id_submitted(campaign_id, file_unique_id)

    # Album handling: initialize or update buffer
    if media_group_id:
        if media_group_id not in ALBUM_BUFFERS:
            ALBUM_BUFFERS[media_group_id] = {
                "chat_id": chat.id,
                "accepted_count": 0,
                "duplicate_count": 0,
                "limit_hit": False,
                "remaining": max(0, MAX_PHOTOS_PER_USER - current_count),
            }
            asyncio.create_task(finalize_album_confirmation(context, media_group_id))

        buffer = ALBUM_BUFFERS[media_group_id]

        if is_duplicate:
            buffer["duplicate_count"] += 1
            return WAITING_FOR_PHOTO

        if current_count >= MAX_PHOTOS_PER_USER:
            buffer["limit_hit"] = True
            return WAITING_FOR_PHOTO

    else:
        # Single file check
        if is_duplicate:
            await msg.reply_text("⚠️ This photo was already received for this campaign.")
            return WAITING_FOR_PHOTO

        if current_count >= MAX_PHOTOS_PER_USER:
            await msg.reply_text(
                f"⚠️ You have reached the limit of {MAX_PHOTOS_PER_USER} photos for this campaign.\n"
                "Thank you for contributing!"
            )
            return ConversationHandler.END

    # Accept & Forward to ADMIN_ID
    sub_num = current_count + 1
    username_str = f"@{html.escape(user.username)}" if user.username else "no username"
    time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    admin_header = (
        "╭─ 📸 <b>NEW PHOTO SUBMISSION</b> ─────\n"
        f"│ 🎨 <b>Campaign:</b> {html.escape(theme)}\n"
        f"│ 👤 <b>Credit:</b> {html.escape(credit_name)}\n"
        f"│ 🏷 <b>Username:</b> {username_str}\n"
        f"│ 🆔 <b>ID:</b> <code>{user.id}</code>  ·  🕒 <code>{time_str}</code>\n"
        f"│ 🔢 <b>Submission:</b> #{sub_num} of {MAX_PHOTOS_PER_USER}\n"
        "╰────────────────────────────────"
    )

    try:
        header_msg = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_header,
            parse_mode=ParseMode.HTML,
        )

        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=chat.id,
            message_id=msg.message_id,
            reply_parameters=ReplyParameters(message_id=header_msg.message_id),
        )

        # Store in SQLite
        await db.add_submission(
            campaign_id=campaign_id,
            user_id=user.id,
            username=user.username,
            credit_name=credit_name,
            file_id=file_id,
            file_unique_id=file_unique_id,
            is_document=is_doc,
        )
        logger.info(
            "Saved photo submission #%s for campaign #%s by user %s",
            sub_num,
            campaign_id,
            user.id,
        )

    except Forbidden:
        logger.error("Admin ID %s has not started the bot or blocked it.", ADMIN_ID)
    except TelegramError as e:
        logger.error("Failed to forward photo to admin: %s", e)

    # Confirmation
    if media_group_id:
        buffer = ALBUM_BUFFERS[media_group_id]
        buffer["accepted_count"] += 1
        buffer["remaining"] = max(0, MAX_PHOTOS_PER_USER - sub_num)
    else:
        remaining = max(0, MAX_PHOTOS_PER_USER - sub_num)
        await msg.reply_text(
            f"✅ Received! Thank you.\n\n"
            f"📸 <b>{sub_num}/{MAX_PHOTOS_PER_USER}</b> photos submitted.\n"
            f"You can send more or /cancel to finish.",
            parse_mode=ParseMode.HTML,
        )

    return WAITING_FOR_PHOTO


async def cancel_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the photo submission flow."""
    context.user_data.pop("active_campaign_id", None)
    context.user_data.pop("active_campaign_theme", None)
    if update.message:
        await update.message.reply_text(
            "❌ Photo submission finished.\n\n"
            "Send /start whenever you would like to return to the main menu."
        )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Outside Flow Photo Guard
# ---------------------------------------------------------------------------
async def handle_photo_outside_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catches photos sent when user is NOT in the photo submission flow.
    If no campaign is active: informs user to watch channel.
    Photos sent outside flow are NEVER forwarded to admin.
    """
    active = await db.get_active_campaign()
    if not active:
        await update.message.reply_text(
            "📸 There is no active photo campaign right now.\n"
            f"Please watch <a href=\"{CHANNEL_URL}\">t.me/bale_helm</a> for the next round!",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            f"📸 We have an active photo campaign: <b>{html.escape(active['theme'])}</b>!\n\n"
            "To submit photos and receive proper credit, please send /start "
            "and tap <b>'📸 Send a photo'</b>.",
            parse_mode=ParseMode.HTML,
        )


def get_photos_conversation_handler() -> ConversationHandler:
    """Creates and returns the ConversationHandler for photo submissions."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_photo_callback, pattern="^send_photo$"),
        ],
        states={
            WAITING_FOR_CREDIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_credit_name_input),
            ],
            WAITING_FOR_PHOTO: [
                MessageHandler(
                    filters.PHOTO | (filters.Document.IMAGE & ~filters.COMMAND),
                    handle_photo_submission,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_photo_submission),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_photo),
        ],
        allow_reentry=True,
    )
