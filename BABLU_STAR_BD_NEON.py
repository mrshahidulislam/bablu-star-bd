import os, logging, re, time, threading
import psycopg2
from psycopg2 import OperationalError, InterfaceError
from psycopg2.pool import ThreadedConnectionPool, PoolError
from psycopg2.extras import RealDictCursor
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN = int(os.getenv("ADMIN_ID", "0") or "0")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
FEE = Decimal("0.15")
REFERRAL = Decimal("0.10")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bablu-star-bd")
DB_POOL = None
DB_POOL_LOCK = threading.Lock()

CATS = [
    ("📘 Facebook", 2), ("▶️ YouTube", 2), ("📸 Instagram", 2), ("🎵 TikTok", 2),
    ("𝕏 X (Twitter)", 2), ("👻 Snapchat", 2), ("✈️ Telegram", 2), ("💼 LinkedIn", 2),
]
TASK_TYPES = ["👍 Like", "👥 Follow", "💬 Comment", "🔁 Repost"]

BUYER = [["➕ কাজ পোস্ট","📋 আমার কাজ"],["💰 বায়ার ব্যালেন্স","➕ ডিপোজিট"],["📤 মোড বদল","🏠 মেইন মেনু"],["🔙 Back / ❌ Cancel"]]
WORKER = [["🔎 কাজ দেখুন","📨 আমার সাবমিশন"],["💰 ফ্রিল্যান্সার ব্যালেন্স","💸 উইথড্র"],["👥 রেফার লিংক","📤 মোড বদল"],["🏠 মেইন মেনু","🔙 Back / ❌ Cancel"]]
MAIN = [["🛒 Buyer Mode","🧑‍💻 Freelancer Mode"],["💳 পেমেন্ট তথ্য","📜 নিয়মাবলি"],["🆘 সাহায্য","🔙 Back / ❌ Cancel"]]

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def m(v):
    d=Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"
def amount(s):
    try:
        raw = str(s).translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
        x=Decimal(raw.replace(",","").replace("৳","").strip())
        return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if x.is_finite() and x>0 else None
    except (InvalidOperation,ValueError,TypeError): return None

def safe_rate(value, default="0.15"):
    """Read a stored decimal rate safely; reject corrupt/out-of-range settings."""
    try:
        rate = Decimal(str(value).strip())
        if rate.is_finite() and Decimal("0") <= rate <= Decimal("1"):
            return rate
    except (InvalidOperation, ValueError, TypeError):
        pass
    try:
        fallback = Decimal(str(default))
        if fallback.is_finite() and Decimal("0") <= fallback <= Decimal("1"):
            return fallback
    except (InvalidOperation, ValueError, TypeError):
        pass
    return Decimal("0.15")

class PGCursor:
    def __init__(self, cursor, connection=None):
        self._cursor = cursor
        self._connection = connection

    def _run(self, query, params=None):
        """Run SQL; recover once from a stale Neon connection before a transaction starts."""
        try:
            self._cursor.execute(query, params or ())
        except (OperationalError, InterfaceError):
            if self._connection is None or self._connection._executed:
                raise
            self._connection.reconnect()
            self._cursor = self._connection.raw.cursor(cursor_factory=RealDictCursor)
            self._connection._cursors.append(self._cursor)
            self._cursor.execute(query, params or ())
        if self._connection is not None:
            self._connection._executed += 1
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, query, params=None):
        q = query.strip()
        if q.upper() == "BEGIN IMMEDIATE":
            q = "BEGIN"
        # SQLite schema introspection -> PostgreSQL information_schema.
        if q.upper().startswith("PRAGMA TABLE_INFO("):
            table = q[q.find("(")+1:q.rfind(")")].strip().strip("'\"")
            self._run(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position",
                (table,)
            )
            rows = self._cursor.fetchall()
            self._pragma_rows = [(i, r["name"]) for i, r in enumerate(rows)]
            return self

        ignore_insert = query.lstrip().upper().startswith("INSERT OR IGNORE")
        if ignore_insert:
            q = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", q, flags=re.I)
            q = q.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        # All parameter markers in this project are SQLite-style question marks.
        q = q.replace("?", "%s")

        # Capture generated IDs for existing code that reads cursor.lastrowid.
        self.lastrowid = None
        if q.lstrip().upper().startswith("INSERT INTO") and " RETURNING " not in q.upper():
            table_match = re.match(r"\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", q, re.I)
            table = table_match.group(1).lower() if table_match else ""
            if table in {"jobs", "submissions", "deposits", "withdrawals", "ledger", "audit"}:
                q = q.rstrip().rstrip(";") + " RETURNING id"
                self._run(q, params)
                row = self._cursor.fetchone()
                if row:
                    self.lastrowid = row["id"]
                self.rowcount = 1
                return self

        self._run(q, params)
        self.rowcount = self._cursor.rowcount
        return self

    def fetchone(self):
        if hasattr(self, "_pragma_rows"):
            return self._pragma_rows.pop(0) if self._pragma_rows else None
        return self._cursor.fetchone()

    def fetchall(self):
        if hasattr(self, "_pragma_rows"):
            rows, self._pragma_rows = self._pragma_rows, []
            return rows
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self.fetchall())


class PGConnection:
    def __init__(self):
        global DB_POOL
        if not DATABASE_URL:
            raise RuntimeError("Hostless Secrets-এ DATABASE_URL সেট করুন (Neon PostgreSQL connection string)।")
        self.raw = self._get_connection()
        self.raw.autocommit = False
        self._cursors = []
        self._executed = 0

    @staticmethod
    def _get_connection():
        global DB_POOL
        last_error = None
        for attempt in range(3):
            try:
                with DB_POOL_LOCK:
                    if DB_POOL is None:
                        DB_POOL = ThreadedConnectionPool(
                            1, 10, DATABASE_URL, sslmode="require", connect_timeout=15,
                            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3
                        )
                    raw = DB_POOL.getconn()
                if raw.closed:
                    with DB_POOL_LOCK:
                        DB_POOL.putconn(raw, close=True)
                    raise OperationalError("Neon returned a closed pooled connection")
                return raw
            except (OperationalError, InterfaceError, PoolError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
        raise last_error

    def reconnect(self):
        """Replace a stale pooled connection; only called before any SQL succeeded."""
        global DB_POOL
        old = self.raw
        try:
            with DB_POOL_LOCK:
                if DB_POOL is not None:
                    DB_POOL.putconn(old, close=True)
        except Exception:
            try: old.close()
            except Exception: pass
        self.raw = self._get_connection()
        self.raw.autocommit = False
        self._cursors = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            for cur in self._cursors:
                try: cur.close()
                except Exception: pass
            self._cursors.clear()
            if exc_type:
                self.raw.rollback()
            else:
                self.raw.commit()
        finally:
            if self.raw.closed:
                with DB_POOL_LOCK:
                    DB_POOL.putconn(self.raw, close=True)
            else:
                with DB_POOL_LOCK:
                    DB_POOL.putconn(self.raw)
        return False

    def execute(self, query, params=None):
        cur = self.raw.cursor(cursor_factory=RealDictCursor)
        self._cursors.append(cur)
        return PGCursor(cur, self).execute(query, params)

    def executescript(self, script):
        cur = self.raw.cursor()
        try:
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)
        finally:
            cur.close()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


def conn():
    return PGConnection()


