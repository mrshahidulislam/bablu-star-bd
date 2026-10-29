BABLU STAR BD - corrected code package

Files:
- bot.py
- requirements.txt

Required environment variables on the hosting service:
- BOT_TOKEN: your Telegram bot token (keep it secret)
- ADMIN_ID: your numeric Telegram user ID
- DB_PATH: optional; defaults to bablu_star_bd.sqlite3

Important:
This version adds a settings table, editable support email, editable withdrawal fee,
editable category minimum prices/enable-disable, admin review shortcuts, and payment-method settings.

Before replacing a running bot:
1. Stop the bot.
2. Back up the existing SQLite database.
3. If the existing database was created by a different schema/version, use a new DB_PATH
   such as bablu_star_bd_v2.sqlite3 for testing. Do not delete the old database.
4. Upload bot.py and requirements.txt, install requirements, then restart.
5. Test with a small test account and test deposit/withdraw requests before real use.

The code was syntax-checked here, but not live-tested against your Telegram bot or hosting account.
