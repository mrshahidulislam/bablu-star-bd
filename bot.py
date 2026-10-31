import os
import sqlite3
import logging
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone

from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN = int(os.getenv("ADMIN_ID", "0") or "0")

_db_env = os.getenv("DB_PATH", "").strip()
DB = str(Path(_db_env).expanduser()) if _db_env else str(
    Path("data") / "bablu_star_bd.sqlite3"
)
Path(DB).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

if not _db_env:
    legacy_db = Path("bablu_star_bd.sqlite3")
    persistent_db = Path(DB)
    if legacy_db.is_file() and not persistent_db.exists():
        src = sqlite3.connect(str(legacy_db), timeout=30)
        try:
            dst = sqlite3.connect(str(persistent_db), timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

FEE = Decimal("0.15")
REFERRAL = Decimal("0.10")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("bablu-star-bd")

CATS = [
    ("📘 Facebook", 2),
    ("▶️ YouTube", 2),
    ("📸 Instagram", 2),
    ("🎵 TikTok", 2),
    ("𝕏 X (Twitter)", 2),
    ("👻 Snapchat", 2),
    ("✈️ Telegram", 2),
    ("💼 LinkedIn", 2),
]

BUYER = [
    ["➕ কাজ পোস্ট", "📋 আমার কাজ"],
    ["💰 বায়ার ব্যালেন্স", "➕ ডিপোজিট"],
    ["📤 মোড বদল", "🏠 মেইন মেনু"],
    ["🔙 Back / ❌ Cancel"]
]

WORKER = [
    ["🔎 কাজ দেখুন", "📨 আমার সাবমিশন"],
    ["💰 ফ্রিল্যান্সার ব্যালেন্স", "💸 উইথড্র"],
    ["👥 রেফার লিংক", "📤 মোড বদল"],
    ["🏠 মেইন মেনু", "🔙 Back / ❌ Cancel"]
]

MAIN = [
    ["🛒 Buyer Mode", "🧑‍💻 Freelancer Mode"],
    ["💳 পেমেন্ট তথ্য", "📜 নিয়মাবলি"],
    ["🆘 সাহায্য", "🔙 Back / ❌ Cancel"]
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def m(value):
    d = Decimal(str(value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"


def amount(value):
    try:
        x = Decimal(
            str(value).replace(",", "").replace("৳", "").strip()
        )
        if x.is_finite() and x > 0:
            return x.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
    except (InvalidOperation, ValueError):
        pass
    return None


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            buyer TEXT DEFAULT '0',
            worker TEXT DEFAULT '0',
            referrer INTEGER,
            ref_paid INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER,
            cat INTEGER,
            title TEXT,
            description TEXT,
            count INTEGER,
            price TEXT,
            reserved TEXT,
            rules TEXT,
            status TEXT DEFAULT 'open',
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            worker_id INTEGER,
            proof TEXT,
            file_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created TEXT,
            reviewed TEXT,
            UNIQUE(job_id,worker_id)
        );

        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount TEXT,
            method TEXT,
            txn TEXT UNIQUE,
            status TEXT DEFAULT 'pending',
            created TEXT,
            reviewed TEXT
        );

        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            gross TEXT,
            fee TEXT,
            net TEXT,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created TEXT,
            reviewed TEXT
        );

        CREATE TABLE IF NOT EXISTS ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            wallet TEXT,
            amount TEXT,
            kind TEXT,
            note TEXT,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor INTEGER,
            action TEXT,
            details TEXT,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        def add_columns(table, columns):
            existing = {
                row["name"]
                for row in c.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            for name, declaration in columns:
                if name not in existing:
                    c.execute(
                        f"ALTER TABLE {table} ADD COLUMN "
                        f"{name} {declaration}"
                    )

        add_columns("users", [
            ("ban_reason", "TEXT DEFAULT ''"),
            ("buyer", "TEXT DEFAULT '0'"),
            ("worker", "TEXT DEFAULT '0'"),
            ("device_info", "TEXT DEFAULT ''"),
            ("referrer", "INTEGER"),
            ("ref_paid", "INTEGER DEFAULT 0"),
            ("banned", "INTEGER DEFAULT 0"),
        ])

        add_columns("jobs", [
            ("is_admin_job", "INTEGER DEFAULT 0"),
            ("reserved", "TEXT DEFAULT '0'"),
            ("status", "TEXT DEFAULT 'open'"),
        ])

        add_columns("submissions", [
            ("file_id", "TEXT DEFAULT ''"),
            ("status", "TEXT DEFAULT 'pending'"),
            ("reviewed", "TEXT"),
        ])

        add_columns("deposits", [
            ("status", "TEXT DEFAULT 'pending'"),
            ("reviewed", "TEXT"),
        ])

        add_columns("withdrawals", [
            ("status", "TEXT DEFAULT 'pending'"),
            ("reviewed", "TEXT"),
        ])


DEFAULT_TEXTS = {
    "welcome": (
        "🌟 BABLU STAR BD-তে স্বাগতম! 🌟\n\n"
        "💼 আপনার কাজ, আপনার উপার্জন!\n"
        "🛒 Buyer Mode — নিজের কাজ পোস্ট করুন\n"
        "🧑‍💻 Freelancer Mode — কাজ করুন ও আয় করুন\n\n"
        "👇 শুরু করতে নিচের একটি মোড বেছে নিন।"
    ),
    "support_email": "mrshahidulislam8989@gmail.com",
    "rules": (
        "• কাজের দাম ক্যাটাগরি মিনিমামের কম নয়।\n"
        "• ভুয়া অ্যাকাউন্ট, প্রতারণা, স্প্যাম নিষিদ্ধ।\n"
        "• বাজেট বায়ার ব্যালেন্স থেকে সংরক্ষিত হবে।\n"
        "• প্রুফ অ্যাডমিন অনুমোদন করলে ফ্রিল্যান্সার টাকা পাবেন।\n"
        "• ডিপোজিট/উইথড্র ম্যানুয়ালি যাচাই হয়।"
    ),
    "payment_info": (
        "bKash/Nagad ডিপোজিট Txn ID দিয়ে জমা দিন। "
        "অ্যাডমিন যাচাইয়ের পর ব্যালেন্স যোগ হবে।"
    ),
    "banned_text": "⛔ আপনার অ্যাকাউন্ট সীমাবদ্ধ করা হয়েছে। কারণ: {reason}",
    "deposit_min": "50",
    "withdraw_min_usd": "1",
    "recharge_min_usd": "0.50",
    "withdraw_min": "100",
    "recharge_min": "50",
    "withdraw_fee": "15",
    "recharge_fee": "15",
    "post_fee": "5",
    "referral_bonus": "10",
    "usd_rate": "100",
    "bkash_number": "পরিবর্তন করা হয়নি",
    "nagad_number": "পরিবর্তন করা হয়নি",
    "support_url": "https://t.me/",
}


def get_setting(key):
    with conn() as c:
        r = c.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
    return r["value"] if r else DEFAULT_TEXTS.get(key, "")


def set_setting(key, value):
    with conn() as c:
        c.execute("""
            INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))


def is_admin(uid):
    return bool(ADMIN and uid == ADMIN)


def user_row(user):
    if not user:
        return
    with conn() as c:
        r = c.execute(
            "SELECT id FROM users WHERE id=?", (user.id,)
        ).fetchone()
        if not r:
            c.execute("""
                INSERT INTO users(id,username,first_name,created)
                VALUES(?,?,?,?)
            """, (
                user.id, user.username or "",
                user.first_name or "", now()
            ))
        else:
            c.execute("""
                UPDATE users SET username=?,first_name=? WHERE id=?
            """, (
                user.username or "", user.first_name or "", user.id
            ))


def kb(mode):
    return ReplyKeyboardMarkup(
        BUYER if mode == "buyer" else
        WORKER if mode == "worker" else MAIN,
        resize_keyboard=True
    )


async def menu(update, ctx, msg="মেনু থেকে অপশন বেছে নিন।"):
    for key in ("stage", "form", "proof_job"):
        ctx.user_data.pop(key, None)

    target = update.effective_message
    if not target and update.callback_query:
        target = update.callback_query.message

    await target.reply_text(
        msg, reply_markup=kb(ctx.user_data.get("mode", "main"))
    )


async def admin_msg(ctx, message, kind=None, item=None):
    if not ADMIN:
        return

    markup = None
    if kind and item is not None:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"review:{kind}:yes:{item}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"review:{kind}:no:{item}"
            )
        ]])

    try:
        await ctx.bot.send_message(
            ADMIN, message, reply_markup=markup
        )
    except Exception:
        log.exception("Admin notification failed")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_row(user)

    with conn() as c:
        banned = c.execute(
            "SELECT banned,ban_reason FROM users WHERE id=?",
            (user.id,)
        ).fetchone()

    if banned and banned["banned"] and not is_admin(user.id):
        reason = banned["ban_reason"] or "অ্যাডমিনের সিদ্ধান্ত"
        await update.effective_message.reply_text(
            get_setting("banned_text").format(reason=reason),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🆘 Support", url=get_setting("support_url")
                )
            ]])
        )
        return

    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref = int(ctx.args[0][4:])
            if ref != user.id:
                with conn() as c:
                    r = c.execute(
                        "SELECT referrer FROM users WHERE id=?",
                        (user.id,)
                    ).fetchone()
                    exists = c.execute(
                        "SELECT 1 FROM users WHERE id=?", (ref,)
                    ).fetchone()
                    if r and r["referrer"] is None and exists:
                        c.execute(
                            "UPDATE users SET referrer=? WHERE id=?",
                            (ref, user.id)
                        )
        except ValueError:
            pass

    ctx.user_data["mode"] = "main"
    await menu(update, ctx, get_setting("welcome"))


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await menu(update, ctx, "চলমান কাজ বাতিল হয়েছে।")


async def text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_row(user)
    t = (update.effective_message.text or "").strip()

    with conn() as c:
        r = c.execute(
            "SELECT banned,ban_reason FROM users WHERE id=?",
            (user.id,)
        ).fetchone()

    if r and r["banned"] and not is_admin(user.id):
        reason = r["ban_reason"] or "অ্যাডমিনের সিদ্ধান্ত"
        await update.effective_message.reply_text(
            get_setting("banned_text").format(reason=reason)
        )
        return

    if t in ("🔙 Back / ❌ Cancel", "🔙 Back", "❌ Cancel", "/cancel"):
        await cancel(update, ctx)
        return

    if is_admin(user.id) and ctx.user_data.pop(
        "admin_user_search", False
    ):
        term = t.lstrip("@").strip()
        with conn() as c:
            if term.isdigit():
                rows = c.execute(
                    "SELECT id,username,first_name,banned FROM users WHERE id=?",
                    (int(term),)
                ).fetchall()
            else:
                rows = c.execute("""
                    SELECT id,username,first_name,banned
                    FROM users WHERE username LIKE ?
                    ORDER BY id DESC LIMIT 20
                """, ("%" + term + "%",)).fetchall()

        if not rows:
            await update.effective_message.reply_text(
                "কোনো ইউজার পাওয়া যায়নি।"
            )
            return

        buttons = [[InlineKeyboardButton(
            f"{r['id']} | @{r['username'] or '—'} | "
            f"{'BANNED' if r['banned'] else 'ACTIVE'}",
            callback_data=f"adm:user:{r['id']}"
        )] for r in rows]

        await update.effective_message.reply_text(
            "🔎 Search Results:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if is_admin(user.id) and ctx.user_data.get("ban_target"):
        target = ctx.user_data.pop("ban_target")
        with conn() as c:
            c.execute(
                "UPDATE users SET banned=1,ban_reason=? WHERE id=?",
                (t[:500], target)
            )
        await update.effective_message.reply_text(
            f"🚫 User {target} Ban করা হয়েছে।"
        )
        try:
            await ctx.bot.send_message(
                target, "⛔ আপনার অ্যাকাউন্ট সীমাবদ্ধ করা হয়েছে।\n"
                "কারণ: " + t[:500]
            )
        except Exception:
            pass
        return

    if is_admin(user.id) and ctx.user_data.get("admin_edit_key"):
        key = ctx.user_data.pop("admin_edit_key")
        if len(t) > 4000:
            await update.effective_message.reply_text(
                "সর্বোচ্চ ৪০০০ অক্ষর দিন।"
            )
            ctx.user_data["admin_edit_key"] = key
            return

        set_setting(key, t)
        with conn() as c:
            c.execute("""
                INSERT INTO audit(actor,action,details,created)
                VALUES(?,?,?,?)
            """, (user.id, "setting_edit", key, now()))

        await update.effective_message.reply_text(
            f"✅ {key} পরিবর্তন সংরক্ষিত হয়েছে।"
        )
        return

    stage = ctx.user_data.get("stage")
    if stage:
        await stage_input(update, ctx, stage, t)
        return

    mode = ctx.user_data.get("mode", "main")
    msg = update.effective_message

    if t == "🛒 Buyer Mode":
        ctx.user_data["mode"] = "buyer"
        await menu(update, ctx, "Buyer Mode চালু হয়েছে।")

    elif t == "🧑‍💻 Freelancer Mode":
        ctx.user_data["mode"] = "worker"
        await menu(update, ctx, "Freelancer Mode চালু হয়েছে।")

    elif t == "📤 মোড বদল":
        ctx.user_data["mode"] = "worker" if mode == "buyer" else "buyer"
        await menu(update, ctx, "মোড পরিবর্তন হয়েছে।")

    elif t == "🏠 মেইন মেনু":
        ctx.user_data["mode"] = "main"
        await menu(update, ctx, "মেইন মেনু।")

    elif t == "➕ কাজ পোস্ট" and mode == "buyer":
        rows = [[InlineKeyboardButton(
            f"{name} — মিনিমাম ৳{price}",
            callback_data=f"cat:{i}"
        )] for i, (name, price) in enumerate(CATS)]

        await msg.reply_text(
            "শুধুমাত্র এই ৮টি প্ল্যাটফর্ম থেকে একটি বেছে নিন।",
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif t == "📋 আমার কাজ" and mode == "buyer":
        with conn() as c:
            rows = c.execute("""
                SELECT * FROM jobs WHERE buyer_id=?
                ORDER BY id DESC LIMIT 20
            """, (user.id,)).fetchall()

        await msg.reply_text("\n".join([
            f"#{r['id']} {r['title']} | বাকি {r['count']} | "
            f"৳{m(r['price'])}/কাজ | {r['status']}"
            for r in rows
        ]) or "আপনার কোনো কাজ নেই।")

    elif t == "💰 বায়ার ব্যালেন্স" and mode == "buyer":
        await balance(update, user.id, "buyer")

    elif t == "💰 ফ্রিল্যান্সার ব্যালেন্স" and mode == "worker":
        await balance(update, user.id, "worker")

    elif t == "➕ ডিপোজিট" and mode == "buyer":
        ctx.user_data["stage"] = "dep_amount"
        await msg.reply_text(
            f"💳 ডিপোজিটের পরিমাণ লিখুন "
            f"(সর্বনিম্ন ৳{get_setting('deposit_min')})।"
        )

    elif t == "🔎 কাজ দেখুন" and mode == "worker":
        with conn() as c:
            rows = c.execute("""
                SELECT * FROM jobs
                WHERE status='open' AND count>0
                ORDER BY id DESC LIMIT 10
            """).fetchall()

        if not rows:
            await msg.reply_text("এখন কোনো খোলা কাজ নেই।")

        for r in rows:
            ci = int(r["cat"] or 0)
            cat = CATS[ci][0] if 0 <= ci < len(CATS) else "অজানা"
            details = (
                f"কাজ #{r['id']} — {cat}\n"
                f"{r['title']}\n{r['description']}\n"
                f"প্রতি কাজ ৳{m(r['price'])} | বাকি {r['count']}\n"
                f"নিয়ম/প্রুফ: {r['rules']}"
            )
            await msg.reply_text(
                details,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "📨 প্রুফ জমা দিন",
                        callback_data=f"submit:{r['id']}"
                    )
                ]])
            )

    elif t == "📨 আমার সাবমিশন" and mode == "worker":
        with conn() as c:
            rows = c.execute("""
                SELECT s.id,s.job_id,s.status,j.title
                FROM submissions s
                JOIN jobs j ON j.id=s.job_id
                WHERE s.worker_id=?
                ORDER BY s.id DESC LIMIT 20
            """, (user.id,)).fetchall()

        await msg.reply_text("\n".join([
            f"#{r['id']} কাজ #{r['job_id']} "
            f"{r['title']} — {r['status']}"
            for r in rows
        ]) or "কোনো সাবমিশন নেই।")

    elif t == "💸 উইথড্র" and mode == "worker":
        ctx.user_data.pop("stage", None)
        ctx.user_data.pop("form", None)
        await msg.reply_text(
            "💸 উইথড্র / পেমেন্ট পদ্ধতি বেছে নিন:\n\n"
            "১) বিকাশ — সর্বনিম্ন ৳১০০, ফি ১৫%\n"
            "২) নগদ — সর্বনিম্ন ৳১০০, ফি ১৫%\n"
            "৩) মোবাইল রিচার্জ — সর্বনিম্ন ৳৫০, ফি ১৫%",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "📱 বিকাশ — মিনিমাম ৳১০০",
                    callback_data="wdmethod:bkash"
                )],
                [InlineKeyboardButton(
                    "💚 নগদ — মিনিমাম ৳১০০",
                    callback_data="wdmethod:nagad"
                )],
                [InlineKeyboardButton(
                    "📞 মোবাইল রিচার্জ — মিনিমাম ৳৫০",
                    callback_data="wdmethod:recharge"
                )]
            ])
        )

    elif t == "👥 রেফার লিংক" and mode == "worker":
        me = await ctx.bot.get_me()
        await msg.reply_text(
            f"https://t.me/{me.username}?start=ref_{user.id}\n"
            "রেফার করা ব্যক্তির প্রথম অনুমোদিত উইথড্রয়ের "
            "মোট অঙ্কের ১০% একবার বোনাস।"
        )

    elif t == "💳 পেমেন্ট তথ্য":
        await msg.reply_text(get_setting("payment_info"))

    elif t == "📜 নিয়মাবলি":
        await msg.reply_text(get_setting("rules"))

    elif t == "🆘 সাহায্য":
        await msg.reply_text(
            "📩 BABLU STAR BD — Support Center\n\n"
            "✉️ Support Email:\n"
            f"{get_setting('support_email')}\n\n"
            f"🆔 আপনার Telegram ID: {user.id}\n\n"
            "সমস্যার বিস্তারিত লিখে ইমেইল পাঠান।"
        )

    else:
        await msg.reply_text("মেনুর বাটন ব্যবহার করুন।")


async def balance(update, uid, wallet):
    with conn() as c:
        row = c.execute(
            f"SELECT {wallet} FROM users WHERE id=?", (uid,)
        ).fetchone()

    value = row[wallet] if row else "0"
    await update.effective_message.reply_text(
        f"আপনার ব্যালেন্স: ৳{m(value or 0)}"
    )


async def stage_input(update, ctx, stage, t):
    uid = update.effective_user.id
    msg = update.effective_message

    if stage == "job_title":
        if not 3 <= len(t) <= 100:
            await msg.reply_text("শিরোনাম ৩–১০০ অক্ষরের দিন।")
            return
        ctx.user_data["form"]["title"] = t
        ctx.user_data["stage"] = "job_desc"
        await msg.reply_text("কাজের বিস্তারিত লিখুন (১০–২০০০ অক্ষর)।")

    elif stage == "job_desc":
        if not 10 <= len(t) <= 2000:
            await msg.reply_text("বিবরণ ১০–২০০০ অক্ষরের দিন।")
            return
        ctx.user_data["form"]["desc"] = t
        ctx.user_data["stage"] = "job_count"
        await msg.reply_text("কতজন কাজ করবে? পূর্ণসংখ্যা দিন।")

    elif stage == "job_count":
        try:
            count = int(t)
            if not 1 <= count <= 100000:
                raise ValueError
        except ValueError:
            await msg.reply_text("১–১০০০০০-এর মধ্যে পূর্ণসংখ্যা দিন।")
            return

        ctx.user_data["form"]["count"] = count
        ci = ctx.user_data["form"]["cat"]
        name, minimum = CATS[ci]
        ctx.user_data["stage"] = "job_price"
        await msg.reply_text(
            f"{name} ক্যাটাগরির সর্বনিম্ন ৳{minimum}; "
            f"প্রতি কাজের দাম ৳{minimum} বা তার বেশি লিখুন।"
        )

    elif stage == "job_price":
        price = amount(t)
        form = ctx.user_data["form"]
        minimum = CATS[form["cat"]][1]

        if price is None or price < minimum:
            await msg.reply_text(
                f"প্রতি কাজ কমপক্ষে ৳{minimum} হতে হবে।"
            )
            return

        form["price"] = str(price)
        ctx.user_data["stage"] = "job_rules"
        await msg.reply_text(
            "কাজের নিয়ম এবং কী প্রুফ দিতে হবে লিখুন।"
        )

    elif stage == "job_rules":
        if not 3 <= len(t) <= 2000:
            await msg.reply_text("৩–২০০০ অক্ষরের নিয়ম লিখুন।")
            return

        form = ctx.user_data["form"]
        form["rules"] = t
        total = Decimal(form["price"]) * form["count"]

        with conn() as c:
            row = c.execute(
                "SELECT buyer FROM users WHERE id=?", (uid,)
            ).fetchone()

        buyer_balance = Decimal(str(row["buyer"] or "0"))
        if not is_admin(uid) and buyer_balance < total:
            await msg.reply_text(
                f"ব্যালেন্স কম। মোট বাজেট ৳{m(total)}, "
                f"বর্তমান ৳{m(buyer_balance)}। আগে ডিপোজিট করুন।"
            )
            ctx.user_data.pop("stage", None)
            ctx.user_data.pop("form", None)
            return

        ctx.user_data["stage"] = "job_confirm"
        await msg.reply_text(
            f"প্ল্যাটফর্ম: {CATS[form['cat']][0]}\n"
            f"শিরোনাম: {form['title']}\n"
            f"বিবরণ: {form['desc']}\n"
            f"কাজ: {form['count']}\n"
            f"প্রতি কাজ: ৳{m(form['price'])}\n"
            f"মোট বাজেট: ৳{m(total)}\n"
            f"নিয়ম: {t}\n\nপোস্ট করবেন?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ পোস্ট", callback_data="job:yes"),
                InlineKeyboardButton("❌ বাতিল", callback_data="job:no")
            ]])
        )

    elif stage == "dep_amount":
        value = amount(t)
        minimum = Decimal(get_setting("deposit_min") or "50")
        if value is None or value < minimum:
            await msg.reply_text(f"সর্বনিম্ন ৳{m(minimum)} লিখুন।")
            return

        ctx.user_data["form"] = {"amount": str(value)}
        ctx.user_data["stage"] = "dep_method"
        await msg.reply_text(
            "পেমেন্ট পদ্ধতি নির্বাচন করুন:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📲 bKash", callback_data="dep_method:bkash"),
                InlineKeyboardButton("💚 Nagad", callback_data="dep_method:nagad")
            ]])
        )

    elif stage == "dep_method":
        await msg.reply_text("উপরের bKash অথবা Nagad বাটন নির্বাচন করুন।")

    elif stage == "dep_txn":
        if not 4 <= len(t) <= 100:
            await msg.reply_text("সঠিক Transaction ID লিখুন।")
            return

        form = ctx.user_data["form"]
        try:
            with conn() as c:
                cur = c.execute("""
                    INSERT INTO deposits(user_id,amount,method,txn,created)
                    VALUES(?,?,?,?,?)
                """, (uid, form["amount"], form["method"], t, now()))
                did = cur.lastrowid
        except sqlite3.IntegrityError:
            await msg.reply_text("এই Txn ID আগে জমা হয়েছে।")
            return

        ctx.user_data.pop("stage", None)
        ctx.user_data.pop("form", None)
        await msg.reply_text(
            "আপনার পেমেন্ট রিকুয়েস্ট সফলভাবে জমা হয়েছে। "
            "অ্যাডমিন যাচাই করার পর ব্যালেন্স যোগ হবে।"
        )
        await admin_msg(
            ctx,
            f"Deposit #{did}\nUser: {uid}\n"
            f"Amount: ৳{form['amount']}\n"
            f"Method: {form['method']}\nTxn: {t}",
            "deposit", did
        )

    elif stage == "wd_gross":
        gross = amount(t)
        method = ctx.user_data.get("wd_method", "bkash")
        minimum = Decimal("50") if method == "recharge" else Decimal("100")

        if gross is None or gross < minimum:
            await msg.reply_text(f"সর্বনিম্ন ৳{m(minimum)}। আবার লিখুন:")
            return

        fee_key = "recharge_fee" if method == "recharge" else "withdraw_fee"
        fee_pct = Decimal(get_setting(fee_key) or "15")
        if fee_pct < 0 or fee_pct >= 100:
            await msg.reply_text("অ্যাডমিনের ফি সেটিংস সঠিক নয়।")
            return

        fee = (gross * fee_pct / 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        net = gross - fee

        with conn() as c:
            row = c.execute(
                "SELECT worker FROM users WHERE id=?", (uid,)
            ).fetchone()

        worker_balance = Decimal(str(row["worker"] or "0"))
        if worker_balance < gross:
            await msg.reply_text(
                f"ব্যালেন্স কম। প্রয়োজন ৳{m(gross)}, "
                f"আপনার ব্যালেন্স ৳{m(worker_balance)}।"
            )
            return

        ctx.user_data["form"] = {
            "gross": str(gross),
            "fee": str(fee),
            "net": str(net),
            "method": method
        }
        ctx.user_data["stage"] = "wd_account"

        label = {
            "bkash": "বিকাশ নম্বর",
            "nagad": "নগদ নম্বর",
            "recharge": "যে মোবাইল নম্বরে রিচার্জ চান"
        }[method]

        await msg.reply_text(
            f"🧾 আপনার উইথড্র হিসাব\n"
            f"মোট টাকা: ৳{m(gross)}\n"
            f"ফি ({m(fee_pct)}%): ৳{m(fee)}\n"
            f"আপনি পাবেন: ৳{m(net)}\n\n"
            f"এখন {label} লিখুন।"
        )

    elif stage == "wd_account":
        if len(t) < 8 or len(t) > 40:
            await msg.reply_text("সঠিক নম্বর লিখুন।")
            return

        form = ctx.user_data["form"]
        gross = Decimal(form["gross"])
        wid = None

        try:
            with conn() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute(
                    "SELECT worker FROM users WHERE id=?", (uid,)
                ).fetchone()
                bal = Decimal(str(row["worker"] or "0"))

                if bal < gross:
                    raise ValueError("ব্যালেন্স যথেষ্ট নেই।")

                c.execute(
                    "UPDATE users SET worker=? WHERE id=?",
                    (str(bal - gross), uid)
                )
                cur = c.execute("""
                    INSERT INTO withdrawals(
                        user_id,gross,fee,net,method,account,created
                    ) VALUES(?,?,?,?,?,?,?)
                """, (
                    uid, form["gross"], form["fee"], form["net"],
                    form["method"], t, now()
                ))
                wid = cur.lastrowid

                c.execute("""
                    INSERT INTO ledger(user_id,wallet,amount,kind,note,created)
                    VALUES(?,?,?,?,?,?)
                """, (
                    uid, "worker", str(-gross), "withdraw_hold",
                    f"Withdrawal #{wid}", now()
                ))

        except ValueError as exc:
            await msg.reply_text(str(exc))
            return

        ctx.user_data.pop("stage", None)
        ctx.user_data.pop("form", None)

        await msg.reply_text(
            "আপনার পেমেন্ট রিকুয়েস্ট জমা হয়েছে। "
            "অ্যাডমিন যাচাই করার পর পেমেন্ট সম্পন্ন হবে।"
        )
        await admin_msg(
            ctx,
            f"Withdrawal #{wid}\nUser: {uid}\n"
            f"Gross: ৳{form['gross']}\nFee: ৳{form['fee']}\n"
            f"Net: ৳{form['net']}\nMethod: {form['method']}\n"
            f"Account: {t}",
            "withdraw", wid
        )

    elif stage == "proof":
        await save_proof(
            update, ctx, ctx.user_data.get("proof_job"), t, ""
        )


async def save_proof(update, ctx, job_id, proof, file_id):
    uid = update.effective_user.id

    with conn() as c:
        job = c.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,)
        ).fetchone()

        if not job or job["status"] != "open" or job["count"] < 1:
            await update.effective_message.reply_text(
                "কাজটি আর খোলা নেই।"
            )
            return

        if job["buyer_id"] == uid:
            await update.effective_message.reply_text(
                "নিজের কাজের প্রুফ দিতে পারবেন না।"
            )
            return

        try:
            cur = c.execute("""
                INSERT INTO submissions(
                    job_id,worker_id,proof,file_id,created
                ) VALUES(?,?,?,?,?)
            """, (job_id, uid, proof, file_id, now()))
            sid = cur.lastrowid
        except sqlite3.IntegrityError:
            await update.effective_message.reply_text(
                "এই কাজের প্রুফ আগে জমা দিয়েছেন।"
            )
            return

    ctx.user_data.pop("stage", None)
    ctx.user_data.pop("proof_job", None)

    await update.effective_message.reply_text(
        f"প্রুফ জমা হয়েছে। সাবমিশন #{sid}; অ্যাডমিন যাচাই করবেন।"
    )

    await admin_msg(
        ctx,
        f"Submission #{sid}\nJob #{job_id}: {job['title']}\n"
        f"Worker: {uid}\nProof: {proof or '[photo]'}",
        "submission", sid
    )

    if file_id and ADMIN:
        try:
            await ctx.bot.send_photo(
                ADMIN, file_id, caption=f"প্রুফ Submission #{sid}"
            )
        except Exception:
            log.exception("Could not forward proof photo")


async def photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("stage") != "proof":
        await update.effective_message.reply_text(
            "আগে কোনো কাজের 'প্রুফ জমা দিন' বাটন চাপুন।"
        )
        return

    await save_proof(
        update, ctx, ctx.user_data.get("proof_job"),
        update.effective_message.caption or "Screenshot proof",
        update.effective_message.photo[-1].file_id
    )


async def review(q, ctx, kind, item, yes):
    if not is_admin(q.from_user.id):
        await q.message.reply_text("শুধু অ্যাডমিন পারবেন।")
        return

    target = None
    user_message = ""
    decision = "yes" if yes else "no"

    try:
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")

            if kind == "job":
                row = c.execute(
                    "SELECT * FROM jobs WHERE id=?", (item,)
                ).fetchone()

                if not row or row["status"] != "pending_admin":
                    raise ValueError("কাজটি আর Pending নেই।")

                if yes:
                    cur = c.execute("""
                        UPDATE jobs SET status='open'
                        WHERE id=? AND status='pending_admin'
                    """, (item,))
                    user_message = (
                        f"আপনার Job #{item} অ্যাডমিন অনুমোদন করেছেন। "
                        "এখন ফ্রিল্যান্সাররা কাজটি দেখতে পারবেন।"
                    )
                else:
                    cur = c.execute("""
                        UPDATE jobs SET status='rejected',reserved='0'
                        WHERE id=? AND status='pending_admin'
                    """, (item,))

                    if not row["is_admin_job"]:
                        buyer = c.execute(
                            "SELECT buyer FROM users WHERE id=?",
                            (row["buyer_id"],)
                        ).fetchone()

                        refund = Decimal(str(row["reserved"] or "0"))
                        if buyer:
                            new_balance = (
                                Decimal(str(buyer["buyer"] or "0")) + refund
                            )
                            c.execute(
                                "UPDATE users SET buyer=? WHERE id=?",
                                (str(new_balance), row["buyer_id"])
                            )
                            c.execute("""
                                INSERT INTO ledger(
                                    user_id,wallet,amount,kind,note,created
                                ) VALUES(?,?,?,?,?,?)
                            """, (
                                row["buyer_id"], "buyer", str(refund),
                                "job_refund", f"Rejected Job #{item}", now()
                            ))

                    user_message = (
                        f"আপনার Job #{item} বাতিল হয়েছে। "
                        "সংরক্ষিত বাজেট ফেরত দেওয়া হয়েছে।"
                    )

                if cur.rowcount != 1:
                    raise ValueError("কাজটি অন্য কেউ ইতিমধ্যে প্রসেস করেছেন।")

                target = row["buyer_id"]

            elif kind == "deposit":
                row = c.execute(
                    "SELECT * FROM deposits WHERE id=?", (item,)
                ).fetchone()

                if not row or row["status"] != "pending":
                    raise ValueError("ডিপোজিটটি ইতিমধ্যে প্রসেস করা হয়েছে।")

                cur = c.execute("""
                    UPDATE deposits SET status=?,reviewed=?
                    WHERE id=? AND status='pending'
                """, ("approved" if yes else "rejected", now(), item))

                if cur.rowcount != 1:
                    raise ValueError("ডিপোজিটটি ইতিমধ্যে প্রসেস করা হয়েছে।")

                if yes:
                    user = c.execute(
                        "SELECT buyer FROM users WHERE id=?",
                        (row["user_id"],)
                    ).fetchone()

                    if not user:
                        raise ValueError("ডিপোজিটের ইউজার পাওয়া যায়নি।")

                    new_balance = (
                        Decimal(str(user["buyer"] or "0"))
                        + Decimal(str(row["amount"]))
                    )
                    c.execute(
                        "UPDATE users SET buyer=? WHERE id=?",
                        (str(new_balance), row["user_id"])
                    )
                    c.execute("""
                        INSERT INTO ledger(
                            user_id,wallet,amount,kind,note,created
                        ) VALUES(?,?,?,?,?,?)
                    """, (
                        row["user_id"], "buyer", row["amount"],
                        "deposit", f"Deposit #{item}", now()
                    ))

                target = row["user_id"]
                user_message = (
                    f"ডিপোজিট #{item} "
                    f"{'অনুমোদিত' if yes else 'বাতিল'} হয়েছে।"
                )

            elif kind == "withdraw":
                row = c.execute(
                    "SELECT * FROM withdrawals WHERE id=?", (item,)
                ).fetchone()

                if not row or row["status"] != "pending":
                    raise ValueError("উইথড্রটি ইতিমধ্যে প্রসেস করা হয়েছে।")

                cur = c.execute("""
                    UPDATE withdrawals SET status=?,reviewed=?
                    WHERE id=? AND status='pending'
                """, ("approved" if yes else "rejected", now(), item))

                if cur.rowcount != 1:
                    raise ValueError("উইথড্রটি ইতিমধ্যে প্রসেস করা হয়েছে।")

                if not yes:
                    user = c.execute(
                        "SELECT worker FROM users WHERE id=?",
                        (row["user_id"],)
                    ).fetchone()

                    if not user:
                        raise ValueError("ইউজার পাওয়া যায়নি।")

                    gross = Decimal(str(row["gross"]))
                    new_balance = (
                        Decimal(str(user["worker"] or "0")) + gross
                    )

                    c.execute(
                        "UPDATE users SET worker=? WHERE id=?",
                        (str(new_balance), row["user_id"])
                    )
                    c.execute("""
                        INSERT INTO ledger(
                            user_id,wallet,amount,kind,note,created
                        ) VALUES(?,?,?,?,?,?)
                    """, (
                        row["user_id"], "worker", str(gross),
                        "withdraw_refund", f"Rejected Withdrawal #{item}", now()
                    ))

                else:
                    referred = c.execute(
                        "SELECT referrer,ref_paid FROM users WHERE id=?",
                        (row["user_id"],)
                    ).fetchone()

                    if referred and referred["referrer"] and not referred["ref_paid"]:
                        bonus_percent = Decimal(
                            get_setting("referral_bonus") or "10"
                        )
                        bonus = (
                            Decimal(str(row["gross"])) *
                            bonus_percent / Decimal("100")
                        ).quantize(
                            Decimal("0.01"),
                            rounding=ROUND_HALF_UP
                        )

                        ref_user = c.execute(
                            "SELECT worker FROM users WHERE id=?",
                            (referred["referrer"],)
                        ).fetchone()

                        if ref_user and bonus > 0:
                            new_ref_balance = (
                                Decimal(str(ref_user["worker"] or "0")) + bonus
                            )
                            c.execute(
                                "UPDATE users SET worker=? WHERE id=?",
                                (str(new_ref_balance), referred["referrer"])
                            )
                            c.execute(
                                "UPDATE users SET ref_paid=1 WHERE id=?",
                                (row["user_id"],)
                            )
                            c.execute("""
                                INSERT INTO ledger(
                                    user_id,wallet,amount,kind,note,created
                                ) VALUES(?,?,?,?,?,?)
                            """, (
                                referred["referrer"], "worker", str(bonus),
                                "referral",
                                f"Referral user {row['user_id']}", now()
                            ))

                target = row["user_id"]
                user_message = (
                    f"উইথড্র #{item} "
                    f"{'অনুমোদিত' if yes else 'বাতিল'} হয়েছে।"
                )

            elif kind == "submission":
                row = c.execute(
                    "SELECT * FROM submissions WHERE id=?", (item,)
                ).fetchone()

                if not row or row["status"] != "pending":
                    raise ValueError("সাবমিশনটি ইতিমধ্যে প্রসেস করা হয়েছে।")

                job = c.execute(
                    "SELECT * FROM jobs WHERE id=?", (row["job_id"],)
                ).fetchone()

                if not job:
                    raise ValueError("সাবমিশনের কাজটি পাওয়া যায়নি।")

                status = "rejected"

                if yes:
                    price = Decimal(str(job["price"] or "0"))
                    reserved = Decimal(str(job["reserved"] or "0"))

                    if (
                        job["status"] == "open"
                        and int(job["count"] or 0) > 0
                        and reserved >= price
                        and price > 0
                    ):
                        worker = c.execute(
                            "SELECT worker FROM users WHERE id=?",
                            (row["worker_id"],)
                        ).fetchone()

                        if not worker:
                            raise ValueError("ফ্রিল্যান্সার পাওয়া যায়নি।")

                        new_worker_balance = (
                            Decimal(str(worker["worker"] or "0")) + price
                        )

                        c.execute(
                            "UPDATE users SET worker=? WHERE id=?",
                            (str(new_worker_balance), row["worker_id"])
                        )

                        left = reserved - price
                        count_left = int(job["count"]) - 1
                        new_status = (
                            "completed"
                            if count_left <= 0 or left <= 0
                            else "open"
                        )

                        c.execute("""
                            UPDATE jobs SET reserved=?,count=?,status=?
                            WHERE id=?
                        """, (
                            str(max(left, Decimal("0"))),
                            max(count_left, 0),
                            new_status,
                            job["id"]
                        ))

                        c.execute("""
                            INSERT INTO ledger(
                                user_id,wallet,amount,kind,note,created
                            ) VALUES(?,?,?,?,?,?)
                        """, (
                            row["worker_id"], "worker", str(price),
                            "earning", f"Job #{job['id']}", now()
                        ))

                        status = "approved"
                        user_message = (
                            f"সাবমিশন #{item} অনুমোদিত। "
                            f"৳{m(price)} আপনার ব্যালেন্সে যোগ হয়েছে।"
                        )
                    else:
                        user_message = (
                            f"সাবমিশন #{item} অনুমোদন করা যায়নি: "
                            "কাজের বাজেট বা কাজের সংখ্যা শেষ।"
                        )
                else:
                    user_message = f"সাবমিশন #{item} বাতিল হয়েছে।"

                cur = c.execute("""
                    UPDATE submissions SET status=?,reviewed=?
                    WHERE id=? AND status='pending'
                """, (status, now(), item))

                if cur.rowcount != 1:
                    raise ValueError("সাবমিশনটি ইতিমধ্যে প্রসেস করা হয়েছে।")

                target = row["worker_id"]

            else:
                raise ValueError("অজানা রিভিউ।")

            c.execute("""
                INSERT INTO audit(actor,action,details,created)
                VALUES(?,?,?,?)
            """, (
                q.from_user.id,
                f"{kind}_{decision}",
                str(item),
                now()
            ))

    except ValueError as exc:
        await q.message.reply_text(str(exc))
        return
    except Exception:
        log.exception("Review failed: kind=%s item=%s", kind, item)
        await q.message.reply_text(
            "রিভিউ করার সময় সমস্যা হয়েছে। Logs পরীক্ষা করুন।"
        )
        return

    await q.message.reply_text(
        f"✅ {kind} #{item} {'Approve' if yes else 'Reject'} সম্পন্ন হয়েছে।"
    )

    if target and user_message:
        try:
            await ctx.bot.send_message(target, user_message)
        except Exception:
            log.exception("Could not notify reviewed user")


async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    uid = q.from_user.id
    user_row(q.from_user)

    with conn() as c:
        banrow = c.execute(
            "SELECT banned FROM users WHERE id=?", (uid,)
        ).fetchone()

    if banrow and banrow["banned"] and not is_admin(uid):
        await q.message.reply_text("⛔ আপনার অ্যাকাউন্ট সীমাবদ্ধ।")
        return

    if data.startswith("review:"):
        if not is_admin(uid):
            await q.message.reply_text("শুধু অ্যাডমিন পারবেন।")
            return

        try:
            _, kind, decision, raw_id = data.split(":")
            if kind not in ("job", "deposit", "withdraw", "submission"):
                raise ValueError
            if decision not in ("yes", "no"):
                raise ValueError
            item = int(raw_id)
        except ValueError:
            await q.message.reply_text("রিভিউ বাটনের তথ্য সঠিক নয়।")
            return

        await review(q, ctx, kind, item, decision == "yes")
        return

    if data.startswith("adm:"):
        if not is_admin(uid):
            await q.message.reply_text("⛔ শুধু অ্যাডমিন পারবেন।")
            return

        parts = data.split(":")

        if data == "adm:home":
            await q.message.reply_text(
                "👑 Admin Menu",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "👑 Admin Job Post",
                        callback_data="adm:jobpost"
                    )],
                    [InlineKeyboardButton(
                        "✏️ লেখা Edit", callback_data="adm:texts"
                    )],
                    [InlineKeyboardButton(
                        "💰 ফি ও সীমা", callback_data="adm:fees"
                    )],
                    [InlineKeyboardButton(
                        "💳 পেমেন্ট নম্বর", callback_data="adm:payments"
                    )],
                    [InlineKeyboardButton(
                        "📊 Pending", callback_data="adm:pending"
                    )],
                    [InlineKeyboardButton(
                        "👥 User List / Ban-Unban",
                        callback_data="adm:users"
                    )]
                ])
            )
            return

        if data == "adm:jobpost":
            rows = [[InlineKeyboardButton(
                f"{name} — Admin Job",
                callback_data=f"cat:{i}:admin"
            )] for i, (name, price) in enumerate(CATS)]

            await q.message.reply_text(
                "👑 Admin Job Post\nক্যাটাগরি বেছে নিন।",
                reply_markup=InlineKeyboardMarkup(rows)
            )
            return

        if data == "adm:texts":
            await admin_setting_prompt(q, ctx, "texts")
            return

        if data == "adm:fees":
            await admin_setting_prompt(q, ctx, "fees")
            return

        if data == "adm:payments":
            await admin_setting_prompt(q, ctx, "payments")
            return

        if data == "adm:pending":
            with conn() as c:
                deposits = c.execute("""
                    SELECT id,user_id,amount,method FROM deposits
                    WHERE status='pending' ORDER BY id DESC LIMIT 10
                """).fetchall()

                withdrawals = c.execute("""
                    SELECT id,user_id,gross,method FROM withdrawals
                    WHERE status='pending' ORDER BY id DESC LIMIT 10
                """).fetchall()

                submissions = c.execute("""
                    SELECT id,job_id,worker_id FROM submissions
                    WHERE status='pending' ORDER BY id DESC LIMIT 10
                """).fetchall()

                jobs = c.execute("""
                    SELECT id,title,buyer_id FROM jobs
                    WHERE status='pending_admin' ORDER BY id DESC LIMIT 10
                """).fetchall()

            lines = ["📋 Pending Queue"]
            buttons = []

            for r in deposits:
                lines.append(
                    f"Deposit #{r['id']} | User {r['user_id']} "
                    f"| ৳{r['amount']} {r['method']}"
                )
                buttons.append([
                    InlineKeyboardButton(
                        f"✅ Approve Deposit #{r['id']}",
                        callback_data=f"review:deposit:yes:{r['id']}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"review:deposit:no:{r['id']}"
                    )
                ])

            for r in jobs:
                lines.append(
                    f"Job Approval #{r['id']} | Buyer {r['buyer_id']} "
                    f"| {r['title']}"
                )
                buttons.append([
                    InlineKeyboardButton(
                        f"✅ Publish Job #{r['id']}",
                        callback_data=f"review:job:yes:{r['id']}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"review:job:no:{r['id']}"
                    )
                ])

            for r in withdrawals:
                lines.append(
                    f"Withdraw #{r['id']} | User {r['user_id']} "
                    f"| ৳{r['gross']} {r['method']}"
                )
                buttons.append([
                    InlineKeyboardButton(
                        f"✅ Approve Withdraw #{r['id']}",
                        callback_data=f"review:withdraw:yes:{r['id']}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"review:withdraw:no:{r['id']}"
                    )
                ])

            for r in submissions:
                lines.append(
                    f"Submission #{r['id']} | Job #{r['job_id']} "
                    f"| Worker {r['worker_id']}"
                )
                buttons.append([
                    InlineKeyboardButton(
                        f"✅ Approve Submission #{r['id']}",
                        callback_data=f"review:submission:yes:{r['id']}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"review:submission:no:{r['id']}"
                    )
                ])

            buttons.append([
                InlineKeyboardButton(
                    "⬅️ Admin Menu", callback_data="adm:home"
                )
            ])

            await q.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        if data == "adm:users":
            with conn() as c:
                total = c.execute(
                    "SELECT COUNT(*) n FROM users"
                ).fetchone()["n"]

                rows = c.execute("""
                    SELECT id,username,first_name,banned
                    FROM users ORDER BY created DESC LIMIT 20
                """).fetchall()

            buttons = []
            for r in rows:
                state = "🚫 BANNED" if r["banned"] else "🟢 ACTIVE"
                username = "@" + r["username"] if r["username"] else "(no username)"
                buttons.append([InlineKeyboardButton(
                    f"{r['id']} | {username} | {state}",
                    callback_data=f"adm:user:{r['id']}"
                )])

            buttons.extend([
                [InlineKeyboardButton(
                    "🔎 Search User ID / Username",
                    callback_data="adm:usersearch"
                )],
                [
                    InlineKeyboardButton(
                        "🔄 Refresh", callback_data="adm:users"
                    ),
                    InlineKeyboardButton(
                        "⬅️ Admin Menu", callback_data="adm:home"
                    )
                ]
            ])

            await q.message.reply_text(
                f"👥 User Management\nমোট ইউজার: {total}",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        if data == "adm:usersearch":
            ctx.user_data["admin_user_search"] = True
            await q.message.reply_text("Telegram User ID অথবা Username লিখুন:")
            return

        if len(parts) == 3 and parts[1] == "user":
            target = int(parts[2])
            with conn() as c:
                row = c.execute(
                    "SELECT * FROM users WHERE id=?", (target,)
                ).fetchone()

            if not row:
                await q.message.reply_text("ইউজার পাওয়া যায়নি।")
                return

            action = "unban" if row["banned"] else "ban"
            await q.message.reply_text(
                f"👤 User Details\nID: {row['id']}\n"
                f"Username: @{row['username'] or 'নেই'}\n"
                f"Name: {row['first_name']}\n"
                f"Status: {'Banned' if row['banned'] else 'Active'}\n"
                f"Ban reason: {row['ban_reason'] or '—'}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔓 Unban" if action == "unban" else "🚫 Ban",
                        callback_data=f"adm:{action}:{target}"
                    )],
                    [InlineKeyboardButton(
                        "⬅️ User List", callback_data="adm:users"
                    )]
                ])
            )
            return

        if len(parts) == 3 and parts[1] in ("ban", "unban"):
            target = int(parts[2])

            if parts[1] == "ban":
                ctx.user_data["ban_target"] = target
                await q.message.reply_text("Ban করার কারণ লিখুন:")
                return

            with conn() as c:
                c.execute(
                    "UPDATE users SET banned=0,ban_reason='' WHERE id=?",
                    (target,)
                )

            await q.message.reply_text(
                f"✅ User {target} Unban করা হয়েছে।"
            )
            return

        if len(parts) == 3 and parts[1] == "edit":
            key = parts[2]
            ctx.user_data["admin_edit_key"] = key
            await q.message.reply_text(
                f"✏️ নতুন মান/লেখা পাঠান।\nবর্তমান মান:\n"
                f"{get_setting(key)}\n\nবাতিল করতে /cancel লিখুন।"
            )
            return

        return

    if data.startswith("dep_method:"):
        method = data.split(":", 1)[1]
        if method not in ("bkash", "nagad"):
            return

        if not ctx.user_data.get("form"):
            await q.message.reply_text("ডিপোজিট ফর্মটি আর সক্রিয় নেই।")
            return

        ctx.user_data["form"]["method"] = method
        ctx.user_data["stage"] = "dep_txn"

        setting = "bkash_number" if method == "bkash" else "nagad_number"
        await q.message.reply_text(
            f"💳 {method.upper()} পেমেন্ট\n"
            f"পরিমাণ: ৳{m(ctx.user_data['form']['amount'])}\n"
            f"পেমেন্ট পাঠানোর নম্বর: {get_setting(setting)}\n\n"
            "পেমেন্ট করার পর Transaction ID লিখুন।"
        )
        return

    if data.startswith("wdmethod:"):
        method = data.split(":", 1)[1]
        if method not in ("bkash", "nagad", "recharge"):
            return

        ctx.user_data["wd_method"] = method
        ctx.user_data["stage"] = "wd_gross"
        ctx.user_data["form"] = {}

        minimum = "50" if method == "recharge" else "100"
        fee_key = "recharge_fee" if method == "recharge" else "withdraw_fee"
        fee = get_setting(fee_key) or "15"

        await q.message.reply_text(
            f"পদ্ধতি: {method}\nসর্বনিম্ন: ৳{minimum}\n"
            f"ফি: {fee}%\nকত টাকা তুলবেন? BDT-তে লিখুন:"
        )
        return

    if data.startswith("cat:"):
        parts = data.split(":")
        try:
            ci = int(parts[1])
            if not 0 <= ci < len(CATS):
                raise ValueError
        except (ValueError, IndexError):
            await q.message.reply_text("ক্যাটাগরি সঠিক নয়।")
            return

        admin_job = len(parts) > 2 and parts[2] == "admin"
        if admin_job and not is_admin(uid):
            await q.message.reply_text("শুধু অ্যাডমিন পারবেন।")
            return

        ctx.user_data["form"] = {
            "cat": ci,
            "admin_job": admin_job
        }
        ctx.user_data["stage"] = "job_title"

        await q.message.reply_text(
            f"প্ল্যাটফর্ম: {CATS[ci][0]}\n"
            f"সর্বনিম্ন দাম ৳{CATS[ci][1]}\n"
            "কাজের শিরোনাম লিখুন।"
        )
        return

    if data == "job:no":
        ctx.user_data.pop("stage", None)
        ctx.user_data.pop("form", None)
        await q.message.reply_text("কাজ পোস্ট বাতিল।")
        return

    if data == "job:yes":
        if (
            ctx.user_data.get("stage") != "job_confirm"
            or not ctx.user_data.get("form")
        ):
            await q.message.reply_text("ফর্মটি আর সক্রিয় নেই।")
            return

        form = ctx.user_data["form"]
        total = Decimal(form["price"]) * int(form["count"])
        admin_job = bool(form.get("admin_job")) and is_admin(uid)

        try:
            with conn() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute(
                    "SELECT buyer FROM users WHERE id=?", (uid,)
                ).fetchone()

                buyer_balance = Decimal(
                    str(row["buyer"] or "0")
                ) if row else Decimal("0")

                if not admin_job and buyer_balance < total:
                    raise ValueError("বায়ার ব্যালেন্স কম। আগে ডিপোজিট করুন।")

                if not admin_job:
                    c.execute(
                        "UPDATE users SET buyer=? WHERE id=?",
                        (str(buyer_balance - total), uid)
                    )

                cur = c.execute("""
                    INSERT INTO jobs(
                        buyer_id,cat,title,description,count,price,
                        reserved,rules,status,created,is_admin_job
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    uid, form["cat"], form["title"], form["desc"],
                    form["count"], form["price"], str(total),
                    form["rules"],
                    "open" if admin_job else "pending_admin",
                    now(), 1 if admin_job else 0
                ))
                job_id = cur.lastrowid

                if not admin_job:
                    c.execute("""
                        INSERT INTO ledger(
                            user_id,wallet,amount,kind,note,created
                        ) VALUES(?,?,?,?,?,?)
                    """, (
                        uid, "buyer", str(-total), "escrow",
                        f"Job #{job_id}", now()
                    ))

        except ValueError as exc:
            await q.message.reply_text(str(exc))
            return

        ctx.user_data.pop("stage", None)
        ctx.user_data.pop("form", None)

        await q.message.reply_text(
            f"কাজ #{job_id} জমা হয়েছে। মোট বাজেট ৳{m(total)}\n"
            + (
                "👑 Admin Job সরাসরি প্রকাশিত হয়েছে।"
                if admin_job else
                "বাজেট সংরক্ষিত হয়েছে। অ্যাডমিন অনুমোদনের পর প্রকাশিত হবে।"
            )
        )

        if not admin_job:
            await admin_msg(
                ctx,
                f"নতুন Job Approval দরকার\nJob #{job_id}: "
                f"{form['title']} | Buyer {uid} | Budget ৳{m(total)}",
                "job", job_id
            )
        return

    if data.startswith("submit:"):
        try:
            job_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            return

        with conn() as c:
            job = c.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            existing = c.execute("""
                SELECT 1 FROM submissions
                WHERE job_id=? AND worker_id=?
            """, (job_id, uid)).fetchone()

        if not job or job["status"] != "open" or job["count"] < 1:
            await q.message.reply_text("কাজটি আর খোলা নেই।")
        elif job["buyer_id"] == uid:
            await q.message.reply_text("নিজের কাজ করা যাবে না।")
        elif existing:
            await q.message.reply_text("এই কাজের প্রুফ আগে জমা দিয়েছেন।")
        else:
            ctx.user_data["proof_job"] = job_id
            ctx.user_data["stage"] = "proof"
            await q.message.reply_text(
                "কাজের প্রুফ লিখুন অথবা স্ক্রিনশট পাঠান। "
                "Cancel বাটন চাপলে বাতিল হবে।"
            )
        return


async def admin(update, ctx):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ শুধু অ্যাডমিনের জন্য।"
        )
        return

    with conn() as c:
        deposits = c.execute(
            "SELECT COUNT(*) n FROM deposits WHERE status='pending'"
        ).fetchone()["n"]

        withdrawals = c.execute(
            "SELECT COUNT(*) n FROM withdrawals WHERE status='pending'"
        ).fetchone()["n"]

        submissions = c.execute(
            "SELECT COUNT(*) n FROM submissions WHERE status='pending'"
        ).fetchone()["n"]

        users = c.execute(
            "SELECT COUNT(*) n FROM users"
        ).fetchone()["n"]

    buttons = [
        [InlineKeyboardButton(
            "👑 Admin Job Post", callback_data="adm:jobpost"
        )],
        [InlineKeyboardButton(
            "✏️ লেখা/মেসেজ Edit", callback_data="adm:texts"
        )],
        [InlineKeyboardButton(
            "💰 ফি ও সীমা Edit", callback_data="adm:fees"
        )],
        [InlineKeyboardButton(
            "💳 পেমেন্ট নম্বর Edit", callback_data="adm:payments"
        )],
        [InlineKeyboardButton(
            "📊 Pending তালিকা", callback_data="adm:pending"
        )],
        [InlineKeyboardButton(
            "👥 User List / Ban-Unban", callback_data="adm:users"
        )]
    ]

    await update.effective_message.reply_text(
        f"👑 BABLU STAR BD — Admin Dashboard\n\n"
        f"📥 Pending Deposit: {deposits}\n"
        f"📤 Pending Withdrawal: {withdrawals}\n"
        f"🧾 Pending Submission: {submissions}\n"
        f"👥 Total Users: {users}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_setting_prompt(q, ctx, group):
    groups = {
        "texts": [
            ("welcome", "স্বাগতম মেসেজ"),
            ("rules", "নিয়মাবলি"),
            ("payment_info", "পেমেন্ট তথ্য"),
            ("support_email", "Support Email"),
            ("banned_text", "Ban মেসেজ"),
            ("support_url", "Support URL")
        ],
        "fees": [
            ("deposit_min", "Minimum Deposit"),
            ("withdraw_min", "Minimum Withdrawal"),
            ("recharge_min", "Minimum Recharge"),
            ("withdraw_fee", "Withdrawal Fee %"),
            ("post_fee", "Job Posting Fee %"),
            ("referral_bonus", "Referral Bonus %"),
            ("usd_rate", "USD Rate ৳")
        ],
        "payments": [
            ("bkash_number", "bKash Number"),
            ("nagad_number", "Nagad Number")
        ]
    }

    buttons = []
    for key, label in groups[group]:
        value = get_setting(key)
        preview = value if len(value) < 25 else value[:22] + "..."
        buttons.append([InlineKeyboardButton(
            f"✏️ {label}: {preview}",
            callback_data=f"adm:edit:{key}"
        )])

    buttons.append([InlineKeyboardButton(
        "⬅️ Admin Menu", callback_data="adm:home"
    )])

    await q.message.reply_text(
        "যে সেটিংস পরিবর্তন করতে চান সেটি চাপুন:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def err(update, ctx):
    log.error(
        "Bot error",
        exc_info=(type(ctx.error), ctx.error, ctx.error.__traceback__)
        if ctx.error else None
    )

    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "সমস্যা হয়েছে। আবার চেষ্টা করুন।"
            )
        except Exception:
            pass


def main():
    if not TOKEN:
        raise SystemExit("Inject Cloud-এ BOT_TOKEN সেট করুন।")

    if not ADMIN:
        raise SystemExit(
            "Inject Cloud-এ ADMIN_ID (numeric Telegram ID) সেট করুন।"
        )

    init()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, photo))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text)
    )

    app.add_error_handler(err)

    log.info("BABLU STAR BD bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