def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
            buyer TEXT DEFAULT '0', worker TEXT DEFAULT '0', referrer BIGINT,
            ref_paid INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, created TEXT,
            ban_reason TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id BIGSERIAL PRIMARY KEY, buyer_id BIGINT, cat INTEGER, title TEXT,
            description TEXT, count INTEGER, price TEXT, reserved TEXT, rules TEXT,
            proof_type TEXT NOT NULL DEFAULT 'text', status TEXT DEFAULT 'open', created TEXT
        );
        CREATE TABLE IF NOT EXISTS submissions(
            id BIGSERIAL PRIMARY KEY, job_id BIGINT, worker_id BIGINT, proof TEXT,
            file_id TEXT DEFAULT '', status TEXT DEFAULT 'pending', created TEXT,
            reviewed TEXT, UNIQUE(job_id,worker_id)
        );
        CREATE TABLE IF NOT EXISTS deposits(
            id BIGSERIAL PRIMARY KEY, user_id BIGINT, amount TEXT, method TEXT,
            txn TEXT UNIQUE, status TEXT DEFAULT 'pending', created TEXT, reviewed TEXT
        );
        CREATE TABLE IF NOT EXISTS withdrawals(
            id BIGSERIAL PRIMARY KEY, user_id BIGINT, gross TEXT, fee TEXT, net TEXT,
            method TEXT, account TEXT, status TEXT DEFAULT 'pending', created TEXT, reviewed TEXT
        );
        CREATE TABLE IF NOT EXISTS ledger(
            id BIGSERIAL PRIMARY KEY, user_id BIGINT, wallet TEXT, amount TEXT,
            kind TEXT, note TEXT, created TEXT
        );
        CREATE TABLE IF NOT EXISTS audit(
            id BIGSERIAL PRIMARY KEY, actor BIGINT, action TEXT, details TEXT, created TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        """)
        defaults = {
            "deposit_min":"50", "withdraw_min":"100", "recharge_min":"50",
            "withdraw_fee":"0.15", "job_fee":"0.05", "referral_rate":"0.10",
            "withdraw_request_sent_text":"✅ আপনার Withdrawal Request সফলভাবে জমা হয়েছে।\\n\\n🕒 অ্যাডমিন যাচাই করার পর পেমেন্ট পাঠানো হবে।\\n📌 অনুগ্রহ করে অপেক্ষা করুন।\\n\\n— BABLU STAR BD",
            "withdraw_instructions_text":"💸 টাকা উত্তোলন\n\nপ্রথমে পেমেন্ট পদ্ধতি নির্বাচন করুন। বিকাশ/নগদে সর্বনিম্ন ৳১০০, মোবাইল রিচার্জে ৳৫০। সার্ভিস চার্জ ১৫%।",
            "withdraw_pending_text":"⏳ আপনার Withdrawal Request অপেক্ষমাণ আছে। অ্যাডমিন যাচাই করছেন।",
            "withdraw_approved_text":"✅ আপনার Withdrawal Request অনুমোদিত হয়েছে।",
            "withdraw_rejected_text":"❌ আপনার Withdrawal Request বাতিল হয়েছে। টাকা আপনার ব্যালেন্সে ফেরত দেওয়া হয়েছে।",
            "welcome_text":"🌟 BABLU STAR BD-তে আপনাকে স্বাগতম! 🌟\n\n💼 আপনার কাজ, আপনার উপার্জন!\n🛒 Buyer Mode — নিজের কাজ পোস্ট করুন\n🧑‍💻 Freelancer Mode — কাজ করুন ও আয় করুন\n\n👇 শুরু করতে নিচের একটি মোড বেছে নিন।",
            "payment_info_text":"💳 BABLU STAR BD — পেমেন্ট তথ্য\n\n➕ ডিপোজিট: বায়ার মোডে ডিপোজিট চাপুন, পরিমাণ লিখুন, বিকাশ/নগদ বেছে নিয়ে Transaction ID জমা দিন। অ্যাডমিন যাচাই করে ব্যালেন্স যোগ করবেন।\n\n💸 উইথড্র: ফ্রিল্যান্সার মোড → উইথড্র → পরিমাণ লিখুন → বিকাশ/নগদ/মোবাইল রিচার্জ বেছে নিন → নম্বর দিন।\n\nবিকাশ/নগদে সর্বনিম্ন উইথড্র ৳১০০, মোবাইল রিচার্জ ৳৫০। প্রতিটি উইথড্র/রিচার্জে ১৫% ফি।",
            "rules_text":"• কাজের দাম ক্যাটাগরি মিনিমামের কম নয়।\n• ভুয়া অ্যাকাউন্ট, প্রতারণা, স্প্যাম বা প্ল্যাটফর্মের নিয়মভঙ্গকারী কাজ নিষিদ্ধ।\n• বাজেট আগে বায়ার ব্যালেন্স থেকে সংরক্ষিত হবে।\n• প্রুফ অ্যাডমিন অনুমোদন করলে ফ্রিল্যান্সার টাকা পাবেন।\n• ডিপোজিট/উইথড্র ম্যানুয়ালি যাচাই হয়।",
            "support_text":"📩 BABLU STAR BD — Support Center\n\nসমস্যা বা প্রশ্ন থাকলে আমাদের ইমেইল করুন।\n✉️ Support Email: {email}\n🆔 আপনার Telegram ID: {user_id}\n\nসমস্যার বিস্তারিত লিখে ইমেইল পাঠান।",
            "referral_text":"👥 আপনার রেফার লিংক:\n{link}\n\n✅ মোট রেফার: {count} জন\n💰 প্রথম অনুমোদিত উইথড্রয়ের ১০% একবার বোনাস পাবেন।"
        }
        for key, value in defaults.items():
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (key, value))
        # Safe additive migration for databases created by earlier bot versions.
        c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS proof_type TEXT NOT NULL DEFAULT 'text'")
        c.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS rejection_reason TEXT NOT NULL DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS buyer TEXT DEFAULT '0'")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS worker TEXT DEFAULT '0'")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_reason TEXT DEFAULT ''")
        # Frequently used lookups; safe and idempotent on existing Neon databases.
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_referrer ON users(referrer)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_worker_created ON submissions(worker_id, created)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ledger_user_kind ON ledger(user_id, kind)")

def user_row(u):
    # One atomic round-trip instead of SELECT followed by INSERT/UPDATE.
    with conn() as c:
        return c.execute(
            "INSERT INTO users(id,username,first_name,created) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name "
            "RETURNING banned, (SELECT value FROM settings WHERE key='welcome_text') AS welcome_text",
            (u.id, u.username or "", u.first_name or "", now())
        ).fetchone()
def kb(mode):
    return ReplyKeyboardMarkup(BUYER if mode=="buyer" else WORKER if mode=="worker" else MAIN,resize_keyboard=True)
def is_banned(uid):
    with conn() as c:
        r=c.execute("SELECT banned FROM users WHERE id=?",(uid,)).fetchone()
    return bool(r and r["banned"])
def support_only_kb():
    return ReplyKeyboardMarkup([["🆘 সাহায্য"]],resize_keyboard=True)
async def banned_notice(target):
    await target.reply_text("⛔ আপনার অ্যাকাউন্ট সাময়িকভাবে বন্ধ আছে। আপিল বা সহায়তার জন্য নিচের 🆘 সাহায্য বাটন ব্যবহার করুন।",reply_markup=support_only_kb())
async def menu(update,ctx,msg="মেনু থেকে অপশন বেছে নিন.",check_ban=True):
    if check_ban and update.effective_user and is_banned(update.effective_user.id):
        target=update.effective_message or update.callback_query.message
        await banned_notice(target); return
    for k in ("stage","form","proof_job","pending_submission_rejection","admin_setting_key","proof_file_id","admin_user_search"): ctx.user_data.pop(k,None)
    target=update.effective_message or update.callback_query.message
    await target.reply_text(msg,reply_markup=kb(ctx.user_data.get("mode","main")))
async def admin_msg(ctx,text,kind=None,item=None):
    if not ADMIN: return
    markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve",callback_data=f"review:{kind}:yes:{item}"),InlineKeyboardButton("❌ Reject",callback_data=f"review:{kind}:no:{item}")]]) if kind else None
    try: await ctx.bot.send_message(ADMIN,text,reply_markup=markup)
    except Exception: log.exception("admin notification failed")
async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    try:
        row = user_row(u)
        if row and row.get("banned"):
            await banned_notice(update.effective_message)
            return
        if ctx.args and ctx.args[0].startswith("ref_"):
            try:
                ref = int(ctx.args[0][4:])
                if ref != u.id:
                    with conn() as c:
                        c.execute(
                            "UPDATE users SET referrer=? WHERE id=? AND referrer IS NULL "
                            "AND EXISTS (SELECT 1 FROM users WHERE id=?)",
                            (ref, u.id, ref)
                        )
            except ValueError:
                log.warning("Invalid referral argument from user %s", u.id)
        ctx.user_data["mode"] = "main"
        welcome = (row.get("welcome_text") if row else None) or "🌟 BABLU STAR BD-তে আপনাকে স্বাগতম! 🌟\n\n💼 আপনার কাজ, আপনার উপার্জন!\n🛒 Buyer Mode — নিজের কাজ পোস্ট করুন\n🧑‍💻 Freelancer Mode — কাজ করুন ও আয় করুন\n👇 শুরু করতে নিচের একটি মোড বেছে নিন।"
        await menu(update, ctx, welcome, check_ban=False)
    except Exception:
        log.exception("/start failed for Telegram user_id=%s", u.id if u else "unknown")
        await update.effective_message.reply_text("সাময়িক সংযোগ সমস্যা হয়েছে। ২–৩ সেকেন্ড পর আবার /start দিন।")

async def text(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; r=user_row(u); t=(update.effective_message.text or "").strip()
    if r and r["banned"]:
        if t=="🆘 সাহায্য":
            await update.effective_message.reply_text(get_setting("support_text", "📩 Support Email: {email}\nআপনার Telegram ID: {user_id}").format(email=get_setting('support_email','mrshahidulislam8989@gmail.com'), user_id=u.id))
        else: await banned_notice(update.effective_message)
        return
    if t in ("🔙 Back / ❌ Cancel","🔙 Back","❌ Cancel","/cancel"):
        await menu(update,ctx,"চলমান কাজ বাতিল হয়েছে।"); return
    if ctx.user_data.get("admin_user_search"):
        if u.id != ADMIN:
            ctx.user_data.pop("admin_user_search", None)
            await update.effective_message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।")
            return
        query_text = t.lstrip("@").strip()
        ctx.user_data.pop("admin_user_search", None)
        with conn() as c:
            if query_text.isdigit():
                matches = c.execute("SELECT id,username,first_name,banned FROM users WHERE id=?", (int(query_text),)).fetchall()
            else:
                matches = c.execute("SELECT id,username,first_name,banned FROM users WHERE username ILIKE ? ORDER BY id DESC LIMIT 20", (f"%{query_text}%",)).fetchall()
        if not matches:
            await update.effective_message.reply_text("এই ID/Username-এ কোনো User পাওয়া যায়নি। আবার /admin খুলুন।")
            return
        buttons=[]
        for item in matches:
            uname="@" + item["username"] if item["username"] else "@—"
            name=item["first_name"] or "নাম নেই"
            status="⛔" if item["banned"] else "🟢"
            buttons.append([InlineKeyboardButton(f"{status} {name[:14]} | {uname[:14]} | {item['id']}", callback_data=f"adminusers:view:{item['id']}:0")])
        await update.effective_message.reply_text("🔎 User Search Results", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if ctx.user_data.get("pending_submission_rejection"):
        sid = int(ctx.user_data.pop("pending_submission_rejection"))
        if len(t) < 3 or len(t) > 1000:
            ctx.user_data["pending_submission_rejection"] = sid
            await update.effective_message.reply_text("Reject করার কারণ ৩–১০০০ অক্ষরের মধ্যে লিখুন। বাতিল করতে /cancel লিখুন।")
            return
        await review_from_reason(update, ctx, sid, t)
        return
    st=ctx.user_data.get("stage")
    if st:
        await stage_input(update,ctx,st,t); return
    mode=ctx.user_data.get("mode","main")
    if t=="🛒 Buyer Mode": ctx.user_data["mode"]="buyer"; await menu(update,ctx,"Buyer Mode চালু হয়েছে।")
    elif t=="🧑‍💻 Freelancer Mode": ctx.user_data["mode"]="worker"; await menu(update,ctx,"Freelancer Mode চালু হয়েছে।")
    elif t=="📤 মোড বদল": ctx.user_data["mode"]="worker" if mode=="buyer" else "buyer"; await menu(update,ctx,"মোড পরিবর্তন হয়েছে।")
    elif t=="🏠 মেইন মেনু": ctx.user_data["mode"]="main"; await menu(update,ctx,"মেইন মেনু।")
    elif t=="➕ কাজ পোস্ট" and mode=="buyer":
        rows=[[InlineKeyboardButton(f"{name} — মিনিমাম ৳{price}",callback_data=f"cat:{i}")] for i,(name,price) in enumerate(CATS)]
        await update.effective_message.reply_text("ক্যাটাগরি বেছে নিন। প্রতিটি ক্যাটাগরির মিনিমাম আলাদা; সর্বোচ্চ সীমা নেই।",reply_markup=InlineKeyboardMarkup(rows))
    elif t=="📋 আমার কাজ" and mode=="buyer":
        with conn() as c: rs=c.execute("SELECT * FROM jobs WHERE buyer_id=? ORDER BY id DESC LIMIT 20",(u.id,)).fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} {r['title']} | বাকি {r['count']} | ৳{m(r['price'])}/কাজ | {r['status']}" for r in rs]) or "আপনার কোনো কাজ নেই।")
    elif t=="💰 বায়ার ব্যালেন্স" and mode=="buyer": await balance(update,u.id,"buyer")
    elif t=="💰 ফ্রিল্যান্সার ব্যালেন্স" and mode=="worker": await balance(update,u.id,"worker")
    elif t=="➕ ডিপোজিট" and mode=="buyer":
        ctx.user_data["stage"]="dep_amount"; await update.effective_message.reply_text("ডিপোজিটের পরিমাণ লিখুন (সর্বনিম্ন ৳৫০)।")
    elif t=="🔎 কাজ দেখুন" and mode=="worker":
        with conn() as c: rs=c.execute("SELECT * FROM jobs WHERE status='open' AND count>0 ORDER BY id DESC LIMIT 10").fetchall()
        if not rs: await update.effective_message.reply_text("এখন কোনো খোলা কাজ নেই।")
        for r in rs:
            cat=CATS[int(r["cat"])][0] if 0 <= int(r["cat"]) < len(CATS) else "পুরোনো/বন্ধ ক্যাটাগরি"
            msg=f"কাজ #{r['id']} — {cat}\n{r['title']}\n{r['description']}\nপ্রতি কাজ ৳{m(r['price'])} | বাকি {r['count']}\nপ্রুফের ধরন: {r['proof_type']}\nনিয়ম/প্রুফ: {r['rules']}"
            await update.effective_message.reply_text(msg,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📨 প্রুফ জমা দিন",callback_data=f"submit:{r['id']}")]]))
    elif t=="📨 আমার সাবমিশন" and mode=="worker":
        with conn() as c: rs=c.execute("SELECT s.id,s.job_id,s.status,j.title FROM submissions s JOIN jobs j ON j.id=s.job_id WHERE s.worker_id=? ORDER BY s.id DESC LIMIT 20",(u.id,)).fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} কাজ #{r['job_id']} {r['title']} — {r['status']}" for r in rs]) or "কোনো সাবমিশন নেই।")
    elif t=="💸 উইথড্র" and mode=="worker":
        # Start with payment-method selection, as requested.
        ctx.user_data["stage"]="wd_method"
        ctx.user_data["form"]={}
        instructions = get_setting(
            "withdraw_instructions_text",
            "💸 টাকা উত্তোলন\n\nআপনার পছন্দের পেমেন্ট পদ্ধতি নির্বাচন করুন।\n\n🟣 বিকাশ — সর্বনিম্ন ৳১০০\n🟠 নগদ — সর্বনিম্ন ৳১০০\n📱 মোবাইল রিচার্জ — সর্বনিম্ন ৳৫০\n\nসার্ভিস চার্জ: ১৫%"
        )
        # Replace the old built-in instruction, but preserve any custom admin text.
        if "উইথড্রের পরিমাণ লিখুন" in instructions:
            instructions = "💸 টাকা উত্তোলন\n\nআপনার পছন্দের পেমেন্ট পদ্ধতি নির্বাচন করুন।\n\n🟣 বিকাশ — সর্বনিম্ন ৳১০০\n🟠 নগদ — সর্বনিম্ন ৳১০০\n📱 মোবাইল রিচার্জ — সর্বনিম্ন ৳৫০\n\nসার্ভিস চার্জ: ১৫%"
        await update.effective_message.reply_text(
            instructions,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟣 বিকাশ | মিনিমাম ৳১০০",callback_data="wdmethod:bKash")],
                [InlineKeyboardButton("🟠 নগদ | মিনিমাম ৳১০০",callback_data="wdmethod:Nagad")],
                [InlineKeyboardButton("📱 মোবাইল রিচার্জ | মিনিমাম ৳৫০",callback_data="wdmethod:Recharge")],
                [InlineKeyboardButton("❌ বাতিল",callback_data="wdconfirm:no")]
            ])
        )
    elif t=="👥 রেফার লিংক" and mode=="worker":
        me=await ctx.bot.get_me()
        with conn() as c:
            referred=c.execute("SELECT COUNT(*) AS total FROM users WHERE referrer=?",(u.id,)).fetchone()["total"]
            successful=c.execute("SELECT COUNT(*) AS total FROM users WHERE referrer=? AND ref_paid=1",(u.id,)).fetchone()["total"]
            bonus=c.execute("SELECT COALESCE(SUM(amount::numeric),0) AS total FROM ledger WHERE user_id=? AND kind='referral'",(u.id,)).fetchone()["total"]
        link=f"https://t.me/{me.username}?start=ref_{u.id}"
        template=get_setting("referral_text", "👥 আপনার রেফার লিংক:\n{link}\n\n💰 প্রথম অনুমোদিত উইথড্রয়ের নির্ধারিত শতাংশ একবার বোনাস পাবেন।")
        try: referral_message=template.format(link=link,count=referred,successful=successful,bonus=m(bonus))
        except (KeyError, ValueError): referral_message=f"👥 আপনার রেফার লিংক:\n{link}"
        await update.effective_message.reply_text(
            referral_message + f"\n\n✅ মোট রেফার: {referred} জন\n🌟 সফল রেফার: {successful} জন\n💰 মোট বোনাস: ৳{m(bonus)}"
        )
    elif t=="💳 পেমেন্ট তথ্য":
        await update.effective_message.reply_text(get_setting("payment_info_text", "পেমেন্ট তথ্য অ্যাডমিনের কাছ থেকে জেনে নিন।"))
    elif t=="📜 নিয়মাবলি":
        await update.effective_message.reply_text(get_setting("rules_text", "নিয়মাবলি পরে আপডেট করা হবে।"))
    elif t=="🆘 সাহায্য":
        await update.effective_message.reply_text(get_setting("support_text", "📩 Support Email: {email}\nআপনার Telegram ID: {user_id}").format(email=get_setting('support_email','mrshahidulislam8989@gmail.com'), user_id=u.id))
    else: await update.effective_message.reply_text("মেনুর বাটন ব্যবহার করুন।")
async def balance(update,uid,wallet):
    with conn() as c: r=c.execute(f"SELECT {wallet} FROM users WHERE id=?",(uid,)).fetchone()
    await update.effective_message.reply_text(f"আপনার ব্যালেন্স: ৳{m(r[wallet] or 0)}")
async def stage_input(update,ctx,st,t):
    uid=update.effective_user.id; msg=update.effective_message
    if st=="admin_setting":
        if uid!=ADMIN:
            ctx.user_data.pop("stage",None); ctx.user_data.pop("admin_setting_key",None)
            await msg.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        key=ctx.user_data.pop("admin_setting_key",None)
        if not key:
            ctx.user_data.pop("stage",None); await msg.reply_text("সেটিং পাওয়া যায়নি। /admin খুলুন।"); return
        if key in ("bkash_number","nagad_number"):
            compact=re.sub(r"[\s-]","",t)
            if not re.fullmatch(r"\+?\d{10,15}",compact):
                await msg.reply_text("সঠিক মোবাইল নম্বর দিন (১০–১৫টি সংখ্যা, প্রয়োজনে শুরুতে +)।")
                ctx.user_data["admin_setting_key"]=key
                return
            t=compact
        elif key=="support_email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",t):
            await msg.reply_text("সঠিক ইমেইল দিন।")
            ctx.user_data["admin_setting_key"]=key
            return
        elif (key.endswith("_text") or key in ("welcome_text", "payment_info_text", "rules_text", "support_text", "referral_text")) and not 2<=len(t)<=3500:
            await msg.reply_text("লেখা ২–৩৫০০ অক্ষরের মধ্যে লিখুন।")
            ctx.user_data["admin_setting_key"]=key
            return
        elif key.startswith("withdraw_") and not 5<=len(t)<=3500:
            await msg.reply_text("মেসেজ ৫–৩৫০০ অক্ষরের মধ্যে লিখুন।")
            ctx.user_data["admin_setting_key"]=key
            return
        set_setting(key,t,uid); ctx.user_data.pop("stage",None)
        await msg.reply_text("✅ সেটিং সেভ হয়েছে। পরিবর্তন দেখতে /admin লিখুন।")
    elif st=="job_title":
        if not 3<=len(t)<=100: await msg.reply_text("শিরোনাম ৩–১০০ অক্ষরের দিন।"); return
        ctx.user_data["form"]["title"]=t; ctx.user_data["stage"]="job_desc"; await msg.reply_text("কাজের বিস্তারিত লিখুন (১০–২০০০ অক্ষর)।")
    elif st=="job_desc":
        if not 10<=len(t)<=2000: await msg.reply_text("বিবরণ ১০–২০০০ অক্ষরের দিন।"); return
        ctx.user_data["form"]["desc"]=t; ctx.user_data["stage"]="job_count"; await msg.reply_text("কতজন কাজ করবে? পূর্ণসংখ্যা দিন।")
    elif st=="job_count":
        try: n=int(t); assert 1<=n<=100000
        except: await msg.reply_text("১–১০০০০০-এর মধ্যে পূর্ণসংখ্যা দিন।"); return
        ctx.user_data["form"]["count"]=n; ci=ctx.user_data["form"]["cat"]; name,mi=CATS[ci]; ctx.user_data["stage"]="job_price"
        await msg.reply_text(f"{name} ক্যাটাগরির সর্বনিম্ন ৳{mi}; প্রতি কাজের দাম ৳{mi} বা তার বেশি লিখুন। সর্বোচ্চ সীমা নেই।")
    elif st=="job_price":
        p=amount(t); f=ctx.user_data["form"]; mi=CATS[f["cat"]][1]
        if p is None or p<mi or p<2: await msg.reply_text(f"প্রতি কাজ কমপক্ষে ৳{mi} হতে হবে।"); return
        f["price"]=str(p); ctx.user_data["stage"]="job_rules"; await msg.reply_text("কাজের নিয়ম এবং কী প্রুফ দিতে হবে লিখুন।")
    elif st=="job_rules":
        if len(t)<3 or len(t)>2000: await msg.reply_text("৩–২০০০ অক্ষরের নিয়ম লিখুন।"); return
        f=ctx.user_data["form"]; f["rules"]=t; ctx.user_data["stage"]="job_proof_type"; await msg.reply_text("এই কাজের জন্য Freelancer কী প্রুফ জমা দেবে? নিচের একটি ধরন বেছে নিন।",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📸 Screenshot only",callback_data="proof_type:photo")],[InlineKeyboardButton("📝 Text only",callback_data="proof_type:text")],[InlineKeyboardButton("📸 + 📝 Screenshot and text",callback_data="proof_type:both")]])); return
        total=Decimal(f["price"])*f["count"]
        with conn() as c: r=c.execute("SELECT buyer FROM users WHERE id=?",(uid,)).fetchone()
        bal=Decimal(r["buyer"] or 0)
        if bal<total:
            await msg.reply_text(f"ব্যালেন্স কম। মোট বাজেট ৳{m(total)}, বর্তমান ৳{m(bal)}। আগে ডিপোজিট করুন।"); ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); return
        ctx.user_data["stage"]="job_confirm"
        await msg.reply_text(f"ক্যাটাগরি: {CATS[f['cat']][0]}\nকাজের ধরন: {f.get('task_type', '—')}\nশিরোনাম: {f['title']}\nবিবরণ: {f['desc']}\nকাজ: {f['count']}\nপ্রতি কাজ: ৳{m(f['price'])}\nমোট বাজেট: ৳{m(total)}\nনিয়ম: {t}\nপোস্ট করবেন?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ পোস্ট",callback_data="job:yes"),InlineKeyboardButton("❌ বাতিল",callback_data="job:no")]]))
    elif st=="dep_amount":
        a=amount(t)
        if a is None or a<50: await msg.reply_text("সর্বনিম্ন ডিপোজিট ৳৫০।"); return
        ctx.user_data["form"]={"amount":str(a)}; ctx.user_data["stage"]="dep_method"
        await msg.reply_text("ডিপোজিটের পদ্ধতি বেছে নিন:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟣 বিকাশ (bKash)",callback_data="depmethod:bKash"),InlineKeyboardButton("🟠 নগদ (Nagad)",callback_data="depmethod:Nagad")]]))
    elif st=="dep_method":
        aliases={"বিকাশ":"bKash","বিকাশ (bKash)":"bKash","নগদ":"Nagad","নগদ (Nagad)":"Nagad","bKash":"bKash","Nagad":"Nagad"}
        method=aliases.get(t)
        if method not in ("bKash","Nagad"):
            await msg.reply_text("নিচের বাটন থেকে বিকাশ অথবা নগদ বেছে নিন।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟣 বিকাশ (bKash)",callback_data="depmethod:bKash"),InlineKeyboardButton("🟠 নগদ (Nagad)",callback_data="depmethod:Nagad")]]))
            return
        ctx.user_data["form"]["method"]=method; ctx.user_data["stage"]="dep_txn"; await msg.reply_text("পেমেন্টের Transaction ID লিখুন।")
    elif st=="dep_txn":
        f=ctx.user_data["form"]
        try:
            with conn() as c: cur=c.execute("INSERT INTO deposits(user_id,amount,method,txn,created) VALUES(?,?,?,?,?)",(uid,f["amount"],f["method"],t,now())); did=cur.lastrowid
        except psycopg2.IntegrityError: await msg.reply_text("এই Txn ID আগে জমা হয়েছে।"); return
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await msg.reply_text("✅ আপনার ডিপোজিট অনুরোধ জমা হয়েছে। অ্যাডমিন Transaction ID যাচাই করে ব্যালেন্স যোগ করবেন।")
        await admin_msg(ctx,f"Deposit #{did}\nUser: {uid}\nAmount: ৳{f['amount']}\nMethod: {f['method']}\nTxn: {t}","deposit",did)
    elif st=="wd_gross":
        f=ctx.user_data.get("form") or {}
        method=f.get("method")
        minimum=Decimal("50") if method=="Recharge" else Decimal("100")
        g=amount(t)
        if g is None or g<minimum:
            await msg.reply_text(f"❌ {('মোবাইল রিচার্জ' if method=='Recharge' else 'বিকাশ/নগদ')} এর সর্বনিম্ন উত্তোলন ৳{m(minimum)}। অনুগ্রহ করে সঠিক পরিমাণ লিখুন।")
            return
        fee_rate=safe_rate(get_setting("withdraw_fee","0.15"), "0.15")
        fee=(g*fee_rate).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP); net=g-fee
        with conn() as c: r=c.execute("SELECT worker FROM users WHERE id=?",(uid,)).fetchone()
        if not r or Decimal(r["worker"] or 0)<g:
            await msg.reply_text(f"❌ পর্যাপ্ত ব্যালেন্স নেই.\nআপনার বর্তমান ব্যালেন্স: ৳{m(r['worker'] if r else 0)}\nঅনুরোধকৃত পরিমাণ: ৳{m(g)}")
            return
        f.update({"gross":str(g),"fee":str(fee),"net":str(net)})
        ctx.user_data["form"]=f; ctx.user_data["stage"]="wd_confirm"
        label={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[method]
        await msg.reply_text(
            "🧾 *উত্তোলনের হিসাব*\n\n"
            f"💳 পদ্ধতি: {label}\n"
            f"💰 মোট উত্তোলন: ৳{m(g)}\n"
            f"🧾 সার্ভিস চার্জ ({m(fee_rate*100)}%): ৳{m(fee)}\n"
            f"✅ আপনি পাবেন: ৳{m(net)}\n\n"
            "সব তথ্য ঠিক থাকলে *নিশ্চিত করুন* চাপুন।",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ নিশ্চিত করুন",callback_data="wdconfirm:yes"),InlineKeyboardButton("❌ বাতিল",callback_data="wdconfirm:no")]])
        )
    elif st=="wd_method":
        await msg.reply_text("অনুগ্রহ করে উপরের বাটন থেকে পেমেন্ট পদ্ধতি নির্বাচন করুন।")
    elif st=="wd_account":
        compact=re.sub(r"[\s-]", "", t)
        if not re.fullmatch(r"\+?\d{10,15}",compact):
            await msg.reply_text("❌ সঠিক মোবাইল/অ্যাকাউন্ট নম্বর লিখুন (১০–১৫টি সংখ্যা)।")
            return
        f=ctx.user_data.get("form") or {}
        if not all(k in f for k in ("gross","fee","net","method")):
            ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
            await msg.reply_text("⚠️ উত্তোলন ফর্মের মেয়াদ শেষ হয়েছে। আবার উত্তোলন শুরু করুন।")
            return
        g=Decimal(f["gross"])
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")
            r=c.execute("SELECT worker FROM users WHERE id=? FOR UPDATE",(uid,)).fetchone()
            bal=Decimal(r["worker"] or 0) if r else Decimal("0")
            if bal<g:
                c.rollback(); await msg.reply_text(f"❌ ব্যালেন্স যথেষ্ট নেই। বর্তমান ব্যালেন্স ৳{m(bal)}।"); return
            c.execute("UPDATE users SET worker=? WHERE id=?",(str(bal-g),uid))
            cur=c.execute("INSERT INTO withdrawals(user_id,gross,fee,net,method,account,created) VALUES(?,?,?,?,?,?,?)",(uid,f["gross"],f["fee"],f["net"],f["method"],compact,now())); wid=cur.lastrowid
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        label={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[f["method"]]
        await msg.reply_text(
            "🎉 *উত্তোলনের আবেদন সফলভাবে জমা হয়েছে!*\n\n"
            f"🆔 Request ID: `WD-{wid:06d}`\n"
            f"💳 পেমেন্ট পদ্ধতি: {label}\n"
            f"📱 অ্যাকাউন্ট নম্বর: `{compact}`\n"
            f"💰 উত্তোলনের পরিমাণ: ৳{m(f['gross'])}\n"
            f"🧾 সার্ভিস চার্জ: ৳{m(f['fee'])}\n"
            f"✅ প্রদেয় পরিমাণ: ৳{m(f['net'])}\n\n"
            "⏳ *স্ট্যাটাস: Pending / যাচাই অপেক্ষমাণ*\n"
            "অ্যাডমিন আপনার আবেদন যাচাই করে পেমেন্ট সম্পন্ন করবেন। অনুগ্রহ করে একই আবেদন পুনরায় জমা দেবেন না।\n\n"
            "— BABLU STAR BD",
            parse_mode="Markdown"
        )
        await admin_msg(ctx,
            f"📤 নতুন উত্তোলন আবেদন\n\nRequest: WD-{wid:06d}\nUser ID: {uid}\nMethod: {label}\nAccount: {compact}\nGross: ৳{m(f['gross'])}\nFee (15%): ৳{m(f['fee'])}\nNet Payable: ৳{m(f['net'])}\nStatus: Pending",
            "withdraw",wid)
    elif st=="proof_text":
        await save_proof(update,ctx,ctx.user_data.get("proof_job"),t,"")
    elif st=="proof_both_text":
        await save_proof(update,ctx,ctx.user_data.get("proof_job"),t,ctx.user_data.get("proof_file_id",""))
async def save_proof(update,ctx,jid,proof,fileid):
    uid=update.effective_user.id
    if is_banned(uid): await banned_notice(update.effective_message); return
    with conn() as c:
        job=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
        if not job or job["status"]!="open" or job["count"]<1: await update.effective_message.reply_text("কাজটি আর খোলা নেই।"); return
        if job["buyer_id"]==uid: await update.effective_message.reply_text("নিজের কাজের প্রুফ দিতে পারবেন না।"); return
        try: cur=c.execute("INSERT INTO submissions(job_id,worker_id,proof,file_id,created) VALUES(?,?,?,?,?)",(jid,uid,proof,fileid,now())); sid=cur.lastrowid
        except psycopg2.IntegrityError: await update.effective_message.reply_text("এই কাজের প্রুফ আগে জমা দিয়েছেন।"); return
    ctx.user_data.pop("stage",None); ctx.user_data.pop("proof_job",None); ctx.user_data.pop("proof_type",None); ctx.user_data.pop("proof_file_id",None)
    await update.effective_message.reply_text(f"প্রুফ জমা হয়েছে। সাবমিশন #{sid}; অ্যাডমিন যাচাই করবেন।")
    await admin_msg(ctx,f"Submission #{sid}\nJob #{jid}: {job['title']}\nWorker: {uid}\nProof: {proof or '[photo]'}","submission",sid)
    if fileid and ADMIN:
        try: await ctx.bot.send_photo(ADMIN,fileid,caption=f"প্রুফ Submission #{sid}")
        except Exception: pass
async def photo(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    row = user_row(update.effective_user)
    if row and row.get("banned") and update.effective_user.id != ADMIN:
        await banned_notice(update.effective_message); return
    st=ctx.user_data.get("stage")
    if st not in ("proof_photo","proof_both_photo"): await update.effective_message.reply_text("আগে কাজের প্রুফ জমা দিন বাটন চাপুন এবং প্রয়োজনীয় ধাপ অনুসরণ করুন।"); return
    fid=update.effective_message.photo[-1].file_id
    if st=="proof_both_photo":
        ctx.user_data["proof_file_id"]=fid; ctx.user_data["stage"]="proof_both_text"
        await update.effective_message.reply_text("✅ Screenshot পেয়েছি। এখন প্রমাণের Text বা Link লিখুন।")
    else: await save_proof(update,ctx,ctx.user_data.get("proof_job"),"Screenshot proof",fid)
async def admin_user_list_page(q, page=0):
    """Render 20 users per page with a total count and selectable user IDs."""
    page = max(0, int(page))
    per_page = 20
    with conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        rows = c.execute(
            "SELECT id, username, first_name, banned FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, page * per_page)
        ).fetchall()
    if total == 0:
        await q.message.edit_text("👥 User List\nএখনো কোনো User নেই।")
        return
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page >= total_pages:
        page = total_pages - 1
        with conn() as c:
            rows = c.execute(
                "SELECT id, username, first_name, banned FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
                (per_page, page * per_page)
            ).fetchall()
    lines = [
        "👥 **User List**",
        f"মোট User: {total} জন",
        f"পৃষ্ঠা: {page + 1}/{total_pages}",
        "নিচে যেকোনো User-এর ID/নামে ক্লিক করুন:",
    ]
    buttons = []
    for r in rows:
        username = "@" + r["username"] if r["username"] else "@—"
        name = r["first_name"] or "নাম নেই"
        status = "⛔" if r["banned"] else "🟢"
        buttons.append([InlineKeyboardButton(
            f"{status} {name[:14]} | {username[:14]} | {r['id']}",
            callback_data=f"adminusers:view:{r['id']}:{page}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"adminusers:page:{page-1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"adminusers:page:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔎 Search by ID / Username", callback_data="adminusers:search")])
    buttons.append([InlineKeyboardButton("🔙 Admin Panel", callback_data="adminusers:admin")])
    await q.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def admin_user_detail(q, target, page=0):
    """Show one selected user's balances, income, withdrawals and ban control."""
    with conn() as c:
        r = c.execute("""SELECT u.id,u.username,u.first_name,u.buyer,u.worker,u.banned,
            COALESCE((SELECT SUM(l.amount::numeric) FROM ledger l WHERE l.user_id=u.id AND l.kind IN ('earning','referral') AND l.amount::numeric>0),0) AS income,
            COALESCE((SELECT SUM(w.gross::numeric) FROM withdrawals w WHERE w.user_id=u.id AND w.status='approved'),0) AS withdrawn
            FROM users u WHERE u.id=?""", (target,)).fetchone()
    if not r:
        await q.message.edit_text("এই User-কে পাওয়া যায়নি।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 User List", callback_data=f"adminusers:page:{page}")]]))
        return
    username = "@" + r["username"] if r["username"] else "@—"
    text = (
        f"👤 User ID: {r['id']}\n"
        f"Username: {username}\n"
        f"নাম: {r['first_name'] or '—'}\n"
        f"Buyer Balance: ৳{m(r['buyer'] or 0)}\n"
        f"Freelancer Balance: ৳{m(r['worker'] or 0)}\n"
        f"মোট Income: ৳{m(r['income'])}\n"
        f"মোট Approved Withdrawal: ৳{m(r['withdrawn'])}\n"
        f"Status: {'BANNED ⛔' if r['banned'] else 'ACTIVE 🟢'}"
    )
    action = "Unban" if r["banned"] else "Ban"
    label = "✅ Unban User" if r["banned"] else "⛔ Ban User"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"adminusers:toggle:{target}:{page}")],
        [InlineKeyboardButton("🔙 User List", callback_data=f"adminusers:page:{page}")],
    ])
    await q.message.edit_text(text, reply_markup=markup)


