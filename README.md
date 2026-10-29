BABLU STAR BD — corrected starter package

Files:
- bot.py: Telegram bot source
- requirements.txt: dependencies

Python: 3.10+
Install: pip install -r requirements.txt
Required environment variables:
  BOT_TOKEN = token from @BotFather
  ADMIN_ID = your numeric Telegram user ID
Optional:
  DB_PATH = bablu_star_bd.sqlite3
Run: python bot.py

IMPORTANT:
- This code has passed Python syntax compilation, but has NOT been tested against your live Telegram bot or Inject Cloud account.
- Set the correct admin ID and token privately in the hosting environment; never share BOT_TOKEN publicly.
- Deposit approvals and withdrawals are manual admin workflows. Test with small amounts / a test bot before real money.
