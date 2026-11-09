# Bale Helm Telegram Bot

Official Telegram bot for the **[Bale Helm | ባለ ሕልም](https://t.me/bale_helm)** channel.

Provides two core features:
1. **Member Comments:** Collects feedback and suggestions from channel members, forwards them to the admin, and supports direct in-chat admin replies.
2. **Photo Campaigns:** Contributor photo rounds that the admin switches on and off with submission limits, album deduplication, copy-paste credits, and zip export.

---

## Setup Guide

### 1. Create Bot with @BotFather
1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to choose a name and username.
3. Copy the HTTP API token provided.

> **Security & Token Revocation:**
> Keep your bot token secret. If your token is ever compromised, message [@BotFather](https://t.me/BotFather), send `/revoke`, select your bot, and copy the new replacement token.

### 2. Configure `.env`
Create or edit `.env` in the root folder:
```env
BOT_TOKEN=your_bot_token_here
ADMIN_ID=6163528558
```
*(Replace `your_bot_token_here` with the token from @BotFather).*

### 3. Activate Your Bot (Important)
Telegram prohibits bots from initiating conversations with users who haven't started them first.
**Open your bot in Telegram and press Start (or send `/start`) before running it.**

### 4. Install Dependencies
```bash
python -m venv venv
.\venv\Scripts\activate   # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
```

### 5. Run the Bot
```bash
python bot.py
```

---

## Admin Commands

All admin commands are restricted to `ADMIN_ID` and silently ignored for other users.

| Command | Description |
|---|---|
| `/photos_on <theme>` | Starts a new photo campaign with the specified theme (e.g. `/photos_on Minimalist UI`). |
| `/photos_off` | Closes the active photo campaign and displays total photos and contributors. |
| `/photos_status` | Displays whether a campaign is active, its theme, and current photo/contributor counts. |
| `/credits [campaign_id]` | Generates a ready-to-paste list of contributor credits (active campaign by default). |
| `/photos_export [campaign_id]` | Packages all campaign submissions into a downloadable `.zip` file. |

---

## Project Structure

```text
bale-helm-bot/
├── bot.py           # Application entry point and handler routing
├── comments.py      # Member comments and admin reply handlers
├── photos.py        # Photo campaign submission flow and admin campaign commands
├── db.py            # SQLite database layer (campaigns, submissions, users)
├── config.py        # Settings, limits, and environment variables
├── requirements.txt # Python dependencies (python-telegram-bot, aiosqlite, python-dotenv)
├── .env             # Bot credentials (ignored by Git)
├── .env.example     # Environment template
├── .gitignore       # Keeps .env, bot.db, and venv private
└── README.md        # Documentation
```