async def callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    # Acknowledge immediately, before any database/network work, to avoid Telegram query expiry.
    try: await q.answer()
    except Exception: log.debug("Callback acknowledgement already expired or answered", exc_info=True)
    d=q.data or ""; uid=q.from_user.id; row=user_row(q.from_user)
    if row and row.get("banned") and d!="support": await banned_notice(q.message); return
    if d=="support":
        await q.message.reply_text(get_setting("support_text", "📩 Support Email: {email}\nআপনার Telegram ID: {user_id}").format(email=get_setting('support_email','mrshahidulislam8989@gmail.com'), user_id=uid)); return
    if d=="adminrefresh":
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        await q.message.reply_text("ড্যাশবোর্ড আপডেট করতে /admin লিখুন।")
    elif d.startswith("adminset:"):
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        key=d.split(":",1)[1]
        allowed={"bkash":"bkash_number","nagad":"nagad_number","support_email":"support_email","withdraw_request_sent":"withdraw_request_sent_text","withdraw_instructions":"withdraw_instructions_text","withdraw_pending":"withdraw_pending_text","withdraw_approved":"withdraw_approved_text","withdraw_rejected":"withdraw_rejected_text","welcome_text":"welcome_text","rules_text":"rules_text","payment_info_text":"payment_info_text","support_text":"support_text","referral_text":"referral_text"}
        if key not in allowed: return
        ctx.user_data["admin_setting_key"]=allowed[key]
        ctx.user_data["stage"]="admin_setting"
        labels={"bkash":"বিকাশের পেমেন্ট গ্রহণের নম্বর","nagad":"নগদের পেমেন্ট গ্রহণের নম্বর","support_email":"সাপোর্ট ইমেইল","withdraw_request_sent":"Withdrawal Request জমা দেওয়ার পর পাঠানোর মেসেজ","withdraw_instructions":"Withdrawal নির্দেশনা","withdraw_pending":"Withdrawal Pending মেসেজ","withdraw_approved":"Withdrawal Approved মেসেজ","withdraw_rejected":"Withdrawal Rejected মেসেজ","welcome_text":"Welcome / স্বাগতম মেসেজ","rules_text":"নিয়মাবলির লেখা","payment_info_text":"পেমেন্ট তথ্যের লেখা","support_text":"সাপোর্ট মেসেজ ( {email} ও {user_id} রাখুন )","referral_text":"রেফার মেসেজ ( {link} ও {count} রাখুন )"}
        await q.message.reply_text(f"{labels[key]} লিখুন। শুধু অ্যাডমিনের দেওয়া সঠিক তথ্য দিন। বাতিল করতে /cancel লিখুন।")
    elif d == "adminlist:users":
        if uid != ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        await admin_user_list_page(q, 0)
    elif d.startswith("adminusers:"):
        if uid != ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        parts = d.split(":")
        if len(parts) == 3 and parts[1] == "page":
            await admin_user_list_page(q, int(parts[2]))
        elif len(parts) == 2 and parts[1] == "admin":
            await q.message.reply_text("অ্যাডমিন প্যানেলে ফিরতে /admin লিখুন।")
        elif len(parts) == 2 and parts[1] == "search":
            ctx.user_data["admin_user_search"] = True
            await q.message.reply_text("যে User খুঁজবেন তার numeric ID অথবা Username লিখুন ( @ ছাড়া/সহ )।")
        elif len(parts) == 4 and parts[1] == "view":
            await admin_user_detail(q, int(parts[2]), int(parts[3]))
        elif len(parts) == 4 and parts[1] == "toggle":
            target, page = int(parts[2]), int(parts[3])
            if target == ADMIN:
                await q.message.reply_text("নিজের Admin ID Ban করা যাবে না।"); return
            with conn() as c:
                r = c.execute("SELECT id,username,banned FROM users WHERE id=?", (target,)).fetchone()
                if not r:
                    await q.message.reply_text("User পাওয়া যায়নি।"); return
                new_status = 0 if r["banned"] else 1
                c.execute("UPDATE users SET banned=? WHERE id=?", (new_status, target))
                c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",
                          (ADMIN, "user_unban" if not new_status else "user_ban", str(target), now()))
            try:
                await ctx.bot.send_message(target,
                    "✅ আপনার অ্যাকাউন্ট চালু করা হয়েছে। /start লিখুন।" if not new_status else
                    "⛔ আপনার অ্যাকাউন্ট বন্ধ করা হয়েছে। সহায়তার জন্য 🆘 সাহায্য ব্যবহার করুন।",
                    reply_markup=None if not new_status else support_only_kb())
            except Exception:
                log.info("Could not notify user %s about ban status change", target)
            await admin_user_detail(q, target, page)
    elif d.startswith("adminlist:"):
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        kind=d.split(":",1)[1]
        table={"deposit":"deposits","withdraw":"withdrawals","submission":"submissions"}.get(kind)
        if not table: return
        with conn() as c:
            rows=c.execute(f"SELECT * FROM {table} WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            await q.message.reply_text("এখন কোনো অপেক্ষমাণ অনুরোধ নেই।"); return
        for row in rows:
            if kind=="deposit":
                msg=f"ডিপোজিট #{row['id']}\nUser: {row['user_id']}\nAmount: ৳{row['amount']}\nMethod: {row['method']}\nTxn: {row['txn']}"
            elif kind=="withdraw":
                msg=f"উইথড্র #{row['id']}\nUser: {row['user_id']}\nGross: ৳{row['gross']}\nFee: ৳{row['fee']}\nNet: ৳{row['net']}\nMethod: {row['method']}\nAccount: {row['account']}"
            else:
                msg=f"সাবমিশন #{row['id']}\nJob: #{row['job_id']}\nWorker: {row['worker_id']}\nProof: {row['proof']}"
            markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve",callback_data=f"review:{kind}:yes:{row['id']}"),InlineKeyboardButton("❌ Reject",callback_data=f"review:{kind}:no:{row['id']}")]])
            await q.message.reply_text(msg,reply_markup=markup)
            if kind=="submission" and row.get("file_id"):
                try: await q.message.reply_photo(row["file_id"], caption=f"Submission #{row['id']} Screenshot")
                except Exception: log.exception("admin submission photo display failed")
    elif d == "adminjob:start":
        if uid != ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        ctx.user_data["admin_free_job"] = True
        ctx.user_data["form"] = {}
        ctx.user_data["stage"] = "job_category"
        rows=[]
        for i in range(0, len(CATS), 2):
            row=[InlineKeyboardButton(f"{CATS[i][0]} (৳{CATS[i][1]}+)",callback_data=f"cat:{i}")]
            if i+1 < len(CATS): row.append(InlineKeyboardButton(f"{CATS[i+1][0]} (৳{CATS[i+1][1]}+)",callback_data=f"cat:{i+1}"))
            rows.append(row)
        await q.message.reply_text("🛠 অ্যাডমিন ফ্রি জব পোস্ট\nপ্রথমে কাজের ক্যাটাগরি বেছে নিন। অ্যাডমিন পোস্টে কোনো বাজেট/পোস্ট ফি কাটা হবে না।",reply_markup=InlineKeyboardMarkup(rows))
    elif d.startswith("proof_type:"):
        if ctx.user_data.get("stage")!="job_proof_type" or not ctx.user_data.get("form"): await q.message.reply_text("ফর্মটি আর সক্রিয় নেই। আবার কাজ পোস্ট করুন।"); return
        pt=d.split(":",1)[1]
        if pt not in ("photo","text","both"): return
        f=ctx.user_data["form"]; f["proof_type"]=pt; total=Decimal(f["price"])*int(f["count"])
        if not ctx.user_data.get("admin_free_job"):
            with conn() as c: r=c.execute("SELECT buyer FROM users WHERE id=?",(uid,)).fetchone()
            bal=Decimal(r["buyer"] or 0)
            if bal<total: await q.message.reply_text(f"ব্যালেন্স কম। মোট বাজেট ৳{m(total)}, বর্তমান ৳{m(bal)}। পোস্ট ফি যোগ করে প্রয়োজনীয় ব্যালেন্স রাখুন।"); ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); return
        ctx.user_data["stage"]="job_confirm"
        label={"photo":"Screenshot only","text":"Text only","both":"Screenshot + Text"}[pt]
        await q.message.reply_text(f"ক্যাটাগরি: {CATS[f['cat']][0]}\nকাজের ধরন: {f.get('task_type', '—')}\nশিরোনাম: {f['title']}\nবিবরণ: {f['desc']}\nকাজ: {f['count']}\nপ্রতি কাজ: ৳{m(f['price'])}\nমোট বাজেট: ৳{m(total)}\nProof Type: {label}\nনিয়ম: {f['rules']}\nপোস্ট করবেন?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ পোস্ট",callback_data="job:yes"),InlineKeyboardButton("❌ বাতিল",callback_data="job:no")]]))
    elif d.startswith("cat:"):
        try: ci=int(d.split(":")[1]); name,mi=CATS[ci]
        except (ValueError, IndexError): return
        if ctx.user_data.get("admin_free_job") and uid != ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        ctx.user_data.setdefault("form", {})["cat"]=ci
        rows=[[InlineKeyboardButton(label, callback_data=f"tasktype:{i}")] for i,label in enumerate(TASK_TYPES)]
        await q.message.reply_text(f"প্ল্যাটফর্ম: {name}\nকাজের ধরন বেছে নিন। শুধু Like, Follow, Comment অথবা Repost করা যাবে।", reply_markup=InlineKeyboardMarkup(rows))
    elif d.startswith("tasktype:"):
        if not ctx.user_data.get("form") or "cat" not in ctx.user_data["form"]:
            await q.message.reply_text("কাজ পোস্টের ফর্মটি শেষ হয়েছে। আবার শুরু করুন।"); return
        try: ti=int(d.split(":")[1]); task=TASK_TYPES[ti]
        except (ValueError, IndexError): return
        ctx.user_data["form"]["task_type"] = task
        ctx.user_data["stage"] = "job_title"
        ci=ctx.user_data["form"]["cat"]
        await q.message.reply_text(f"{CATS[ci][0]} — {task}\nএখন কাজের শিরোনাম লিখুন।")
    elif d.startswith("wdmethod:"):
        if ctx.user_data.get("stage")!="wd_method":
            await q.message.reply_text("এই উত্তোলন ফর্মটি আর সক্রিয় নেই। আবার উত্তোলন শুরু করুন।"); return
        method=d.split(":",1)[1]
        if method not in ("bKash","Nagad","Recharge"): return
        ctx.user_data["form"]={"method":method}
        ctx.user_data["stage"]="wd_gross"
        label={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[method]
        minimum="৫০" if method=="Recharge" else "১০০"
        await q.message.reply_text(
            f"💳 নির্বাচিত পদ্ধতি: {label}\n📌 সর্বনিম্ন উত্তোলন: ৳{minimum}\n🧾 সার্ভিস চার্জ: ১৫%\n\n"
            "আপনি কত টাকা উত্তোলন করতে চান? পরিমাণ লিখুন।"
        )
    elif d.startswith("wdconfirm:"):
        action=d.split(":",1)[1]
        if action=="no":
            ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
            await q.message.reply_text("❎ উত্তোলনের আবেদন বাতিল করা হয়েছে। আপনার ব্যালেন্স থেকে কোনো টাকা কাটা হয়নি।")
            return
        if not ctx.user_data.get("form"):
            await q.message.reply_text("এই উত্তোলন ফর্মটি আর সক্রিয় নেই। আবার শুরু করুন।"); return
        if action!="yes" or ctx.user_data.get("stage")!="wd_confirm": return
        ctx.user_data["stage"]="wd_account"
        label={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[ctx.user_data["form"]["method"]]
        await q.message.reply_text(
            f"✅ উত্তোলনের হিসাব নিশ্চিত হয়েছে।\nপদ্ধতি: {label}\n\n"
            "এখন যে নম্বরে টাকা পাঠাতে হবে সেই মোবাইল/অ্যাকাউন্ট নম্বরটি লিখুন।"
        )
    elif d.startswith("depmethod:"):
        if ctx.user_data.get("stage")!="dep_method" or not ctx.user_data.get("form"):
            await q.message.reply_text("এই ডিপোজিট ফর্মটি আর সক্রিয় নেই। আবার ডিপোজিট শুরু করুন।"); return
        method=d.split(":",1)[1]
        if method not in ("bKash","Nagad"): return
        receiver=get_setting("bkash_number" if method=="bKash" else "nagad_number","")
        if not receiver:
            await q.message.reply_text("এই পেমেন্ট পদ্ধতির গ্রহণের নম্বর এখনো অ্যাডমিন সেট করেননি। পরে আবার চেষ্টা করুন বা অ্যাডমিনকে জানান।")
            ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
            return
        ctx.user_data["form"]["method"]=method; ctx.user_data["stage"]="dep_txn"
        await q.message.reply_text(f"পেমেন্ট পদ্ধতি: {'বিকাশ' if method=='bKash' else 'নগদ'}\nপেমেন্ট গ্রহণের নম্বর: {receiver}\nএই নম্বরে টাকা পাঠিয়ে Transaction ID লিখুন।")
    elif d=="job:no":
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); await q.message.reply_text("কাজ পোস্ট বাতিল।")
    elif d=="job:yes":
        if ctx.user_data.get("stage")!="job_confirm" or not ctx.user_data.get("form"): await q.message.reply_text("ফর্মটি আর সক্রিয় নেই।"); return
        f=ctx.user_data["form"]; total=Decimal(f["price"])*int(f["count"]); is_admin_free=bool(ctx.user_data.get("admin_free_job") and uid==ADMIN)
        job_fee_rate=safe_rate(get_setting("job_fee","0.05"), "0.05")
        post_fee=Decimal("0") if is_admin_free else (total*job_fee_rate).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        charge=Decimal("0") if is_admin_free else total+post_fee
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if not is_admin_free:
                r=c.execute("SELECT buyer FROM users WHERE id=? FOR UPDATE",(uid,)).fetchone(); bal=Decimal(r["buyer"] or 0)
                if bal<charge: c.rollback(); await q.message.reply_text(f"ব্যালেন্স কম। বাজেট ৳{m(total)} + ৫% পোস্ট ফি ৳{m(post_fee)} = মোট ৳{m(charge)} দরকার। আগে ডিপোজিট করুন।"); return
                c.execute("UPDATE users SET buyer=? WHERE id=?",(str(bal-charge),uid))
            cur=c.execute("INSERT INTO jobs(buyer_id,cat,title,description,count,price,reserved,rules,proof_type,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uid,f["cat"],f"{f.get('task_type', '')} — {f['title']}",f["desc"],f["count"],f["price"],str(total),f["rules"],f.get("proof_type","text"),"open",now())); jid=cur.lastrowid
            if not is_admin_free:
                c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(uid,"buyer",str(-total),"escrow",f"Job #{jid} budget",now()))
                if post_fee:
                    c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(uid,"buyer",str(-post_fee),"job_fee",f"Job #{jid} 5% posting fee",now()))
            c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(uid,"job_posted_admin_free" if is_admin_free else "job_posted_buyer",f"Job #{jid}; budget {m(total)}; fee {m(post_fee)}",now()))
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); ctx.user_data.pop("admin_free_job",None)
        if is_admin_free:
            await q.message.reply_text(f"✅ অ্যাডমিন হিসেবে কাজ #{jid} বিনা খরচে পোস্ট হয়েছে। কোনো ডিপোজিট বা পোস্ট ফি কাটা হয়নি।")
        else:
            await q.message.reply_text(f"✅ কাজ #{jid} পোস্ট হয়েছে। বাজেট ৳{m(total)}; ৫% পোস্ট ফি ৳{m(post_fee)}; মোট কাটা হয়েছে ৳{m(charge)}।")
        await admin_msg(ctx,f"New job #{jid}: {f['title']} | poster {uid} | budget ৳{m(total)} | admin_free={is_admin_free}")
    elif d.startswith("submit:"):
        jid=int(d.split(":")[1])
        with conn() as c:
            j=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone(); ex=c.execute("SELECT 1 FROM submissions WHERE job_id=? AND worker_id=?",(jid,uid)).fetchone()
        if not j or j["status"]!="open" or j["count"]<1: await q.message.reply_text("কাজটি আর খোলা নেই।")
        elif j["buyer_id"]==uid: await q.message.reply_text("নিজের কাজ করা যাবে না।")
        elif ex: await q.message.reply_text("এই কাজের প্রুফ আগে জমা দিয়েছেন।")
        else:
            ctx.user_data["proof_job"]=jid; ctx.user_data["proof_type"]=j["proof_type"] or "text"
            if ctx.user_data["proof_type"]=="photo":
                ctx.user_data["stage"]="proof_photo"; prompt="📸 শুধু Screenshot পাঠান।"
            elif ctx.user_data["proof_type"]=="both":
                ctx.user_data["stage"]="proof_both_photo"; prompt="📸 প্রথমে Screenshot পাঠান; এরপর Text/Link লিখবেন।"
            else:
                ctx.user_data["stage"]="proof_text"; prompt="📝 এখন Text বা Link লিখুন।"
            await q.message.reply_text(prompt+"\nএকটি কাজের জন্য একবারই সাবমিশন করা যাবে।")
    elif d.startswith("review:"):
        if uid!=ADMIN: await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        _,kind,decision,raw=d.split(":"); item=int(raw); yes=decision=="yes"
        if kind=="submission" and not yes:
            with conn() as c:
                pending=c.execute("SELECT id FROM submissions WHERE id=? AND status='pending'",(item,)).fetchone()
            if not pending:
                await q.message.reply_text("এই Submission আগেই Review করা হয়েছে।"); return
            ctx.user_data["pending_submission_rejection"] = item
            await q.message.reply_text(f"Submission #{item} Reject করার কারণ লিখুন (৩–১০০০ অক্ষর)। বাতিল করতে /cancel লিখুন।")
            return
        await review(q,ctx,kind,item,yes)
