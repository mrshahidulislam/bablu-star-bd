
import os
import sqlite3
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ======================================
# BABLU STAR BD - PART 1 OF 2
# Configuration, database and helpers
# ======================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "bablu_star_bd.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

if not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID must be a numeric Telegram user ID.")

ADMIN_ID = int(ADMIN_ID_RAW)

FEE_RATE = Decimal("0.15")
REFERRAL_RATE = Decimal("0.10")
USD_TO_BDT = Decimal("100")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("bablu_star_bd")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def amount(value):
    return Decimal(str(value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def taka(value):
    return f"৳{amount(value):,.2f}"


def payout_calculation(gross):
    gross = amount(gross)
    fee = amount(gross * FEE_RATE)
    net = amount(gross - fee)
    return gross, fee, net


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with connect_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'freelancer'
                CHECK(mode IN ('buyer', 'freelancer')),
            buyer_balance TEXT NOT NULL DEFAULT '0.00',
            freelancer_balance TEXT NOT NULL DEFAULT '0.00',
            referred_by INTEGER,
            referral_paid INTEGER NOT NULL DEFAULT 0,
            banned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            task_count INTEGER NOT NULL CHECK(task_count >= 0),
            price_per_task TEXT NOT NULL,
            reserved_budget TEXT NOT NULL,
            rules TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY(buyer_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS submissions (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            freelancer_id INTEGER NOT NULL,
            proof TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            UNIQUE(job_id, freelancer_id),
            FOREIGN KEY(job_id) REFERENCES jobs(job_id),
            FOREIGN KEY(freelancer_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS deposits (
            deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount TEXT NOT NULL,
            method TEXT NOT NULL,
            transaction_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            withdrawal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            balance_type TEXT NOT NULL,
            gross TEXT NOT NULL,
            fee TEXT NOT NULL,
            net TEXT NOT NULL,
            method TEXT NOT NULL,
            account TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """)


def audit(actor_id, action, details=""):
    with connect_db() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (actor_id, action, details, created_at)
               VALUES (?, ?, ?, ?)""",
            (actor_id, action, details, now()),
        )


def get_user(user_id):
    with connect_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def ensure_user(tg_user, referral_id=None):
    if referral_id == tg_user.id:
        referral_id = None

    with connect_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO users
               (user_id, username, full_name, referred_by, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                tg_user.id,
                tg_user.username or "",
                tg_user.full_name or "",
                referral_id,
                now(),
            ),
        )

        conn.execute(
            """UPDATE users SET username=?, full_name=?
               WHERE user_id=?""",
            (tg_user.username or "", tg_user.full_name or "", tg_user.id),
        )

        return conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (tg_user.id,),
        ).fetchone()


def is_admin(user_id):
    return user_id == ADMIN_ID


def is_blocked(user_id):
    user = get_user(user_id)
    return user is None or bool(user["banned"])


def main_menu(mode):
    if mode == "buyer":
        rows = [
            ["➕ কাজ পোস্ট", "📋 আমার কাজ"],
            ["💰 ব্যালেন্স", "➕ ডিপোজিট"],
            ["💸 উত্তোলন", "🔄 Freelancer Mode"],
            ["👥 রেফার", "ℹ️ সাহায্য"],
        ]
    else:
        rows = [
            ["🔎 কাজ খুঁজুন", "📨 আমার সাবমিশন"],
            ["💰 ব্যালেন্স", "➕ ডিপোজিট"],
            ["💸 উত্তোলন", "🔄 Buyer Mode"],
            ["👥 রেফার", "ℹ️ সাহায্য"],
        ]

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💳 Pending Deposits",
            callback_data="admin:deposits",
        )],
        [InlineKeyboardButton(
            "💸 Pending Withdrawals",
            callback_data="admin:withdrawals",
        )],
        [InlineKeyboardButton(
            "📋 Pending Submissions",
            callback_data="admin:submissions",
        )],
        [InlineKeyboardButton(
            "📊 Finance Summary",
            callback_data="admin:finance",
        )],
    ])


# END OF PART 1
# Add PART 2 directly below this line.

# ======================================
# BABLU STAR BD - PART 2 OF 2
# Menus, jobs, deposits, withdrawals,
# referrals, admin actions and startup
# ======================================

from telegram.error import TelegramError


# Add a column to existing databases safely.
def migrate_db():
    with connect_db() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(deposits)")
        }
        if "balance_type" not in columns:
            conn.execute(
                "ALTER TABLE deposits ADD COLUMN "
                "balance_type TEXT NOT NULL DEFAULT 'freelancer'"
            )


