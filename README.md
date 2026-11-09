# Bale Helm Telegram Bot

Feedback and comment collection bot for the **[Bale Helm | ባለ ሕልም](https://t.me/bale_helm)** Telegram channel. Collects member comments (with optional anonymity), forwards them to the channel admin, and delivers admin replies back to users.

## Setup

1. **Configure environment:**
   Create `.env` (copy from `.env.example`):
   ```env
   BOT_TOKEN=your_bot_token_here
   ADMIN_ID=your_telegram_id
   ```
   *(Note: Press Start on your bot once in Telegram so it can message you).*

2. **Install dependencies:**
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Run:**
   ```bash
   python bot.py
   ```