async def review(q,ctx,kind,item,yes,rejection_reason=""):
    target=0; usermsg=""
    decision = "yes" if yes else "no"
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if kind=="deposit":
            r=c.execute("SELECT * FROM deposits WHERE id=? FOR UPDATE",(item,)).fetchone()
            if not r or r["status"]!="pending": await q.message.reply_text("ইতিমধ্যে প্রসেস করা হয়েছে।"); return
            c.execute("UPDATE deposits SET status=?,reviewed=? WHERE id=? AND status='pending'",("approved" if yes else "rejected",now(),item))
            if yes:
                u=c.execute("SELECT buyer FROM users WHERE id=?",(r["user_id"],)).fetchone(); b=Decimal(u["buyer"] or 0)+Decimal(r["amount"]); c.execute("UPDATE users SET buyer=? WHERE id=?",(str(b),r["user_id"]))
                c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(r["user_id"],"buyer",r["amount"],"deposit",f"Deposit #{item}",now()))
            target=r["user_id"]; usermsg=f"ডিপোজিট #{item} {'অনুমোদিত' if yes else 'বাতিল'} হয়েছে।"
        elif kind=="withdraw":
            r=c.execute("SELECT * FROM withdrawals WHERE id=? FOR UPDATE",(item,)).fetchone()
            if not r or r["status"]!="pending": await q.message.reply_text("ইতিমধ্যে প্রসেস করা হয়েছে।"); return
            c.execute("UPDATE withdrawals SET status=?,reviewed=? WHERE id=? AND status='pending'",("approved" if yes else "rejected",now(),item))
            if not yes:
                u=c.execute("SELECT worker FROM users WHERE id=?",(r["user_id"],)).fetchone(); b=Decimal(u["worker"] or 0)+Decimal(r["gross"]); c.execute("UPDATE users SET worker=? WHERE id=?",(str(b),r["user_id"]))
            else:
                u=c.execute("SELECT referrer,ref_paid FROM users WHERE id=?",(r["user_id"],)).fetchone()
                if u and u["referrer"] and not u["ref_paid"]:
                    referral_rate=safe_rate(get_setting("referral_rate",str(REFERRAL)),str(REFERRAL))
                    bonus=(Decimal(r["gross"])*referral_rate).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                    ref=c.execute("SELECT worker FROM users WHERE id=?",(u["referrer"],)).fetchone()
                    if ref:
                        c.execute("UPDATE users SET worker=? WHERE id=?",(str(Decimal(ref["worker"] or 0)+bonus),u["referrer"]))
                        c.execute("UPDATE users SET ref_paid=1 WHERE id=?",(r["user_id"],))
                        c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(u["referrer"],"worker",str(bonus),"referral",f"Referral user {r['user_id']}",now()))
            target=r["user_id"]; usermsg=get_setting("withdraw_approved_text" if yes else "withdraw_rejected_text", "উইথড্র অনুমোদিত হয়েছে।" if yes else "উইথড্র বাতিল হয়েছে; টাকা ফেরত দেওয়া হয়েছে।")
        elif kind=="submission":
            r=c.execute("SELECT * FROM submissions WHERE id=? FOR UPDATE",(item,)).fetchone()
            if not r or r["status"]!="pending": await q.message.reply_text("ইতিমধ্যে প্রসেস করা হয়েছে।"); return
            j=c.execute("SELECT * FROM jobs WHERE id=? FOR UPDATE",(r["job_id"],)).fetchone()
            if not j: await q.message.reply_text("কাজ নেই।"); return
            if yes and j["count"]>0 and Decimal(j["reserved"])>=Decimal(j["price"]):
                pay=Decimal(j["price"]); u=c.execute("SELECT worker FROM users WHERE id=?",(r["worker_id"],)).fetchone()
                c.execute("UPDATE users SET worker=? WHERE id=?",(str(Decimal(u["worker"] or 0)+pay),r["worker_id"]))
                left=Decimal(j["reserved"])-pay; count=j["count"]-1
                c.execute("UPDATE jobs SET reserved=?,count=?,status=? WHERE id=?",(str(max(left,Decimal(0))),max(count,0),"completed" if count<=0 or left<=0 else "open",j["id"]))
                c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(r["worker_id"],"worker",str(pay),"earning",f"Job #{j['id']}",now()))
                status="approved"; usermsg=f"সাবমিশন #{item} অনুমোদিত। ৳{m(pay)} ব্যালেন্সে যোগ হয়েছে।"
            elif yes:
                status="rejected"; usermsg=f"সাবমিশন #{item} বাতিল: কাজের বাজেট শেষ।"
            else:
                status="rejected"
                usermsg=f"❌ সাবমিশন #{item} বাতিল হয়েছে।\nReject করার কারণ: {rejection_reason or 'কারণ উল্লেখ করা হয়নি'}"
            c.execute("UPDATE submissions SET status=?,reviewed=?,rejection_reason=? WHERE id=? AND status='pending'",(status,now(),rejection_reason if not yes else "",item)); target=r["worker_id"]
        else: await q.message.reply_text("অজানা রিভিউ।"); return
        c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(q.from_user.id,f"{kind}_{decision}",str(item),now()))
    await q.message.reply_text(f"{kind} #{item} সম্পন্ন।")
    try: await ctx.bot.send_message(target,usermsg)
    except Exception: pass