def set_mode(user_id, mode):
    with connect_db() as conn:
        conn.execute(
            "UPDATE users SET mode=? WHERE user_id=?",
            (mode, user_id),
        )


def add_balance(user_id, balance_type, value):
    column = (
        "buyer_balance"
        if balance_type == "buyer"
        else "freelancer_balance"
    )
    with connect_db() as conn:
        row = conn.execute(
            f"SELECT {column} AS bal FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return
        new_value = amount(Decimal(row["bal"]) + amount(value))
        conn.execute(
            f"UPDATE users SET {column}=? WHERE user_id=?",
            (str(new_value), user_id),
        )


def balance_text(user_id):
    user = get_user(user_id)
    return (
        "💰 আপনার ব্যালেন্স\n\n"
        f"🛒 Buyer Balance: {taka(user['buyer_balance'])}\n"
        f"👨‍💻 Freelancer Balance: {taka(user['freelancer_balance'])}"
    )


async def safe_send(context, user_id, text, **kwargs):
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            **kwargs,
        )
    except TelegramError:
        logger.warning("Could not message Telegram user %s", user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    ref_id = None

    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_") and arg[4:].isdigit():
            ref_id = int(arg[4:])

    user = ensure_user(tg, ref_id)

    if is_blocked(tg.id):
        await update.message.reply_text("⛔ আপনার অ্যাকাউন্ট বন্ধ আছে।")
        return

    await update.message.reply_text(
        "⭐ BABLU STAR BD-তে স্বাগতম!\n\n"
        "একটি মোড বেছে নিয়ে কাজ শুরু করুন।\n"
        "💱 হিসাব: 1 USD = ৳100\n"
        "💸 উত্তোলন ফি: 15%",
        reply_markup=main_menu(user["mode"]),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ BABLU STAR BD সাহায্য\n\n"
        "• Buyer Mode-এ কাজ পোস্ট করুন।\n"
        "• Freelancer Mode-এ কাজ করে প্রমাণ জমা দিন।\n"
        "• ডিপোজিট অ্যাডমিন যাচাই করার পর ব্যালেন্সে যোগ হবে।\n"
        "• উত্তোলনে 15% ফি কাটা হবে।\n"
        "• পেমেন্ট ম্যানুয়ালি যাচাই করা হয়; স্বয়ংক্রিয় পেমেন্ট নয়।\n"
        "• প্রতারণা বা ভুয়া প্রমাণ দিলে অ্যাকাউন্ট বন্ধ হতে পারে।\n\n"
        "সমস্যা হলে অ্যাডমিনের সঙ্গে যোগাযোগ করুন।"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ অনুমতি নেই।")
        return
    await update.message.reply_text(
        "🛠 Admin Panel",
        reply_markup=admin_keyboard(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    uid = update.effective_user.id
    user = get_user(uid)
    if not user or is_blocked(uid):
        await msg.reply_text("⛔ অ্যাকাউন্ট ব্যবহার করা যাচ্ছে না।")
        return

    text = msg.text.strip()
    stage = context.user_data.get("stage")

    # ----- Step-by-step user flows -----
    if stage:
        await process_stage(update, context, stage, text)
        return

    if text == "🔄 Buyer Mode":
        set_mode(uid, "buyer")
        await msg.reply_text("🛒 Buyer Mode চালু হয়েছে।",
                             reply_markup=main_menu("buyer"))
        return

    if text == "🔄 Freelancer Mode":
        set_mode(uid, "freelancer")
        await msg.reply_text("👨‍💻 Freelancer Mode চালু হয়েছে।",
                             reply_markup=main_menu("freelancer"))
        return

    if text == "💰 ব্যালেন্স":
        await msg.reply_text(balance_text(uid))
        return

    if text == "ℹ️ সাহায্য":
        await help_command(update, context)
        return

    if text == "👥 রেফার":
        me = await context.bot.get_me()
        await msg.reply_text(
            "👥 আপনার রেফারেল লিংক:\n"
            f"https://t.me/{me.username}?start=ref_{uid}\n\n"
            "আপনার রেফার করা ব্যক্তির প্রথম অনুমোদিত "
            "উত্তোলনের মোট অঙ্কের 10% একবার বোনাস পাবেন।"
        )
        return

    if text == "➕ ডিপোজিট":
        context.user_data["stage"] = "deposit_amount"
        await msg.reply_text(
            "কত টাকা ডিপোজিট করতে চান? শুধু সংখ্যায় লিখুন।"
        )
        return

    if text == "💸 উত্তোলন":
        context.user_data["stage"] = "withdraw_amount"
        await msg.reply_text(
            "কত টাকা উত্তোলন করতে চান? শুধু সংখ্যায় লিখুন।"
        )
        return

    if text == "➕ কাজ পোস্ট":
        if user["mode"] != "buyer":
            await msg.reply_text("আগে Buyer Mode চালু করুন।")
            return
        context.user_data["stage"] = "job_title"
        await msg.reply_text("কাজের শিরোনাম লিখুন।")
        return

    if text == "📋 আমার কাজ":
        with connect_db() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE buyer_id=? ORDER BY job_id DESC",
                (uid,),
            ).fetchall()
        if not rows:
            await msg.reply_text("আপনার কোনো কাজ নেই।")
            return
        for job in rows:
            await msg.reply_text(
                f"📋 Job #{job['job_id']}\n"
                f"শিরোনাম: {job['title']}\n"
                f"অবশিষ্ট টাস্ক: {job['task_count']}\n"
                f"প্রতি টাস্ক: {taka(job['price_per_task'])}\n"
                f"স্ট্যাটাস: {job['status']}"
            )
        return

    if text == "🔎 কাজ খুঁজুন":
        if user["mode"] != "freelancer":
            await msg.reply_text("আগে Freelancer Mode চালু করুন।")
            return
        with connect_db() as conn:
            jobs = conn.execute(
                "SELECT * FROM jobs WHERE status='open' "
                "AND task_count>0 ORDER BY job_id DESC LIMIT 20"
            ).fetchall()
        if not jobs:
            await msg.reply_text("এখন কোনো কাজ খোলা নেই।")
            return
        for job in jobs:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📨 কাজ জমা দিন",
                    callback_data=f"submit:{job['job_id']}",
                )
            ]])
            await msg.reply_text(
                f"📋 Job #{job['job_id']}\n"
                f"শিরোনাম: {job['title']}\n"
                f"বিবরণ: {job['description']}\n"
                f"নিয়ম: {job['rules'] or 'নির্দিষ্ট নিয়ম নেই'}\n"
                f"অবশিষ্ট টাস্ক: {job['task_count']}\n"
                f"প্রতি টাস্ক: {taka(job['price_per_task'])}",
                reply_markup=keyboard,
            )
        return

    if text == "📨 আমার সাবমিশন":
        with connect_db() as conn:
            rows = conn.execute(
                """SELECT s.*, j.title FROM submissions s
                   JOIN jobs j ON j.job_id=s.job_id
                   WHERE s.freelancer_id=? ORDER BY s.submission_id DESC
                   LIMIT 20""",
                (uid,),
            ).fetchall()
        if not rows:
            await msg.reply_text("আপনার কোনো সাবমিশন নেই।")
            return
        for row in rows:
            await msg.reply_text(
                f"📨 {row['title']}\n"
                f"Submission #{row['submission_id']}\n"
                f"স্ট্যাটাস: {row['status']}"
            )
        return

    await msg.reply_text(
        "মেনু থেকে একটি অপশন বেছে নিন।",
        reply_markup=main_menu(user["mode"]),
    )


