import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🌟 স্বাগতম BABLU STAR BD-তে, {user.first_name}!\n\n"
        "এটি একটি মাইক্রোজব প্ল্যাটফর্ম।\n\n"
        "📌 /help — সাহায্য\n"
        "💰 /balance — ব্যালেন্স\n"
        "👤 /me — আপনার তথ্য"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 BABLU STAR BD Help\n\n"
        "/start — বট শুরু করুন\n"
        "/balance — ব্যালেন্স দেখুন\n"
        "/me — আপনার Telegram তথ্য দেখুন\n\n"
        "কাজ ও পেমেন্টের ফিচার পরবর্তী ধাপে যোগ করা হবে।"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💰 আপনার ব্যালেন্স ব্যবস্থা এখনো সেটআপ করা হয়নি।"
    )


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👤 নাম: {user.full_name}\n"
        f"🆔 Telegram ID: {user.id}"
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("me", me))

    print("BABLU STAR BD bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