async def review_from_reason(update, ctx, sid, reason):
    class _Query:
        def __init__(self, message, user):
            self.message = message
            self.from_user = user
    await review(_Query(update.effective_message, update.effective_user), ctx, "submission", sid, False, reason)


def get_setting(key, default=""):
    with conn() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return r["value"] if r else default

def set_setting(key, value, actor):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
        c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(actor,"setting_update",key,now()))

async def ban_cmd(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN: await update.effective_message.reply_text("শুধু অ্যাডমিনের জন্য।"); return
    if len(ctx.args)!=1 or not ctx.args[0].isdigit(): await update.effective_message.reply_text("ব্যবহার: /ban USER_ID"); return
    target=int(ctx.args[0])
    with conn() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?",(target,)).fetchone(): await update.effective_message.reply_text("ইউজার পাওয়া যায়নি।"); return
        c.execute("UPDATE users SET banned=1 WHERE id=?",(target,))
        c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(ADMIN,"user_ban",str(target),now()))
    await update.effective_message.reply_text(f"⛔ User {target} Ban করা হয়েছে।")
    try: await ctx.bot.send_message(target,"⛔ আপনার অ্যাকাউন্ট বন্ধ করা হয়েছে। সহায়তার জন্য 🆘 সাহায্য বাটন ব্যবহার করুন।",reply_markup=support_only_kb())
    except Exception: pass
