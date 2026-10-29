import os
import sqlite3
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_NAME = os.getenv("DB_NAME", "bablu_star.db")
REFERRAL_PERCENT = 10.0


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    con = sqlite3.connect(DB_NAME, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            balance REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            reward REAL NOT NULL CHECK(reward > 0),
            status TEXT NOT NULL DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            worker_id INTEGER NOT NULL,
            proof TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            review_reason TEXT DEFAULT '',
            reviewed_by INTEGER,
            reviewed_at TEXT,
            UNIQUE(job_id, worker_id)
        );

        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            reason TEXT NOT NULL,
            banned_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER UNIQUE NOT NULL,
            worker_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            evidence TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            decided_by INTEGER,
            decision_reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER UNIQUE NOT NULL,
            worker_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS referrals (
            referred_id INTEGER PRIMARY KEY,
            referrer_id INTEGER NOT NULL,
            bonus_paid INTEGER NOT NULL DEFAULT 0,
            bonus_amount REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            bonus_paid_at TEXT
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL CHECK(amount > 0),
            method TEXT NOT NULL,
            account TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by INTEGER,
            review_reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS withdrawal_referral_bonus (
            referred_id INTEGER PRIMARY KEY,
            withdrawal_id INTEGER UNIQUE NOT NULL,
            referrer_id INTEGER NOT NULL,
            withdrawal_amount REAL NOT NULL,
            bonus_amount REAL NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_id INTEGER,
            details TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        """)

        # Migrate older versions of these tables safely.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(submissions)")}
        for name, declaration in (
            ("review_reason", "TEXT DEFAULT ''"),
            ("reviewed_by", "INTEGER"),
            ("reviewed_at", "TEXT"),
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE submissions ADD COLUMN {name} {declaration}")

        cols = {r["name"] for r in con.execute("PRAGMA table_info(referrals)")}
        for name, declaration in (
            ("bonus_paid", "INTEGER NOT NULL DEFAULT 0"),
            ("bonus_amount", "REAL NOT NULL DEFAULT 0"),
            ("bonus_paid_at", "TEXT"),
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE referrals ADD COLUMN {name} {declaration}")


def log_action(con, actor, action, target=None, details=""):
    con.execute(
        """INSERT INTO audit_logs(actor_id, action, target_id, details, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (actor, action, target, details, now()),
    )


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def banned(user_id):
    with connect() as con:
        return con.execute(
            "SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)
        ).fetchone() is not None


async def allowed(update):
    user = update.effective_user
    if not user:
        return False
    if banned(user.id) and not is_admin(user.id):
        await update.effective_message.reply_text(
            "⛔ আপনার অ্যাকাউন্ট Ban করা হয়েছে। Admin-এর সঙ্গে যোগাযোগ করুন।"
        )
        return False
    return True


def register_user(user):
    with connect() as con:
        con.execute(
            """INSERT INTO users(user_id, username) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
            (user.id, user.username or user.first_name or ""),
        )


def register_referral(con, new_user_id, start_parameter):
    if not start_parameter or not start_parameter.startswith("ref_"):
        return
    try:
        referrer_id = int(start_parameter[4:])
    except ValueError:
        return
    if referrer_id == new_user_id or referrer_id <= 0:
        return
    # A referral is recorded only on the user's first registration.
    exists = con.execute(
        "SELECT 1 FROM users WHERE user_id=?", (new_user_id,)
    ).fetchone()
    if not exists:
        return
    con.execute(
        """INSERT OR IGNORE INTO referrals
           (referred_id, referrer_id, bonus_paid, bonus_amount, created_at)
           VALUES (?, ?, 0, 0, ?)""",
        (new_user_id, referrer_id, now()),
    )


def pay_worker(con, submission, reward):
    # Unique submission_id in transactions prevents duplicate job payments.
    existing = con.execute(
        "SELECT 1 FROM transactions WHERE submission_id=?", (submission["id"],)
    ).fetchone()
    if existing:
        return False
    con.execute(
        """INSERT INTO users(user_id, username, balance) VALUES (?, '', 0)
           ON CONFLICT(user_id) DO NOTHING""",
        (submission["worker_id"],),
    )
    con.execute(
        """INSERT INTO transactions(submission_id, worker_id, amount, created_at)
           VALUES (?, ?, ?, ?)""",
        (submission["id"], submission["worker_id"], reward, now()),
    )
    con.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (reward, submission["worker_id"]),
    )
    return True


def pay_referral_bonus_for_first_approved_withdrawal(con, withdrawal):
    """Pay 10% of the first approved withdrawal, once per referred user."""
    user_id = withdrawal["user_id"]
    ref = con.execute(
        "SELECT * FROM referrals WHERE referred_id=?", (user_id,)
    ).fetchone()
    if not ref or ref["bonus_paid"]:
        return None

    # This unique key also protects against duplicate bonuses.
    already = con.execute(
        "SELECT 1 FROM withdrawal_referral_bonus WHERE referred_id=?", (user_id,)
    ).fetchone()
    if already:
        con.execute(
            "UPDATE referrals SET bonus_paid=1 WHERE referred_id=?", (user_id,)
        )
        return None

    if con.execute(
        "SELECT 1 FROM banned_users WHERE user_id=?", (ref["referrer_id"],)
    ).fetchone():
        # Keep unpaid; admin can unban then process only if a separate retry feature is added.
        return None

    bonus = round(float(withdrawal["amount"]) * REFERRAL_PERCENT / 100.0, 2)
    if bonus <= 0:
        return None

    con.execute(
        """INSERT INTO users(user_id, username, balance) VALUES (?, '', 0)
           ON CONFLICT(user_id) DO NOTHING""",
        (ref["referrer_id"],),
    )
    con.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (bonus, ref["referrer_id"]),
    )
    con.execute(
        """INSERT INTO withdrawal_referral_bonus
           (referred_id, withdrawal_id, referrer_id, withdrawal_amount,
            bonus_amount, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            user_id, withdrawal["id"], ref["referrer_id"],
            withdrawal["amount"], bonus, now()
        ),
    )
    con.execute(
        """UPDATE referrals SET bonus_paid=1, bonus_amount=?, bonus_paid_at=?
           WHERE referred_id=? AND bonus_paid=0""",
        (bonus, now(), user_id),
    )
    log_action(
        con, 0, "REFERRAL_BONUS_PAID", ref["referrer_id"],
        f"referred={user_id}; withdrawal={withdrawal['id']}; bonus={bonus}"
    )
    return (ref["referrer_id"], bonus)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existed = False
    with connect() as con:
        existed = con.execute(
            "SELECT 1 FROM users WHERE user_id=?", (user.id,)
        ).fetchone() is not None
        con.execute(
            """INSERT INTO users(user_id, username) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
            (user.id, user.username or user.first_name or ""),
        )
        if not existed:
            start_arg = context.args[0] if context.args else ""
            register_referral(con, user.id, start_arg)

    if not await allowed(update):
        return

    await update.message.reply_text(
        "⭐ BABLU STAR BD-তে স্বাগতম!\n\n"
        "/me — আপনার তথ্য\n/balance — ব্যালেন্স\n/jobs — কাজ দেখুন\n"
        "/postjob শিরোনাম | বিবরণ | পুরস্কার\n"
        "/submit JOB_ID | প্রমাণ/লিংক\n/myjobs — পোস্ট করা কাজ\n"
        "/reviews — জমা কাজ\n/refer — রেফার লিংক ও রিপোর্ট\n"
        "/withdraw AMOUNT | METHOD | ACCOUNT — উত্তোলন আবেদন\n"
        "/mywithdrawals — উত্তোলনের অবস্থা\n/help — সব কমান্ড"
    )


async def help_cmd(update, context):
    if not await allowed(update):
        return
    await update.message.reply_text(
        "সাধারণ কমান্ড:\n/start /help /me /balance /jobs /postjob /submit "
        "/myjobs /reviews /refer /withdraw /mywithdrawals /appeal\n\n"
        "Admin:\n/admin /review /appeals /decideappeal /withdrawals "
        "/decidewithdrawal /ban /unban /audit"
    )


async def me(update, context):
    if not await allowed(update):
        return
    user = update.effective_user
    register_user(user)
    with connect() as con:
        row = con.execute(
            "SELECT balance FROM users WHERE user_id=?", (user.id,)
        ).fetchone()
    await update.message.reply_text(
        f"👤 {user.first_name}\n🆔 ID: {user.id}\n"
        f"💰 ব্যালেন্স: ৳{row['balance']:.2f}\n"
        f"🔐 ভূমিকা: {'Admin' if is_admin(user.id) else 'User'}"
    )


async def balance(update, context):
    if not await allowed(update):
        return
    with connect() as con:
        row = con.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (update.effective_user.id,),
        ).fetchone()
    await update.message.reply_text(
        f"💰 ব্যালেন্স: ৳{(row['balance'] if row else 0):.2f}"
    )


async def postjob(update, context):
    if not await allowed(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 2)]
    if len(parts) != 3:
        await update.message.reply_text(
            "ব্যবহার: /postjob শিরোনাম | বিবরণ | পুরস্কার"
        )
        return
    title, description, reward_text = parts
    try:
        reward = float(reward_text)
        if not 0 < reward <= 1_000_000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("পুরস্কারের পরিমাণ সঠিক নয়।")
        return
    user = update.effective_user
    with connect() as con:
        cur = con.execute(
            """INSERT INTO jobs(buyer_id, title, description, reward, status)
               VALUES (?, ?, ?, ?, 'open')""",
            (user.id, title, description, reward),
        )
        job_id = cur.lastrowid
        log_action(con, user.id, "POST_JOB", job_id, title)
    await update.message.reply_text(
        f"✅ কাজ পোস্ট হয়েছে!\nJob ID: {job_id}\n{title}\nপুরস্কার: ৳{reward:.2f}"
    )


async def jobs(update, context):
    if not await allowed(update):
        return
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM jobs WHERE status='open' ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await update.message.reply_text("এখন কোনো খোলা কাজ নেই।")
        return
    for j in rows:
        await update.message.reply_text(
            f"📌 Job #{j['id']}: {j['title']}\n{j['description']}\n"
            f"💰 ৳{j['reward']:.2f}\n"
            f"/submit {j['id']} | আপনার প্রমাণ/লিংক"
        )


async def submit(update, context):
    if not await allowed(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 1)]
    if len(parts) != 2:
        await update.message.reply_text("ব্যবহার: /submit JOB_ID | প্রমাণ/লিংক")
        return
    try:
        job_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("Job ID সঠিক নয়।")
        return
    user_id = update.effective_user.id
    try:
        with connect() as con:
            job = con.execute(
                "SELECT * FROM jobs WHERE id=? AND status='open'", (job_id,)
            ).fetchone()
            if not job:
                await update.message.reply_text("কাজটি নেই বা বন্ধ।")
                return
            if job["buyer_id"] == user_id:
                await update.message.reply_text("নিজের কাজে নিজে সাবমিট করা যাবে না।")
                return
            cur = con.execute(
                """INSERT INTO submissions(job_id, worker_id, proof, status)
                   VALUES (?, ?, ?, 'pending')""",
                (job_id, user_id, parts[1]),
            )
            log_action(con, user_id, "SUBMIT_WORK", cur.lastrowid, f"job={job_id}")
        await update.message.reply_text(f"✅ কাজ জমা হয়েছে। Submission ID: {cur.lastrowid}")
    except sqlite3.IntegrityError:
        await update.message.reply_text("এই কাজে আপনি আগেই সাবমিট করেছেন।")


async def myjobs(update, context):
    if not await allowed(update):
        return
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM jobs WHERE buyer_id=? ORDER BY id DESC",
            (update.effective_user.id,),
        ).fetchall()
    if not rows:
        await update.message.reply_text("আপনি এখনো কাজ পোস্ট করেননি।")
        return
    for j in rows:
        await update.message.reply_text(
            f"Job #{j['id']} — {j['title']}\n৳{j['reward']:.2f} | {j['status']}"
        )


async def reviews(update, context):
    if not await allowed(update):
        return
    with connect() as con:
        rows = con.execute(
            """SELECT s.*, j.title FROM submissions s
               JOIN jobs j ON j.id=s.job_id
               WHERE s.worker_id=? ORDER BY s.id DESC LIMIT 30""",
            (update.effective_user.id,),
        ).fetchall()
    if not rows:
        await update.message.reply_text("আপনার কোনো জমা কাজ নেই।")
        return
    for s in rows:
        await update.message.reply_text(
            f"Submission #{s['id']} — {s['title']}\nStatus: {s['status']}\n"
            f"প্রমাণ: {s['proof']}\nকারণ: {s['review_reason'] or 'নেই'}"
        )


async def review(update, context):
    if not await allowed(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 1)]
    first = parts[0].split()
    if len(first) < 2:
        await update.message.reply_text(
            "ব্যবহার: /review SUBMISSION_ID approve|reject | কারণ"
        )
        return
    try:
        sub_id = int(first[0])
    except ValueError:
        await update.message.reply_text("Submission ID সঠিক নয়।")
        return
    decision = first[1].lower()
    reason = parts[1] if len(parts) > 1 else "কারণ উল্লেখ করা হয়নি"
    if decision not in ("approve", "reject"):
        await update.message.reply_text("approve অথবা reject লিখুন।")
        return

    actor = update.effective_user.id
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        s = con.execute(
            """SELECT s.*, j.buyer_id, j.reward FROM submissions s
               JOIN jobs j ON j.id=s.job_id WHERE s.id=?""",
            (sub_id,),
        ).fetchone()
        if not s:
            await update.message.reply_text("Submission পাওয়া যায়নি।")
            return
        if not is_admin(actor) and s["buyer_id"] != actor:
            await update.message.reply_text("শুধু সংশ্লিষ্ট Buyer বা Admin পারবেন।")
            return
        if s["status"] != "pending":
            await update.message.reply_text("এই কাজের সিদ্ধান্ত ইতিমধ্যে হয়েছে।")
            return
        con.execute(
            """UPDATE submissions SET status=?, review_reason=?, reviewed_by=?, reviewed_at=?
               WHERE id=?""",
            (decision, reason, actor, now(), sub_id),
        )
        if decision == "approve":
            pay_worker(con, s, s["reward"])
        log_action(con, actor, "REVIEW_SUBMISSION", sub_id, f"{decision}: {reason}")
    await update.message.reply_text(f"Submission #{sub_id}: {decision}\nকারণ: {reason}")


async def appeal(update, context):
    if not await allowed(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 2)]
    if len(parts) < 2:
        await update.message.reply_text(
            "ব্যবহার: /appeal SUBMISSION_ID | কারণ | প্রমাণ/লিংক"
        )
        return
    try:
        sub_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("Submission ID সঠিক নয়।")
        return
    reason = parts[1]
    evidence = parts[2] if len(parts) > 2 else ""
    with connect() as con:
        s = con.execute(
            "SELECT * FROM submissions WHERE id=?", (sub_id,)
        ).fetchone()
        if not s or s["worker_id"] != update.effective_user.id:
            await update.message.reply_text("এই Submission আপনার নয় বা নেই।")
            return
        if s["status"] != "rejected":
            await update.message.reply_text("শুধু বাতিল কাজের বিরুদ্ধে আপিল করা যাবে।")
            return
        try:
            cur = con.execute(
                """INSERT INTO appeals
                   (submission_id, worker_id, reason, evidence, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sub_id, update.effective_user.id, reason, evidence, now()),
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text("এই কাজের জন্য আগেই আপিল করেছেন।")
            return
        log_action(con, update.effective_user.id, "FILE_APPEAL", cur.lastrowid, reason)
    await update.message.reply_text(f"আপিল জমা হয়েছে। Appeal ID: {cur.lastrowid}")


async def appeals_cmd(update, context):
    if not await allowed(update):
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    with connect() as con:
        rows = con.execute(
            """SELECT a.*, s.proof FROM appeals a
               JOIN submissions s ON s.id=a.submission_id
               WHERE a.status='pending' ORDER BY a.id"""
        ).fetchall()
    if not rows:
        await update.message.reply_text("কোনো Pending appeal নেই।")
        return
    for a in rows:
        await update.message.reply_text(
            f"Appeal #{a['id']} | Submission #{a['submission_id']}\n"
            f"Worker: {a['worker_id']}\nকারণ: {a['reason']}\n"
            f"প্রমাণ: {a['evidence']}\nকাজের প্রমাণ: {a['proof']}\n"
            f"/decideappeal {a['id']} approve | কারণ\n"
            f"/decideappeal {a['id']} reject | কারণ"
        )


async def decideappeal(update, context):
    if not await allowed(update):
        return
    actor = update.effective_user.id
    if not is_admin(actor):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 1)]
    first = parts[0].split()
    if len(first) < 2:
        await update.message.reply_text("ব্যবহার: /decideappeal ID approve|reject | কারণ")
        return
    try:
        appeal_id = int(first[0])
    except ValueError:
        await update.message.reply_text("Appeal ID সঠিক নয়।")
        return
    decision = first[1].lower()
    reason = parts[1] if len(parts) > 1 else "Admin সিদ্ধান্ত"
    if decision not in ("approve", "reject"):
        await update.message.reply_text("approve অথবা reject লিখুন।")
        return

    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        a = con.execute(
            """SELECT a.*, s.status AS sub_status, s.worker_id AS sub_worker,
                      j.reward FROM appeals a
               JOIN submissions s ON s.id=a.submission_id
               JOIN jobs j ON j.id=s.job_id WHERE a.id=?""",
            (appeal_id,),
        ).fetchone()
        if not a or a["status"] != "pending":
            await update.message.reply_text("আপিল নেই বা সিদ্ধান্ত দেওয়া হয়েছে।")
            return
        con.execute(
            """UPDATE appeals SET status=?, decided_by=?, decision_reason=?, decided_at=?
               WHERE id=?""",
            (decision, actor, reason, now(), appeal_id),
        )
        if decision == "approve":
            con.execute(
                """UPDATE submissions SET status='approved', review_reason=?,
                   reviewed_by=?, reviewed_at=? WHERE id=?""",
                (reason, actor, now(), a["submission_id"]),
            )
            pay_worker(
                con,
                {"id": a["submission_id"], "worker_id": a["sub_worker"]},
                a["reward"],
            )
        else:
            con.execute(
                """UPDATE submissions SET status='rejected', review_reason=?,
                   reviewed_by=?, reviewed_at=? WHERE id=?""",
                (reason, actor, now(), a["submission_id"]),
            )
        log_action(con, actor, "DECIDE_APPEAL", appeal_id, f"{decision}: {reason}")
    await update.message.reply_text(f"আপিল #{appeal_id}: {decision}\nকারণ: {reason}")


async def refer(update, context):
    if not await allowed(update):
        return
    user = update.effective_user
    bot_info = await context.bot.get_me()
    with connect() as con:
        total = con.execute(
            "SELECT COUNT(*) n FROM referrals WHERE referrer_id=?", (user.id,)
        ).fetchone()["n"]
        paid = con.execute(
            "SELECT COUNT(*) n FROM referrals WHERE referrer_id=? AND bonus_paid=1",
            (user.id,),
        ).fetchone()["n"]
        earned = con.execute(
            "SELECT COALESCE(SUM(bonus_amount),0) n FROM referrals WHERE referrer_id=?",
            (user.id,),
        ).fetchone()["n"]
    link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"
    await update.message.reply_text(
        f"🔗 আপনার Referral Link:\n{link}\n\n"
        f"👥 মোট রেফার: {total}\n✅ বোনাস পাওয়া রেফার: {paid}\n"
        f"🎁 মোট রেফার বোনাস: ৳{earned:.2f}\n\n"
        "নিয়ম: আপনার রেফারে আসা সদস্যের প্রথম অনুমোদিত উত্তোলনের "
        "১০% আপনি একবার পাবেন।"
    )


async def withdraw(update, context):
    if not await allowed(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 2)]
    if len(parts) != 3:
        await update.message.reply_text(
            "ব্যবহার: /withdraw AMOUNT | bKash/Nagad | Account Number"
        )
        return
    try:
        amount = round(float(parts[0]), 2)
        if amount <= 0 or amount > 1_000_000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("টাকার পরিমাণ সঠিক নয়।")
        return
    method, account = parts[1], parts[2]
    user_id = update.effective_user.id
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        user = con.execute(
            "SELECT balance FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not user or user["balance"] < amount:
            await update.message.reply_text("আপনার পর্যাপ্ত ব্যালেন্স নেই।")
            return
        con.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, user_id))
        cur = con.execute(
            """INSERT INTO withdrawals(user_id, amount, method, account, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, amount, method, account, now()),
        )
        log_action(con, user_id, "WITHDRAW_REQUEST", cur.lastrowid, f"{amount}; {method}")
    await update.message.reply_text(
        f"📤 উত্তোলন আবেদন #{cur.lastrowid} জমা হয়েছে: ৳{amount:.2f}\n"
        "Admin অনুমোদন না করা পর্যন্ত অপেক্ষা করুন।"
    )


async def mywithdrawals(update, context):
    if not await allowed(update):
        return
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (update.effective_user.id,),
        ).fetchall()
    if not rows:
        await update.message.reply_text("আপনার কোনো উত্তোলন আবেদন নেই।")
        return
    for w in rows:
        await update.message.reply_text(
            f"Withdrawal #{w['id']} — ৳{w['amount']:.2f}\n"
            f"পদ্ধতি: {w['method']}\nStatus: {w['status']}\n"
            f"কারণ: {w['review_reason'] or 'নেই'}"
        )


async def withdrawals_cmd(update, context):
    if not await allowed(update):
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM withdrawals WHERE status='pending' ORDER BY id"
        ).fetchall()
    if not rows:
        await update.message.reply_text("কোনো Pending withdrawal নেই।")
        return
    for w in rows:
        await update.message.reply_text(
            f"Withdrawal #{w['id']} | User {w['user_id']}\n"
            f"Amount: ৳{w['amount']:.2f}\nMethod: {w['method']}\n"
            f"Account: {w['account']}\n"
            f"/decidewithdrawal {w['id']} approve | কারণ\n"
            f"/decidewithdrawal {w['id']} reject | কারণ"
        )


async def decidewithdrawal(update, context):
    if not await allowed(update):
        return
    actor = update.effective_user.id
    if not is_admin(actor):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 1)]
    first = parts[0].split()
    if len(first) < 2:
        await update.message.reply_text(
            "ব্যবহার: /decidewithdrawal ID approve|reject | কারণ"
        )
        return
    try:
        wid = int(first[0])
    except ValueError:
        await update.message.reply_text("Withdrawal ID সঠিক নয়।")
        return
    decision = first[1].lower()
    reason = parts[1] if len(parts) > 1 else "Admin সিদ্ধান্ত"
    if decision not in ("approve", "reject"):
        await update.message.reply_text("approve অথবা reject লিখুন।")
        return

    bonus_info = None
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        w = con.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
        if not w or w["status"] != "pending":
            await update.message.reply_text("আবেদন নেই বা সিদ্ধান্ত দেওয়া হয়েছে।")
            return
        con.execute(
            """UPDATE withdrawals SET status=?, reviewed_by=?, review_reason=?, reviewed_at=?
               WHERE id=?""",
            (decision, actor, reason, now(), wid),
        )
        if decision == "reject":
            con.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (w["amount"], w["user_id"]),
            )
        else:
            bonus_info = pay_referral_bonus_for_first_approved_withdrawal(con, w)
        log_action(con, actor, "DECIDE_WITHDRAWAL", wid, f"{decision}: {reason}")

    msg = f"Withdrawal #{wid}: {decision}\nকারণ: {reason}"
    if bonus_info:
        msg += f"\nরেফারার {bonus_info[0]}-কে ৳{bonus_info[1]:.2f} বোনাস যোগ হয়েছে।"
    await update.message.reply_text(msg)


async def ban_cmd(update, context):
    if not await allowed(update):
        return
    actor = update.effective_user.id
    if not is_admin(actor):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 1)]
    try:
        target = int(parts[0].split()[0])
    except (ValueError, IndexError):
        await update.message.reply_text("ব্যবহার: /ban USER_ID | কারণ")
        return
    if target == ADMIN_ID:
        await update.message.reply_text("নিজের Admin ID Ban করা যাবে না।")
        return
    reason = parts[1] if len(parts) > 1 else "কারণ উল্লেখ নেই"
    with connect() as con:
        con.execute(
            """INSERT INTO banned_users(user_id, reason, banned_by, created_at)
               VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
               reason=excluded.reason, banned_by=excluded.banned_by,
               created_at=excluded.created_at""",
            (target, reason, actor, now()),
        )
        log_action(con, actor, "BAN_USER", target, reason)
    await update.message.reply_text(f"⛔ User {target} Ban হয়েছে।")


async def unban_cmd(update, context):
    if not await allowed(update):
        return
    actor = update.effective_user.id
    if not is_admin(actor):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    parts = [p.strip() for p in " ".join(context.args).split("|", 1)]
    try:
        target = int(parts[0].split()[0])
    except (ValueError, IndexError):
        await update.message.reply_text("ব্যবহার: /unban USER_ID | কারণ")
        return
    reason = parts[1] if len(parts) > 1 else "কারণ উল্লেখ নেই"
    with connect() as con:
        con.execute("DELETE FROM banned_users WHERE user_id=?", (target,))
        log_action(con, actor, "UNBAN_USER", target, reason)
    await update.message.reply_text(f"✅ User {target} Unban হয়েছে।")


async def audit(update, context):
    if not await allowed(update):
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("শুধু Admin পারবেন।")
        return
    with connect() as con:
        rows = con.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 15").fetchall()
    if not rows:
        await update.message.reply_text("Audit record নেই।")
        return
    for r in rows:
        await update.message.reply_text(
            f"#{r['id']} {r['created_at']}\nActor: {r['actor_id']}\n"
            f"{r['action']} | Target: {r['target_id']}\n{r['details']}"
        )


async def admin_cmd(update, context):
    if not await allowed(update):
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("আপনি Admin নন।")
        return
    with connect() as con:
        users = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        jobs_count = con.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
        subs = con.execute(
            "SELECT COUNT(*) n FROM submissions WHERE status='pending'"
        ).fetchone()["n"]
        appeals_count = con.execute(
            "SELECT COUNT(*) n FROM appeals WHERE status='pending'"
        ).fetchone()["n"]
        wds = con.execute(
            "SELECT COUNT(*) n FROM withdrawals WHERE status='pending'"
        ).fetchone()["n"]
        bans = con.execute("SELECT COUNT(*) n FROM banned_users").fetchone()["n"]
    await update.message.reply_text(
        "🛡 BABLU STAR BD Admin Panel\n"
        f"Users: {users}\nJobs: {jobs_count}\nPending work: {subs}\n"
        f"Pending appeals: {appeals_count}\nPending withdrawals: {wds}\n"
        f"Banned users: {bans}\n\n"
        "/appeals\n/withdrawals\n/audit\n"
        "/ban USER_ID | কারণ\n/unban USER_ID | কারণ"
    )


def main():
    if not TOKEN:
        raise RuntimeError("Inject Cloud-এ BOT_TOKEN সেট করুন।")
    if ADMIN_ID == 0:
        raise RuntimeError("Inject Cloud-এ ADMIN_ID সঠিকভাবে সেট করুন।")

    init_db()
    app = Application.builder().token(TOKEN).build()
    handlers = [
        ("start", start), ("help", help_cmd), ("me", me),
        ("balance", balance), ("postjob", postjob), ("jobs", jobs),
        ("submit", submit), ("myjobs", myjobs), ("reviews", reviews),
        ("review", review), ("appeal", appeal), ("appeals", appeals_cmd),
        ("decideappeal", decideappeal), ("refer", refer),
        ("withdraw", withdraw), ("mywithdrawals", mywithdrawals),
        ("withdrawals", withdrawals_cmd), ("decidewithdrawal", decidewithdrawal),
        ("ban", ban_cmd), ("unban", unban_cmd), ("audit", audit),
        ("admin", admin_cmd),
    ]
    for command, callback in handlers:
        app.add_handler(CommandHandler(command, callback))

    print("BABLU STAR BD bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