async def process_stage(update, context, stage, text):
    msg = update.message
    uid = update.effective_user.id

    def stop_stage():
        context.user_data.pop("stage", None)

    if stage in ("deposit_amount", "withdraw_amount",
                 "job_price", "job_count"):
        try:
            value = amount(text)
            if value <= 0:
                raise ValueError
            if stage in ("job_count",) and value != value.to_integral_value():
                raise ValueError
        except Exception:
            await msg.reply_text("সঠিক ধনাত্মক সংখ্যা লিখুন।")
            return

    if stage == "deposit_amount":
        context.user_data["deposit_amount"] = str(value)
        context.user_data["stage"] = "deposit_method"
        await msg.reply_text("পদ্ধতি লিখুন: bKash অথবা Nagad")
        return

    if stage == "deposit_method":
        method = text.lower()
        if method not in ("bkash", "nagad", "বিকাশ", "নগদ"):
            await msg.reply_text("bKash অথবা Nagad লিখুন।")
            return
        context.user_data["deposit_method"] = method
        context.user_data["stage"] = "deposit_balance"
        await msg.reply_text(
            "কোন ব্যালেন্সে জমা হবে?\n"
            "buyer অথবা freelancer লিখুন।"
        )
        return

    if stage == "deposit_balance":
        balance_type = text.lower()
        if balance_type not in ("buyer", "freelancer"):
            await msg.reply_text("buyer অথবা freelancer লিখুন।")
            return
        context.user_data["deposit_balance"] = balance_type
        context.user_data["stage"] = "deposit_txn"
        await msg.reply_text(
            "পেমেন্টের Transaction ID লিখুন।\n"
            "অ্যাডমিন যাচাই না করা পর্যন্ত ব্যালেন্স যোগ হবে না।"
        )
        return

    if stage == "deposit_txn":
        txn = text.strip()
        if len(txn) < 4 or len(txn) > 100:
            await msg.reply_text("Transaction ID সঠিকভাবে লিখুন।")
            return
        try:
            with connect_db() as conn:
                cur = conn.execute(
                    """INSERT INTO deposits
                       (user_id, amount, method, transaction_id,
                        balance_type, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                    (
                        uid,
                        context.user_data["deposit_amount"],
                        context.user_data["deposit_method"],
                        txn,
                        context.user_data["deposit_balance"],
                        now(),
                    ),
                )
                dep_id = cur.lastrowid
        except sqlite3.IntegrityError:
            await msg.reply_text(
                "❌ এই Transaction ID আগে জমা হয়েছে।"
            )
            stop_stage()
            return
        audit(uid, "deposit_created", f"deposit={dep_id}")
        stop_stage()
        await msg.reply_text(
            f"✅ ডিপোজিট রিকোয়েস্ট #{dep_id} জমা হয়েছে। "
            "অ্যাডমিন যাচাই করবেন।"
        )
        await safe_send(
            context, ADMIN_ID,
            f"💳 নতুন ডিপোজিট #{dep_id}\n"
            f"User: {uid}\n"
            f"Amount: {taka(context.user_data.get('deposit_amount', 0))}\n"
            f"Transaction ID: {txn}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve",
                                     callback_data=f"dep:ok:{dep_id}"),
                InlineKeyboardButton("❌ Reject",
                                     callback_data=f"dep:no:{dep_id}"),
            ]]),
        )
        return

    if stage == "withdraw_amount":
        gross, fee, net = payout_calculation(value)
        user = get_user(uid)
        balance_type = user["mode"]
        column = "buyer_balance" if balance_type == "buyer" else "freelancer_balance"
        available = amount(user[column])
        if gross > available:
            await msg.reply_text(
                f"ব্যালেন্স যথেষ্ট নয়। আছে {taka(available)}।"
            )
            return
        context.user_data["withdraw_gross"] = str(gross)
        context.user_data["withdraw_balance"] = balance_type
        context.user_data["stage"] = "withdraw_method"
        await msg.reply_text(
            f"মোট: {taka(gross)}\n"
            f"ফি 15%: {taka(fee)}\n"
            f"আপনি পাবেন: {taka(net)}\n\n"
            "পদ্ধতি লিখুন: bKash, Nagad অথবা recharge"
        )
        return

    if stage == "withdraw_method":
        method = text.lower()
        valid = ("bkash", "nagad", "recharge", "বিকাশ", "নগদ", "মোবাইল রিচার্জ")
        if method not in valid:
            await msg.reply_text("bKash, Nagad অথবা recharge লিখুন।")
            return
        context.user_data["withdraw_method"] = method
        context.user_data["stage"] = "withdraw_account"
        await msg.reply_text(
            "আপনার bKash/Nagad নম্বর অথবা রিচার্জের মোবাইল নম্বর লিখুন।"
        )
        return

    if stage == "withdraw_account":
        account = text.strip()
        if len(account) < 5 or len(account) > 80:
            await msg.reply_text("সঠিক নম্বর/অ্যাকাউন্ট লিখুন।")
            return
        gross, fee, net = payout_calculation(
            context.user_data["withdraw_gross"]
        )
        balance_type = context.user_data["withdraw_balance"]
        column = "buyer_balance" if balance_type == "buyer" else "freelancer_balance"

        # Deduct atomically only if sufficient balance remains.
        with connect_db() as conn:
            row = conn.execute(
                f"SELECT {column} AS bal FROM users WHERE user_id=?",
                (uid,),
            ).fetchone()
            if not row or amount(row["bal"]) < gross:
                await msg.reply_text("ব্যালেন্স পরিবর্তিত হয়েছে; আবার চেষ্টা করুন।")
                stop_stage()
                return
            new_balance = amount(Decimal(row["bal"]) - gross)
            conn.execute(
                f"UPDATE users SET {column}=? WHERE user_id=?",
                (str(new_balance), uid),
            )
            cur = conn.execute(
                """INSERT INTO withdrawals
                   (user_id, balance_type, gross, fee, net, method,
                    account, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    uid, balance_type, str(gross), str(fee), str(net),
                    context.user_data["withdraw_method"], account, now(),
                ),
            )
            wid = cur.lastrowid

        audit(uid, "withdrawal_created", f"withdrawal={wid}")
        stop_stage()
        await msg.reply_text(
            "আপনার পেমেন্ট রিকুয়েস্ট সফলভাবে জমা হয়েছে। "
            "অনুগ্রহ করে অপেক্ষা করুন। আপনার পেমেন্ট রিকুয়েস্ট জমা "
            "দেওয়ার সময় থেকে সর্বোচ্চ ৪৮ ঘণ্টার মধ্যে পেমেন্ট "
            "সম্পন্ন করা হবে। ধৈর্য ধরে অপেক্ষা করার জন্য আপনাকে ধন্যবাদ। "
            "— BABLU STAR BD"
        )
        await safe_send(
            context, ADMIN_ID,
            f"💸 Withdrawal #{wid}\nUser: {uid}\n"
            f"Gross: {taka(gross)}\nFee: {taka(fee)}\n"
            f"Net: {taka(net)}\nMethod: {context.user_data.get('withdraw_method')}\n"
            f"Account: {account}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Paid",
                                     callback_data=f"wd:ok:{wid}"),
                InlineKeyboardButton("❌ Reject",
                                     callback_data=f"wd:no:{wid}"),
            ]]),
        )
        return

    if stage == "job_title":
        if len(text) > 100:
            await msg.reply_text("শিরোনাম সর্বোচ্চ 100 অক্ষরের হবে।")
            return
        context.user_data["job_title"] = text
        context.user_data["stage"] = "job_description"
        await msg.reply_text("কাজের বিবরণ লিখুন।")
        return

    if stage == "job_description":
        context.user_data["job_description"] = text[:3000]
        context.user_data["stage"] = "job_count"
        await msg.reply_text("কতটি টাস্ক থাকবে? পূর্ণ সংখ্যা লিখুন।")
        return

    if stage == "job_count":
        count = int(value)
        if count != value.to_integral_value() or count > 10000:
            await msg.reply_text("1 থেকে 10000-এর মধ্যে পূর্ণ সংখ্যা লিখুন।")
            return
        context.user_data["job_count"] = count
        context.user_data["stage"] = "job_price"
        await msg.reply_text("প্রতি টাস্কের মূল্য কত টাকা?")
        return

    if stage == "job_price":
        price = amount(value)
        count = int(context.user_data["job_count"])
        budget = amount(price * count)
        user = get_user(uid)
        available = amount(user["buyer_balance"])
        if budget > available:
            await msg.reply_text(
                f"মোট বাজেট {taka(budget)}; Buyer Balance "
                f"{taka(available)}। আগে ডিপোজিট করুন।"
            )
            stop_stage()
            return
        context.user_data["job_price"] = str(price)
        context.user_data["job_budget"] = str(budget)
        context.user_data["stage"] = "job_rules"
        await msg.reply_text(
            f"মোট বাজেট: {taka(budget)}\n"
            "প্রমাণ/নিয়ম/ডেডলাইন লিখুন।"
        )
        return

    if stage == "job_rules":
        rules = text[:3000]
        count = int(context.user_data["job_count"])
        price = amount(context.user_data["job_price"])
        budget = amount(context.user_data["job_budget"])

        with connect_db() as conn:
            row = conn.execute(
                "SELECT buyer_balance FROM users WHERE user_id=?",
                (uid,),
            ).fetchone()
            if not row or amount(row["buyer_balance"]) < budget:
                await msg.reply_text("Buyer Balance যথেষ্ট নয়।")
                stop_stage()
                return

            new_bal = amount(Decimal(row["buyer_balance"]) - budget)
            conn.execute(
                "UPDATE users SET buyer_balance=? WHERE user_id=?",
                (str(new_bal), uid),
            )
            cur = conn.execute(
                """INSERT INTO jobs
                   (buyer_id, title, description, task_count,
                    price_per_task, reserved_budget, rules,
                    status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    uid, context.user_data["job_title"],
                    context.user_data["job_description"], count,
                    str(price), str(budget), rules, now(),
                ),
            )
            job_id = cur.lastrowid

        audit(uid, "job_created", f"job={job_id},budget={budget}")
        stop_stage()
        await msg.reply_text(
            f"✅ কাজ পোস্ট হয়েছে!\nJob #{job_id}\n"
            f"টাস্ক: {count}\nপ্রতি টাস্ক: {taka(price)}\n"
            f"Escrow বাজেট: {taka(budget)}"
        )
        return

    stop_stage()
    await msg.reply_text("ধাপটি শেষ হয়েছে। মেনু থেকে আবার শুরু করুন।")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data or ""

    if data.startswith("submit:"):
        user = get_user(uid)
        if not user or user["mode"] != "freelancer" or is_blocked(uid):
            await query.message.reply_text("Freelancer Mode চালু করুন।")
            return
        job_id = int(data.split(":")[1])
        with connect_db() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=? AND status='open' "
                "AND task_count>0",
                (job_id,),
            ).fetchone()
            if not job:
                await query.message.reply_text("কাজটি আর খোলা নেই।")
                return
            if job["buyer_id"] == uid:
                await query.message.reply_text("নিজের কাজে আবেদন করা যাবে না।")
                return
            exists = conn.execute(
                "SELECT 1 FROM submissions WHERE job_id=? AND freelancer_id=?",
                (job_id, uid),
            ).fetchone()
            if exists:
                await query.message.reply_text("আপনি ইতিমধ্যে সাবমিশন দিয়েছেন।")
                return
        context.user_data["stage"] = "proof"
        context.user_data["proof_job_id"] = job_id
        await query.message.reply_text("আপনার কাজের প্রমাণ (লিংক/বিবরণ) পাঠান।")
        return

    if data == "admin:deposits" or data == "admin:withdrawals" or \
       data == "admin:submissions" or data == "admin:finance":
        if not is_admin(uid):
            await query.message.reply_text("⛔ অনুমতি নেই।")
            return
        await admin_action(query, context, data)
        return

    parts = data.split(":")
    if len(parts) == 3 and parts[0] in ("dep", "wd", "sub"):
        if not is_admin(uid):
            await query.message.reply_text("⛔ অনুমতি নেই।")
            return
        kind, decision, raw_id = parts
        if decision not in ("ok", "no", "accept", "reject"):
            return
        record_id = int(raw_id)
        await review_record(query, context, kind, decision, record_id)
        return


async def handle_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("stage") != "proof":
        return
    uid = update.effective_user.id
    job_id = context.user_data.get("proof_job_id")
    proof = update.message.text.strip() if update.message.text else ""
    if not proof:
        await update.message.reply_text("প্রমাণ হিসেবে টেক্সট বা লিংক পাঠান।")
        return

    try:
        with connect_db() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=? AND status='open' AND task_count>0",
                (job_id,),
            ).fetchone()
            if not job:
                await update.message.reply_text("কাজটি আর খোলা নেই।")
                context.user_data.pop("stage", None)
                return
            conn.execute(
                """INSERT INTO submissions
                   (job_id, freelancer_id, proof, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (job_id, uid, proof[:4000], now()),
            )
            sub = conn.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
    except sqlite3.IntegrityError:
        await update.message.reply_text("এই কাজে আপনার সাবমিশন আগে জমা হয়েছে।")
        context.user_data.pop("stage", None)
        return

    context.user_data.pop("stage", None)
    context.user_data.pop("proof_job_id", None)
    audit(uid, "submission_created", f"submission={sub}")
    await update.message.reply_text(f"✅ সাবমিশন #{sub} জমা হয়েছে।")
    await safe_send(
        context, ADMIN_ID,
        f"📨 Pending submission #{sub}\n"
        f"Job #{job_id}\nFreelancer: {uid}\nProof:\n{proof[:2500]}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"sub:accept:{sub}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"sub:reject:{sub}"),
        ]]),
    )