async def unban_cmd(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN: await update.effective_message.reply_text("শুধু অ্যাডমিনের জন্য।"); return
    if len(ctx.args)!=1 or not ctx.args[0].isdigit(): await update.effective_message.reply_text("ব্যবহার: /unban USER_ID"); return
    target=int(ctx.args[0])
    with conn() as c:
        cur=c.execute("UPDATE users SET banned=0 WHERE id=?",(target,))
        if not cur.rowcount: await update.effective_message.reply_text("ইউজার পাওয়া যায়নি।"); return
        c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(ADMIN,"user_unban",str(target),now()))
    await update.effective_message.reply_text(f"✅ User {target} Unban করা হয়েছে।")
    try: await ctx.bot.send_message(target,"✅ আপনার অ্যাকাউন্ট চালু করা হয়েছে। /start লিখে আবার শুরু করুন.")
    except Exception: pass
async def admin(update,ctx):
    if update.effective_user.id!=ADMIN:
        await update.effective_message.reply_text("শুধু অ্যাডমিনের জন্য।")
        return
    with conn() as c:
        d=c.execute("SELECT COUNT(*) n FROM deposits WHERE status='pending'").fetchone()["n"]
        w=c.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
        sub=c.execute("SELECT COUNT(*) n FROM submissions WHERE status='pending'").fetchone()["n"]
        users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        jobs=c.execute("SELECT COUNT(*) n FROM jobs WHERE status='open'").fetchone()["n"]
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ফ্রিতে কাজ পোস্ট",callback_data="adminjob:start")],
        [InlineKeyboardButton("💳 বিকাশ নম্বর সেট",callback_data="adminset:bkash"),InlineKeyboardButton("💳 নগদ নম্বর সেট",callback_data="adminset:nagad")],
        [InlineKeyboardButton("📧 সাপোর্ট ইমেইল",callback_data="adminset:support_email")],
        [InlineKeyboardButton("✏️ Welcome / স্বাগতম",callback_data="adminset:welcome_text"),InlineKeyboardButton("✏️ Rules / নিয়ম",callback_data="adminset:rules_text")],
        [InlineKeyboardButton("✏️ Payment Info",callback_data="adminset:payment_info_text"),InlineKeyboardButton("✏️ Support Text",callback_data="adminset:support_text")],
        [InlineKeyboardButton("✏️ Referral Text",callback_data="adminset:referral_text")],
        [InlineKeyboardButton("✏️ Withdrawal Request Sent",callback_data="adminset:withdraw_request_sent")],
        [InlineKeyboardButton("✏️ Withdrawal Instructions",callback_data="adminset:withdraw_instructions"),InlineKeyboardButton("✏️ Pending",callback_data="adminset:withdraw_pending")],
        [InlineKeyboardButton("✏️ Approved",callback_data="adminset:withdraw_approved"),InlineKeyboardButton("✏️ Rejected",callback_data="adminset:withdraw_rejected")],
        [InlineKeyboardButton("📥 ডিপোজিট যাচাই",callback_data="adminlist:deposit"),InlineKeyboardButton("📤 উত্তোলন যাচাই",callback_data="adminlist:withdraw")],
        [InlineKeyboardButton("🧾 কাজের প্রুফ যাচাই",callback_data="adminlist:submission"),InlineKeyboardButton("👥 User List + Balance/Income",callback_data="adminlist:users")],
        [InlineKeyboardButton("🔄 রিফ্রেশ",callback_data="adminrefresh")]
    ])
    await update.effective_message.reply_text(
        "🛠 BABLU STAR BD — অ্যাডমিন প্যানেল\n\n"
        f"👥 মোট ব্যবহারকারী: {users}\n📋 চালু কাজ: {jobs}\n"
        f"📥 অপেক্ষমাণ ডিপোজিট: {d}\n📤 অপেক্ষমাণ উত্তোলন: {w}\n🧾 অপেক্ষমাণ কাজের প্রুফ: {sub}\n\n"
        f"বিকাশ: {get_setting('bkash_number','সেট করা হয়নি')}\n"
        f"নগদ: {get_setting('nagad_number','সেট করা হয়নি')}\n"
        f"Support: {get_setting('support_email','mrshahidulislam8989@gmail.com')}\n\n"
        "নম্বর/ইমেইল/Withdrawal মেসেজ পরিবর্তন করতে নিচের বাটনে চাপ দিন।", reply_markup=kb)

