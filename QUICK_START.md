# Quick Start Guide

## 1. Get Your Telegram Bot Token

1. Open Telegram and search for @BotFather
2. Start a chat with BotFather
3. Send `/newbot` command
4. Follow the instructions to create your bot
5. Copy the token that BotFather provides

## 2. Get Your Telegram User ID

1. Open Telegram and search for @userinfobot
2. Start a chat with userinfobot
3. It will automatically send you your user ID
4. Copy this ID

## 3. Configure the Bot

1. Open the `.env` file in this directory
2. Replace `your_telegram_bot_token_here` with your actual bot token
3. Replace `your_telegram_user_id_here` with your user ID
4. Update the card details if needed:
   ```
   CARD_NUMBER=1234 5678 9012 3456
   CARD_HOLDER=Your Name
   ```

## 4. Install and Run

### Option 1: Quick Setup (Recommended)
```bash
python setup.py
python main.py
```

### Option 2: Manual Installation
```bash
pip install -r requirements.txt
python main.py
```

## 5. Test the Bot

1. Open Telegram
2. Search for your bot by username
3. Send `/start` to begin
4. Follow the on-screen instructions

## 6. Admin Functions

As the admin, you can:
- Review payment receipts
- Approve/reject user subscriptions
- Manage user accounts
- View bot statistics

Access admin functions by sending `/start` and using the admin panel options.

## Need Help?

Check the `README.md` file for detailed documentation or look at the logs in the `logs/` directory for troubleshooting information.