async def admin_action(query, context, data):
    with connect_db() as conn:
        if data == "admin:deposits":
            rows = conn.execute(
                "SELECT * FROM deposits WHERE status='pending' ORDER BY deposit_id LIMIT 20"
            ).fetchall()
            for r in rows:
                await safe_send(
                    context, ADMIN_ID,
                    f"Deposit #{r['deposit_id']} User {r['user_id']}\n"
                    f"{taka(r['amount'])} | {r['method']}\nTxn: {r['transaction_id']}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Approve", callback_data=f"dep:ok:{r['deposit_id']}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"dep:no:{r['deposit_id']}"),
                    ]]),
                )
            if not rows:
                await query.message.reply_text("Pending deposit নেই।")

        elif data == "admin:withdrawals":
            rows = conn.execute(
                "SELECT * FROM withdrawals WHERE status='pending' ORDER BY withdrawal_id LIMIT 20"
            ).fetchall()
            for r in rows:
                await safe_send(
                    context, ADMIN_ID,
                    f"Withdrawal #{r['withdrawal_id']} User {r['user_id']}\n"
                    f"Gross {taka(r['gross'])} | Fee {taka(r['fee'])}\n"
                    f"Net {taka(r['net'])} | {r['method']} | {r['account']}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Paid", callback_data=f"wd:ok:{r['withdrawal_id']}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"wd:no:{r['withdrawal_id']}"),
                    ]]),
                )
            if not rows:
                await query.message.reply_text("Pending withdrawal নেই।")

        elif data == "admin:submissions":
            rows = conn.execute(
                "SELECT * FROM submissions WHERE status='pending' ORDER BY submission_id LIMIT 20"
            ).fetchall()
            for r in rows:
                await safe_send(
                    context, ADMIN_ID,
                    f"Submission #{r['submission_id']} Job #{r['job_id']}\n"
                    f"Freelancer {r['freelancer_id']}\nProof: {r['proof'][:2000]}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Accept", callback_data=f"sub:accept:{r['submission_id']}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"sub:reject:{r['submission_id']}"),
                    ]]),
                )
            if not rows:
                await query.message.reply_text("Pending submission নেই।")

        elif data == "admin:finance":
            dep = conn.execute(
                "SELECT amount FROM deposits WHERE status='approved'"
            ).fetchall()
            wd = conn.execute(
                "SELECT gross, fee, net FROM withdrawals WHERE status='paid'"
            ).fetchall()
            await query.message.reply_text(
                "📊 Finance Summary\n"
                f"Approved deposits: {taka(sum((Decimal(r['amount']) for r in dep), Decimal('0')))}\n"
                f"Paid withdrawals: {taka(sum((Decimal(r['gross']) for r in wd), Decimal('0')))}\n"
                f"Withdrawal fees: {taka(sum((Decimal(r['fee']) for r in wd), Decimal('0')))}"
            )