async def cancel_cmd(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id): await banned_notice(update.effective_message); return
    await menu(update,ctx,"চলমান কাজ বাতিল হয়েছে।")
async def err(update,ctx):
    log.error("Unhandled Telegram update error", exc_info=(type(ctx.error), ctx.error, ctx.error.__traceback__) if ctx.error else None)
    if update and update.effective_message:
        try: await update.effective_message.reply_text("সমস্যা হয়েছে। আবার চেষ্টা করুন।")
        except: pass
async def close_db_pool(app):
    global DB_POOL
    if DB_POOL is not None:
        DB_POOL.closeall()
        DB_POOL = None
DB_POOL_LOCK = threading.Lock()

def main():
    if not TOKEN: raise SystemExit("Hostless Secrets-এ BOT_TOKEN সেট করুন।")
    if not ADMIN: raise SystemExit("Hostless Secrets-এ ADMIN_ID (numeric Telegram ID) সেট করুন।")
    if not DATABASE_URL: raise SystemExit("Hostless Secrets-এ DATABASE_URL (Neon PostgreSQL) সেট করুন।")
    init(); app=Application.builder().token(TOKEN).post_shutdown(close_db_pool).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("admin",admin)); app.add_handler(CommandHandler("ban",ban_cmd)); app.add_handler(CommandHandler("unban",unban_cmd))
    app.add_handler(CommandHandler("cancel",cancel_cmd))
    app.add_handler(CallbackQueryHandler(callback)); app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text)); app.add_error_handler(err)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__=="__main__": main()
