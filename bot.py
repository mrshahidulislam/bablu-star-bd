import os
import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "bablu_star.db"


def setup_database():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL NOT NULL DEFAULT 0.00
            )
        """)


def get_user(user):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO users
               (user_id, username, balance)
               VALUES (?, ?, 0.00)""",
            (user.id, user.username or "")
        )
        conn.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (user.username or "", user.id)
        )
        row = conn.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user.id,)
        ).fetchone()
        return row[0]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    await update.message.reply_text(
        "স্বাগতম BABLU STAR BD-তে! 🎉\n\n"
        "/help - সাহায্য\n"
        "/balance - আপনার ব্যালেন্স\n"
        "/me - আপনার প্রোফাইল"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = get_user(update.effective_user)
    await update.message.reply_text(
        f"💰 আপনার বর্তমান ব্যালেন্স: ৳{amount:.2f}"
    )


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    amount = get_user(user)
    await update.message.reply_text(
        f"👤 আপনার প্রোফাইল\n"
        f"নাম: {user.full_name}\n"
        f"ইউজার আইডি: {user.id}\n"
        f"ব্যালেন্স: ৳{amount:.2f}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 BABLU STAR BD সাহায্য\n"
        "/start - শুরু করুন\n"
        "/balance - ব্যালেন্স দেখুন\n"
        "/me - প্রোফাইল দেখুন"
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN সেট করা হয়নি")

    setup_database()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("help", help_command))

    print("BABLU STAR BD bot is running")
    app.run_polling()


if __name__ == "__main__":
    main()