async def review_record(query, context, kind, decision, record_id):
    actor = query.from_user.id
    if not is_admin(actor):
        return

    if kind == "dep":
        with connect_db() as conn:
            row = conn.execute(
                "SELECT * FROM deposits WHERE deposit_id=? AND status='pending'",
                (record_id,),
            ).fetchone()
            if not row:
                await query.message.reply_text("এই ডিপোজিট ইতিমধ্যে পর্যালোচনা হয়েছে।")
                return
            if decision == "ok":
                conn.execute(
                    "UPDATE deposits SET status='approved', reviewed_at=? WHERE deposit_id=? AND status='pending'",
                    (now(), record_id),
                )
                column = "buyer_balance" if row["balance_type"] == "buyer" else "freelancer_balance"
                bal = conn.execute(
                    f"SELECT {column} AS bal FROM users WHERE user_id=?",
                    (row["user_id"],),
                ).fetchone()
                new_bal = amount(Decimal(bal["bal"]) + Decimal(row["amount"]))
                conn.execute(
                    f"UPDATE users SET {column}=? WHERE user_id=?",
                    (str(new_bal), row["user_id"]),
                )
            else:
                conn.execute(
                    "UPDATE deposits SET status='rejected', reviewed_at=? WHERE deposit_id=? AND status='pending'",
                    (now(), record_id),
                )
        audit(actor, "deposit_review", f"id={record_id},decision={decision}")
        await safe_send(
            context, row["user_id"],
            "✅ ডিপোজিট অনুমোদিত হয়েছে।" if decision == "ok"
            else "❌ ডিপোজিট প্রত্যাখ্যান করা হয়েছে।"
        )
        await query.message.reply_text("ডিপোজিট পর্যালোচনা সম্পন্ন।")
        return

    if kind == "wd":
        with connect_db() as conn:
            row = conn.execute(
                "SELECT * FROM withdrawals WHERE withdrawal_id=? AND status='pending'",
                (record_id,),
            ).fetchone()
            if not row:
                await query.message.reply_text("এই উত্তোলন ইতিমধ্যে পর্যালোচনা হয়েছে।")
                return
            if decision == "ok":
                conn.execute(
                    "UPDATE withdrawals SET status='paid', reviewed_at=? WHERE withdrawal_id=? AND status='pending'",
                    (now(), record_id),
                )
            else:
                column = "buyer_balance" if row["balance_type"] == "buyer" else "freelancer_balance"
                bal = conn.execute(
                    f"SELECT {column} AS bal FROM users WHERE user_id=?",
                    (row["user_id"],),
                ).fetchone()
                new_bal = amount(Decimal(bal["bal"]) + Decimal(row["gross"]))
                conn.execute(
                    f"UPDATE users SET {column}=? WHERE user_id=?",
                    (str(new_bal), row["user_id"]),
                )
                conn.execute(
                    "UPDATE withdrawals SET status='rejected', reviewed_at=? WHERE withdrawal_id=? AND status='pending'",
                    (now(), record_id),
                )

            # First approved withdrawal earns the referrer's one-time bonus.
            if decision == "ok":
                user = conn.execute(
                    "SELECT referred_by, referral_paid FROM users WHERE user_id=?",
                    (row["user_id"],),
                ).fetchone()
                if user and user["referred_by"] and not user["referral_paid"]:
                    referrer = conn.execute(
                        "SELECT user_id, freelancer_balance FROM users WHERE user_id=?",
                        (user["referred_by"],),
                    ).fetchone()
                    if referrer:
                        bonus = amount(Decimal(row["gross"]) * REFERRAL_RATE)
                        new_ref_balance = amount(
                            Decimal(referrer["freelancer_balance"]) + bonus
                        )
                        conn.execute(
                            "UPDATE users SET freelancer_balance=? WHERE user_id=?",
                            (str(new_ref_balance), user["referred_by"]),
                        )
                        conn.execute(
                            "UPDATE users SET referral_paid=1 WHERE user_id=?",
                            (row["user_id"],),
                        )
                        audit(actor, "referral_bonus",
                              f"referrer={user['referred_by']},bonus={bonus}")
        audit(actor, "withdrawal_review", f"id={record_id},decision={decision}")
        await safe_send(
            context, row["user_id"],
            "✅ আপনার পেমেন্ট সম্পন্ন হয়েছে।" if decision == "ok"
            else f"❌ উত্তোলন প্রত্যাখ্যাত; {taka(row['gross'])} ব্যালেন্সে ফেরত দেওয়া হয়েছে।"
        )
        await query.message.reply_text("উত্তোলন পর্যালোচনা সম্পন্ন।")
        return

    if kind == "sub":
        with connect_db() as conn:
            row = conn.execute(
                """SELECT s.*, j.buyer_id, j.price_per_task,
                          j.reserved_budget, j.task_count, j.status AS job_status
                   FROM submissions s JOIN jobs j ON j.job_id=s.job_id
                   WHERE s.submission_id=? AND s.status='pending'""",
                (record_id,),
            ).fetchone()
            if not row:
                await query.message.reply_text("সাবমিশনটি ইতিমধ্যে পর্যালোচনা হয়েছে।")
                return

            if decision == "accept":
                if row["job_status"] != "open" or row["task_count"] <= 0:
                    await query.message.reply_text("কাজটি আর খোলা নেই।")
                    return
                price = amount(row["price_per_task"])
                reserved = amount(row["reserved_budget"])
                if reserved < price:
                    await query.message.reply_text("Escrow বাজেট যথেষ্ট নয়।")
                    return

                conn.execute(
                    "UPDATE submissions SET status='accepted', reviewed_at=? WHERE submission_id=? AND status='pending'",
                    (now(), record_id),
                )
                conn.execute(
                    """UPDATE jobs SET task_count=task_count-1,
                       reserved_budget=?, status=?
                       WHERE job_id=?""",
                    (
                        str(amount(reserved - price)),
                        "closed" if row["task_count"] == 1 else "open",
                        row["job_id"],
                    ),
                )
                f = conn.execute(
                    "SELECT freelancer_balance FROM users WHERE user_id=?",
                    (row["freelancer_id"],),
                ).fetchone()
                new_bal = amount(Decimal(f["freelancer_balance"]) + price)
                conn.execute(
                    "UPDATE users SET freelancer_balance=? WHERE user_id=?",
                    (str(new_bal), row["freelancer_id"]),
                )
            else:
                conn.execute(
                    "UPDATE submissions SET status='rejected', reviewed_at=? WHERE submission_id=? AND status='pending'",
                    (now(), record_id),
                )

        audit(actor, "submission_review", f"id={record_id},decision={decision}")
        await safe_send(
            context, row["freelancer_id"],
            f"✅ সাবমিশন অনুমোদিত; {taka(row['price_per_task'])} যোগ হয়েছে।"
            if decision == "accept"
            else "❌ আপনার সাবমিশন প্রত্যাখ্যান করা হয়েছে।"
        )
        await safe_send(
            context, row["buyer_id"],
            f"আপনার Job #{row['job_id']} সাবমিশন #{record_id} "
            f"{'অনুমোদিত' if decision == 'accept' else 'প্রত্যাখ্যাত'} হয়েছে।"
        )
        await query.message.reply_text("সাবমিশন পর্যালোচনা সম্পন্ন।")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("stage", None)
    context.user_data.pop("proof_job_id", None)
    await update.message.reply_text(
        "চলমান ধাপ বাতিল হয়েছে। /start দিয়ে আবার শুরু করুন।"
    )


def main():
    init_db()
    migrate_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text,
    ))
    # Proof handler must run before the general text handler.
    # This group ensures the proof stage is handled first.
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_proof,
    ), group=1)

    logger.info("BABLU STAR BD bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
