# Bale Helm Telegram Bot 🎨✨

The official feedback and comment collection bot for the **[Bale Helm | ባለ ሕልም](https://t.me/bale_helm)** Telegram channel.

This bot allows channel members to submit comments, suggestions, feedback, and creative ideas directly to the channel administrator. The admin can reply directly to any comment from within Telegram, and the bot seamlessly delivers the answer back to the user.

---

## Features

- **Simple Member Flow:**
  - `/start` welcomes members and provides a direct link to the channel.
  - A single inline button (`💬 Send a comment`) starts the submission.
  - Accepts **any message type** (text, photos, voice notes, audio, videos, documents, stickers).
  - `/cancel` fallback ensures users never get stuck in a state.
- **Admin Forwarding:**
  - User details (full name, `@username`, and numeric Telegram ID) are delivered in a clean, HTML-formatted header.
  - The member's original message is copied right underneath as a threaded reply to the header.
- **Direct Admin Replies:**
  - The admin simply replies to either the header or the forwarded message.
  - The bot extracts the user ID and delivers the reply prefixed with `💌 Reply from the channel admin:`.
  - Confirms delivery with `✅ Sent.` or reports an error if the user has blocked the bot.
- **Secure & Extensible:**
  - Built with `python-telegram-bot` v21+ (`asyncio`), `ConversationHandler`, and long polling.
  - Tokens and admin credentials are kept strictly in `.env` (ignored by Git).

---

## Quick Setup Guide

### 1. Create Your Bot with @BotFather
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Choose a display name (e.g. `Bale Helm Bot`) and a unique username ending in `bot` (e.g. `bale_helm_bot`).
4. BotFather will provide your **API Token** (formatted like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).

> [!TIP]
> **Token Security & Revocation:**
> Never share your bot token publicly. If your token is ever exposed, message [@BotFather](https://t.me/BotFather), send `/revoke`, select your bot, and generate a new token immediately. Update your `.env` file with the replacement token.

---

### 2. Configure `.env`
Open the [`.env`](file:///c:/Users/hp/Desktop/bale-helm-bot/.env) file in the root of the project:

```env
BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
ADMIN_ID=6163528558
```

1. Replace `PASTE_YOUR_BOT_TOKEN_HERE` with the token received from `@BotFather`.
2. Confirm that `ADMIN_ID` matches your Telegram numeric user ID (`6163528558`).

---

### 3. Activate Your Bot (Crucial Step)

> [!IMPORTANT]
> Telegram security policies prohibit bots from initiating a conversation with a user who has not previously started the bot.
>
> **Before running the bot, open your bot in Telegram and press "Start" (or send `/start`).**
>
> If you do not perform this one-time step, the bot will not be permitted to deliver incoming member comments to your chat.

---

### 4. Installation & Running

1. **Activate the virtual environment:**
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
   *(Or on Command Prompt: `venv\Scripts\activate.bat`)*

2. **Install requirements:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Start the bot:**
   ```powershell
   python bot.py
   ```

You will see a log confirmation:
```text
[INFO] bale_helm_bot: Starting Bale Helm bot with long polling...
```

---

## How It Works

### For Channel Members
1. User sends `/start` to the bot.
2. Bot greets them with a link to `t.me/bale_helm` and shows `💬 Send a comment`.
3. User taps the button and types/uploads their message (text, image, voice note, etc.).
4. The bot confirms delivery: `"✅ Thank you! Your comment has been delivered to the channel admin."`

### For the Admin
1. The admin receives a notification header:
   ```text
   📬 New Comment Received
   From: John Doe
   Username: @johndoe
   ID: 12345678

   💡 Reply to this header or the copied message below to answer the user.
   ```
2. Below it, the user's message is copied as a reply.
3. The admin replies directly to either message.
4. The bot forwards the reply to the user with the prefix:
   ```text
   💌 Reply from the channel admin:

   <admin's message>
   ```
5. The admin receives a confirmation: `"✅ Sent."`

---

## Project Structure

```text
bale-helm-bot/
├── bot.py               # Core application logic and handlers
├── requirements.txt     # Dependencies (python-telegram-bot, python-dotenv)
├── .env                 # Secret environment variables (ignored by git)
├── .env.example         # Example environment template
├── .gitignore           # Git ignore file (keeps .env and venv private)
└── README.md            # Setup and usage documentation
```
