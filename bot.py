import os, sqlite3, logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN = int(os.getenv("ADMIN_ID", "0") or "0")
DB = os.getenv("DB_PATH", "bablu_star_bd.sqlite3")
FEE = Decimal("0.15")
REFERRAL = Decimal("0.10")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bablu-star-bd")

# শুধুমাত্র নির্ধারিত ৮টি সোশ্যাল প্ল্যাটফর্ম; পুরোনো ৩০টি জেনেরিক ক্যাটাগরি বাদ।
# এই বেসলাইন ফ্লোতে প্ল্যাটফর্মভিত্তিক সাধারণ মিনিমাম ৳২ রাখা হয়েছে।
PLATFORMS = [
    ("📘 Facebook", ["Like", "Follow", "Comment"], 3),
    ("▶️ YouTube", ["Like", "Subscriber", "Comment"], 3),
    ("📸 Instagram", ["Like", "Follow", "Comment"], 4),
    ("🎵 TikTok", ["Like", "Follow", "Comment"], 3),
    ("𝕏 X", ["Like", "Follow", "Comment", "Repost"], 4),
    ("👻 Snapchat", ["Like", "Follow", "Comment"], 3),
    ("✈️ Telegram", ["Channel Join", "Bot Start", "Group Join"], None),
    ("💼 LinkedIn", ["Like", "Follow", "Comment"], 4),
]
# প্রতি একক কাজের ন্যূনতম দাম; একসাথে সম্পূর্ণ নির্ধারিত bundle বাছলে bundle minimum প্রযোজ্য।
TASK_MINIMUMS = {"Like": 2, "Follow": 2, "Comment": 2, "Subscriber": 2,
                 "Repost": 2, "Channel Join": 2, "Bot Start": 3, "Group Join": 2}
BUNDLE_MINIMUMS = {"Facebook": 3, "YouTube": 3, "Instagram": 4, "TikTok": 3,
                   "Snapchat": 3, "LinkedIn": 4}
CATS = [(p[0], 2) for p in PLATFORMS]


BUYER = [["➕ কাজ পোস্ট","📋 আমার কাজ"],["💰 বায়ার ব্যালেন্স","➕ ডিপোজিট"],["📤 মোড বদল","🏠 মেইন মেনু"],["🔙 Back / ❌ Cancel"]]
WORKER = [["🔎 কাজ দেখুন","📨 আমার সাবমিশন"],["💰 ফ্রিল্যান্সার ব্যালেন্স","💸 উইথড্র"],["👥 রেফার লিংক","📤 মোড বদল"],["🏠 মেইন মেনু","🔙 Back / ❌ Cancel"]]
MAIN = [["🛒 Buyer Mode","🧑‍💻 Freelancer Mode"],["💳 পেমেন্ট তথ্য","📜 নিয়মাবলি"],["🆘 সাহায্য","🔙 Back / ❌ Cancel"]]

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def m(v):
    d=Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"
def amount(s):
    try:
        x=Decimal(str(s).replace(",","").replace("৳","").strip())
        return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if x.is_finite() and x>0 else None
    except (InvalidOperation,ValueError): return None
def conn():
    c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA journal_mode=WAL"); return c
def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,buyer TEXT DEFAULT '0',worker TEXT DEFAULT '0',referrer INTEGER,ref_paid INTEGER DEFAULT 0,banned INTEGER DEFAULT 0,created TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,buyer_id INTEGER,cat INTEGER,title TEXT,description TEXT,count INTEGER,price TEXT,reserved TEXT,rules TEXT,status TEXT DEFAULT 'open',created TEXT);
        CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,worker_id INTEGER,proof TEXT,file_id TEXT DEFAULT '',status TEXT DEFAULT 'pending',created TEXT,reviewed TEXT,UNIQUE(job_id,worker_id));
        CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,amount TEXT,method TEXT,txn TEXT UNIQUE,status TEXT DEFAULT 'pending',created TEXT,reviewed TEXT);
        CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,gross TEXT,fee TEXT,net TEXT,method TEXT,account TEXT,status TEXT DEFAULT 'pending',created TEXT,reviewed TEXT);
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,wallet TEXT,amount TEXT,kind TEXT,note TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor INTEGER,action TEXT,details TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        # Safe additive migrations for existing installations.
        cols={r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        # Additive-only migrations: existing rows and transaction history remain intact.
        for col, decl in (("ban_reason", "TEXT DEFAULT ''"), ("buyer", "TEXT DEFAULT '0'"), ("worker", "TEXT DEFAULT '0'"), ("device_info", "TEXT DEFAULT ''")):
            if col not in cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        jcols={r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
        if "is_admin_job" not in jcols: c.execute("ALTER TABLE jobs ADD COLUMN is_admin_job INTEGER DEFAULT 0")
DEFAULT_TEXTS = {
    "welcome": "🌟 BABLU STAR BD-তে স্বাগতম! 🌟\n\n💼 আপনার কাজ, আপনার উপার্জন!\n🛒 Buyer Mode — নিজের কাজ পোস্ট করুন\n🧑‍💻 Freelancer Mode — কাজ করুন ও আয় করুন\n\n👇 শুরু করতে নিচের একটি মোড বেছে নিন।",
    "support_email": "mrshahidulislam8989@gmail.com",
    "rules": "• কাজের দাম ক্যাটাগরি মিনিমামের কম নয়।\n• ভুয়া অ্যাকাউন্ট, প্রতারণা, স্প্যাম বা প্ল্যাটফর্মের নিয়মভঙ্গকারী কাজ নিষিদ্ধ।\n• বাজেট আগে বায়ার ব্যালেন্স থেকে সংরক্ষিত হবে।\n• প্রুফ অ্যাডমিন অনুমোদন করলে ফ্রিল্যান্সার টাকা পাবেন।\n• ডিপোজিট/উইথড্র ম্যানুয়ালি যাচাই হয়।",
    "payment_info": "bKash/Nagad ডিপোজিট Txn ID দিয়ে জমা দিন। অ্যাডমিন যাচাইয়ের পর ব্যালেন্স যোগ হবে।",
    "banned_text": "⛔ আপনার অ্যাকাউন্ট সীমাবদ্ধ করা হয়েছে। কারণ: {reason}",
    "deposit_min": "50", "withdraw_min_usd": "1", "recharge_min_usd": "0.50", "withdraw_min": "1", "recharge_min": "0.50",
    "withdraw_fee": "15", "recharge_fee": "15", "post_fee": "5", "referral_bonus": "10", "usd_rate": "100",
    "bkash_number": "পরিবর্তন করা হয়নি", "nagad_number": "পরিবর্তন করা হয়নি",
    "support_url": "https://t.me/"
}
def get_setting(key):
    with conn() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return r["value"] if r else DEFAULT_TEXTS.get(key, "")
def set_setting(key,value):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
def is_admin(uid): return bool(ADMIN and uid==ADMIN)

def user_row(u):
    with conn() as c:
        r=c.execute("SELECT * FROM users WHERE id=?",(u.id,)).fetchone()
        if not r:
            c.execute("INSERT INTO users(id,username,first_name,created) VALUES(?,?,?,?)",(u.id,u.username or "",u.first_name or "",now()))
        else: c.execute("UPDATE users SET username=?,first_name=? WHERE id=?",(u.username or "",u.first_name or "",u.id))
def kb(mode):
    return ReplyKeyboardMarkup(BUYER if mode=="buyer" else WORKER if mode=="worker" else MAIN,resize_keyboard=True)
async def menu(update,ctx,msg="মেনু থেকে অপশন বেছে নিন।"):
    for k in ("stage","form","proof_job"): ctx.user_data.pop(k,None)
    target=update.effective_message or update.callback_query.message
    await target.reply_text(msg,reply_markup=kb(ctx.user_data.get("mode","main")))
async def admin_msg(ctx,text,kind=None,item=None):
    if not ADMIN: return
    markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve",callback_data=f"review:{kind}:yes:{item}"),InlineKeyboardButton("❌ Reject",callback_data=f"review:{kind}:no:{item}")]]) if kind else None
    try: await ctx.bot.send_message(ADMIN,text,reply_markup=markup)
    except Exception: log.exception("admin notification failed")
async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; user_row(u)
    with conn() as c: br=c.execute("SELECT banned,ban_reason FROM users WHERE id=?",(u.id,)).fetchone()
    if br and br["banned"] and not is_admin(u.id):
        await update.effective_message.reply_text(get_setting("banned_text").format(reason=br["ban_reason"] or "অ্যাডমিনের সিদ্ধান্ত"),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support",url=get_setting("support_url"))]])); return
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref=int(ctx.args[0][4:])
            if ref!=u.id:
                with conn() as c:
                    r=c.execute("SELECT referrer FROM users WHERE id=?",(u.id,)).fetchone()
                    if r and r["referrer"] is None and c.execute("SELECT 1 FROM users WHERE id=?",(ref,)).fetchone():
                        c.execute("UPDATE users SET referrer=? WHERE id=?",(ref,u.id))
        except ValueError: pass
    ctx.user_data["mode"]="main"
    await menu(
        update,
        ctx,
        get_setting("welcome")
    )
async def text(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; user_row(u); t=(update.effective_message.text or "").strip()
    with conn() as c:
        r=c.execute("SELECT banned FROM users WHERE id=?",(u.id,)).fetchone()
    if r and r["banned"]:
        with conn() as c: br=c.execute("SELECT ban_reason FROM users WHERE id=?",(u.id,)).fetchone()
        await update.effective_message.reply_text(get_setting("banned_text").format(reason=(br["ban_reason"] if br else "") or "অ্যাডমিনের সিদ্ধান্ত"),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support",url=get_setting("support_url"))]])); return
    if t in ("🔙 Back / ❌ Cancel","🔙 Back","❌ Cancel","/cancel"):
        await menu(update,ctx,"চলমান কাজ বাতিল হয়েছে।"); return
    if is_admin(u.id) and ctx.user_data.pop("admin_user_search",False):
        term=t.lstrip("@").strip()
        with conn() as c:
            if term.isdigit(): rs=c.execute("SELECT id,username,first_name,banned FROM users WHERE id=?",(int(term),)).fetchall()
            else: rs=c.execute("SELECT id,username,first_name,banned FROM users WHERE username LIKE ? ORDER BY id DESC LIMIT 20",("%"+term+"%",)).fetchall()
        if not rs: await update.effective_message.reply_text("কোনো ইউজার পাওয়া যায়নি।"); return
        rows=[[InlineKeyboardButton(f"{r['id']} | @{r['username'] or '—'} | {'BANNED' if r['banned'] else 'ACTIVE'}",callback_data=f"adm:user:{r['id']}")] for r in rs]
        await update.effective_message.reply_text("🔎 Search Results:",reply_markup=InlineKeyboardMarkup(rows)); return
    if is_admin(u.id) and ctx.user_data.get("ban_target"):
        target=ctx.user_data.pop("ban_target")
        with conn() as c: c.execute("UPDATE users SET banned=1,ban_reason=? WHERE id=?",(t[:500],target))
        await update.effective_message.reply_text(f"🚫 User {target} Ban করা হয়েছে। কারণ: {t[:500]}")
        try: await ctx.bot.send_message(target,"⛔ আপনার অ্যাকাউন্ট সীমাবদ্ধ করা হয়েছে।\nকারণ: "+t[:500],reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support",url=get_setting("support_url"))]]))
        except Exception: pass
        return
    if is_admin(u.id) and ctx.user_data.get("admin_edit_key"):
        key=ctx.user_data.pop("admin_edit_key")
        if len(t)>4000: await update.effective_message.reply_text("সর্বোচ্চ ৪০০০ অক্ষর দিন।"); ctx.user_data["admin_edit_key"]=key; return
        set_setting(key,t)
        with conn() as c: c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(u.id,"setting_edit",key,now()))
        await update.effective_message.reply_text(f"✅ {key} পরিবর্তন সংরক্ষিত হয়েছে।")
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
        await update.effective_message.reply_text("শুধুমাত্র এই ৮টি প্ল্যাটফর্ম থেকে একটি বেছে নিন। অন্য কোনো ক্যাটাগরি নেই।",reply_markup=InlineKeyboardMarkup(rows))
    elif t=="📋 আমার কাজ" and mode=="buyer":
        with conn() as c: rs=c.execute("SELECT * FROM jobs WHERE buyer_id=? ORDER BY id DESC LIMIT 20",(u.id,)).fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} {r['title']} | বাকি {r['count']} | ৳{m(r['price'])}/কাজ | {r['status']}" for r in rs]) or "আপনার কোনো কাজ নেই।")
    elif t=="💰 বায়ার ব্যালেন্স" and mode=="buyer": await balance(update,u.id,"buyer")
    elif t=="💰 ফ্রিল্যান্সার ব্যালেন্স" and mode=="worker": await balance(update,u.id,"worker")
    elif t=="➕ ডিপোজিট" and mode=="buyer":
        ctx.user_data["stage"]="dep_amount"; await update.effective_message.reply_text(f"💳 ডিপোজিটের পরিমাণ লিখুন (সর্বনিম্ন ৳{get_setting('deposit_min')})।")
    elif t=="🔎 কাজ দেখুন" and mode=="worker":
        with conn() as c: rs=c.execute("SELECT * FROM jobs WHERE status='open' AND count>0 ORDER BY id DESC LIMIT 10").fetchall()
        if not rs: await update.effective_message.reply_text("এখন কোনো খোলা কাজ নেই।")
        for r in rs:
            cat=CATS[int(r["cat"])][0] if 0 <= int(r["cat"]) < len(CATS) else "অজানা প্ল্যাটফর্ম"
            msg=f"কাজ #{r['id']} — {cat}\n{r['title']}\n{r['description']}\nপ্রতি কাজ ৳{m(r['price'])} | বাকি {r['count']}\nনিয়ম/প্রুফ: {r['rules']}"
            await update.effective_message.reply_text(msg,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📨 প্রুফ জমা দিন",callback_data=f"submit:{r['id']}")]]))
    elif t=="📨 আমার সাবমিশন" and mode=="worker":
        with conn() as c: rs=c.execute("SELECT s.id,s.job_id,s.status,j.title FROM submissions s JOIN jobs j ON j.id=s.job_id WHERE s.worker_id=? ORDER BY s.id DESC LIMIT 20",(u.id,)).fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} কাজ #{r['job_id']} {r['title']} — {r['status']}" for r in rs]) or "কোনো সাবমিশন নেই।")
    elif t=="💸 উইথড্র" and mode=="worker":
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await update.effective_message.reply_text(
            "💸 উইথড্র / পেমেন্ট পদ্ধতি বেছে নিন:\n\n"
            "১) বিকাশ — সর্বনিম্ন ৳১০০, ফি ১৫%\n"
            "২) নগদ — সর্বনিম্ন ৳১০০, ফি ১৫%\n"
            "৩) মোবাইল রিচার্জ — সর্বনিম্ন ৳৫০, ফি ১৫%",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 বিকাশ — মিনিমাম ৳১০০",callback_data="wdmethod:bkash")],
                [InlineKeyboardButton("💚 নগদ — মিনিমাম ৳১০০",callback_data="wdmethod:nagad")],
                [InlineKeyboardButton("📞 মোবাইল রিচার্জ — মিনিমাম ৳৫০",callback_data="wdmethod:recharge")]
            ])
        )
    elif t=="👥 রেফার লিংক" and mode=="worker":
        me=await ctx.bot.get_me(); await update.effective_message.reply_text(f"https://t.me/{me.username}?start=ref_{u.id}\nরেফার করা ব্যক্তির প্রথম অনুমোদিত উইথড্রয়ের মোট অঙ্কের ১০% একবার বোনাস।")
    elif t=="💳 পেমেন্ট তথ্য":
        await update.effective_message.reply_text("bKash/Nagad ডিপোজিট Txn ID দিয়ে জমা দিন। অ্যাডমিন যাচাইয়ের পর ব্যালেন্স যোগ হবে। উইথড্র/মোবাইল রিচার্জে ১৫% ফি।")
    elif t=="📜 নিয়মাবলি":
        await update.effective_message.reply_text(get_setting("rules"))
    elif t=="🆘 সাহায্য":
        await update.effective_message.reply_text(
            "📩 BABLU STAR BD — Support Center\n\n"
            "আপনার কোনো সমস্যা বা প্রশ্ন থাকলে আমাদের ইমেইল করুন।\n\n"
            "✉️ Support Email:\n"
            f"{get_setting('support_email')}\n\n"
            f"🆔 আপনার Telegram ID: {u.id}\n\n"
            "সমস্যার বিস্তারিত লিখে ইমেইল পাঠান।\n"
            "ধন্যবাদ — BABLU STAR BD"
        )
    else: await update.effective_message.reply_text("মেনুর বাটন ব্যবহার করুন।")
async def balance(update,uid,wallet):
    with conn() as c: r=c.execute(f"SELECT {wallet} FROM users WHERE id=?",(uid,)).fetchone()
    await update.effective_message.reply_text(f"আপনার ব্যালেন্স: ৳{m(r[wallet] or 0)}")
async def stage_input(update,ctx,st,t):
    uid=update.effective_user.id; msg=update.effective_message
    if st=="job_title":
        if not 3<=len(t)<=100: await msg.reply_text("শিরোনাম ৩–১০০ অক্ষরের দিন।"); return
        ctx.user_data["form"]["title"]=t; ctx.user_data["stage"]="job_desc"; await msg.reply_text("কাজের বিস্তারিত লিখুন (১০–২০০০ অক্ষর)।")
    elif st=="job_desc":
        if not 10<=len(t)<=2000: await msg.reply_text("বিবরণ ১০–২০০০ অক্ষরের দিন।"); return
        ctx.user_data["form"]["desc"]=t; ctx.user_data["stage"]="job_count"; await msg.reply_text("কতজন কাজ করবে? পূর্ণসংখ্যা দিন।")
    elif st=="job_count":
        try: n=int(t); assert 1<=n<=100000
        except: await msg.reply_text("১–১০০০০০-এর মধ্যে পূর্ণসংখ্যা দিন।"); return
        ctx.user_data["form"]["count"]=n; ci=ctx.user_data["form"]["cat"]
        name, tasks, bundle = PLATFORMS[ci]
        ctx.user_data["stage"]="job_price"
        selected=ctx.user_data["form"].get("tasks", [])
        if len(selected)==len(tasks) and bundle is not None:
            mi=Decimal(str(bundle))
        elif ci==4 and len(selected)==3:
            mi=Decimal("4")
        else:
            mi=max(Decimal(str(TASK_MINIMUMS[x])) for x in selected)
        ctx.user_data["form"]["min_price"]=str(mi)
        await msg.reply_text(f"{name} / {', '.join(selected)}\nএই কাজের প্রতি সম্পন্ন টাস্কের সর্বনিম্ন দাম ৳{m(mi)}। এর কম দেওয়া যাবে না; বেশি দিতে পারবেন।")
    elif st=="job_price":
        p=amount(t); f=ctx.user_data["form"]; mi=Decimal(f.get("min_price", "2"))
        if p is None or p<mi or p<2: await msg.reply_text(f"প্রতি কাজ কমপক্ষে ৳{mi} হতে হবে।"); return
        f["price"]=str(p); ctx.user_data["stage"]="job_rules"; await msg.reply_text("কাজের নিয়ম এবং কী প্রুফ দিতে হবে লিখুন।")
    elif st=="job_rules":
        if len(t)<3 or len(t)>2000: await msg.reply_text("৩–২০০০ অক্ষরের নিয়ম লিখুন।"); return
        f=ctx.user_data["form"]; f["rules"]=t; total=Decimal(f["price"])*f["count"]
        with conn() as c: r=c.execute("SELECT buyer FROM users WHERE id=?",(uid,)).fetchone()
        bal=Decimal(r["buyer"] or 0)
        if not is_admin(uid) and bal<total:
            await msg.reply_text(f"ব্যালেন্স কম। মোট বাজেট ৳{m(total)}, বর্তমান ৳{m(bal)}। আগে ডিপোজিট করুন।"); ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); return
        ctx.user_data["stage"]="job_confirm"
        await msg.reply_text(f"প্ল্যাটফর্ম: {CATS[f['cat']][0]}\nকাজের ধরন: {f.get('task_label', '—')}\nশিরোনাম: {f['title']}\nবিবরণ: {f['desc']}\nকাজ: {f['count']}\nপ্রতি কাজ: ৳{m(f['price'])}\nমোট বাজেট: ৳{m(total)}\nনিয়ম: {t}\nপোস্ট করবেন?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ পোস্ট",callback_data="job:yes"),InlineKeyboardButton("❌ বাতিল",callback_data="job:no")]]))
    elif st=="dep_amount":
        a=amount(t)
        if a is None or a<Decimal(get_setting("deposit_min")): await msg.reply_text(f"সর্বনিম্ন ৳{get_setting('deposit_min')} লিখুন।"); return
        ctx.user_data["form"]={"amount":str(a)}; ctx.user_data["stage"]="dep_method"
        await msg.reply_text("পেমেন্ট পদ্ধতি নির্বাচন করুন:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📲 bKash",callback_data="dep_method:bkash"),InlineKeyboardButton("💚 Nagad",callback_data="dep_method:nagad")]]))
    elif st=="dep_method":
        await msg.reply_text("উপরের bKash অথবা Nagad বাটন নির্বাচন করুন।")
    elif st=="dep_txn":
        f=ctx.user_data["form"]
        try:
            with conn() as c: cur=c.execute("INSERT INTO deposits(user_id,amount,method,txn,created) VALUES(?,?,?,?,?)",(uid,f["amount"],f["method"],t,now())); did=cur.lastrowid
        except sqlite3.IntegrityError: await msg.reply_text("এই Txn ID আগে জমা হয়েছে।"); return
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await msg.reply_text("আপনার পেমেন্ট রিকুয়েস্ট সফলভাবে জমা হয়েছে। অনুগ্রহ করে অপেক্ষা করুন। আপনার পেমেন্ট রিকুয়েস্ট জমা দেওয়ার সময় থেকে সর্বোচ্চ ৪৮ ঘণ্টার মধ্যে পেমেন্ট সম্পন্ন করা হবে। ধৈর্য ধরে অপেক্ষা করার জন্য আপনাকে ধন্যবাদ। — BABLU STAR BD")
        await admin_msg(ctx,f"Deposit #{did}\nUser: {uid}\nAmount: ৳{f['amount']}\nMethod: {f['method']}\nTxn: {t}","deposit",did)
    elif st=="wd_gross":
        gross=amount(t); method=ctx.user_data.get("wd_method","bkash")
        minimum=Decimal("50") if method=="recharge" else Decimal("100")
        if gross is None or gross<minimum:
            await msg.reply_text(f"সর্বনিম্ন পরিমাণ ৳{m(minimum)}। আবার লিখুন:"); return
        fee_pct=Decimal(get_setting("recharge_fee" if method=="recharge" else "withdraw_fee") or "15")
        fee=(gross*fee_pct/Decimal("100")).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP); net=gross-fee
        with conn() as c: r=c.execute("SELECT worker FROM users WHERE id=?",(uid,)).fetchone()
        if Decimal(r["worker"] or 0)<gross:
            await msg.reply_text(f"ব্যালেন্স কম। প্রয়োজন ৳{m(gross)}, আপনার ব্যালেন্স ৳{m(r['worker'] or 0)}।"); return
        ctx.user_data["form"]={"gross":str(gross),"fee":str(fee),"net":str(net),"method":method}
        ctx.user_data["stage"]="wd_account"
        label={"bkash":"বিকাশ নম্বর","nagad":"নগদ নম্বর","recharge":"যে মোবাইল নম্বরে রিচার্জ চান"}[method]
        await msg.reply_text(f"🧾 আপনার উইথড্র হিসাব\nনির্বাচিত পদ্ধতি: {label}\nমোট টাকা: ৳{m(gross)}\nফি ({m(fee_pct)}%): ৳{m(fee)}\n✅ ফি কাটার পর আপনি পাবেন: ৳{m(net)}\n\nহিসাব ঠিক থাকলে এখন {label} লিখুন।")
    elif st=="wd_account":
        if len(t)<8 or len(t)>40: await msg.reply_text("সঠিক নম্বর লিখুন।"); return
        f=ctx.user_data["form"]; g=Decimal(f["gross"])
        with conn() as c:
            c.execute("BEGIN IMMEDIATE"); r=c.execute("SELECT worker FROM users WHERE id=?",(uid,)).fetchone(); bal=Decimal(r["worker"] or 0)
            if bal<g: c.rollback(); await msg.reply_text("ব্যালেন্স যথেষ্ট নেই।"); return
            c.execute("UPDATE users SET worker=? WHERE id=?",(str(bal-g),uid))
            cur=c.execute("INSERT INTO withdrawals(user_id,gross,fee,net,method,account,created) VALUES(?,?,?,?,?,?,?)",(uid,f["gross"],f["fee"],f["net"],f["method"],t,now())); wid=cur.lastrowid
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await msg.reply_text("আপনার পেমেন্ট রিকুয়েস্ট সফলভাবে জমা হয়েছে। অনুগ্রহ করে অপেক্ষা করুন। আপনার পেমেন্ট রিকুয়েস্ট জমা দেওয়ার সময় থেকে সর্বোচ্চ ৪৮ ঘণ্টার মধ্যে পেমেন্ট সম্পন্ন করা হবে। ধৈর্য ধরে অপেক্ষা করার জন্য আপনাকে ধন্যবাদ। — BABLU STAR BD")
        await admin_msg(ctx,f"Withdrawal #{wid}\nUser: {uid}\nGross: ৳{f['gross']}\nFee: ৳{f['fee']}\nNet: ৳{f['net']}\nMethod: {f['method']}\nAccount: {t}","withdraw",wid)
    elif st=="proof":
        await save_proof(update,ctx,ctx.user_data.get("proof_job"),t,"")
async def save_proof(update,ctx,jid,proof,fileid):
    uid=update.effective_user.id
    with conn() as c:
        job=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
        if not job or job["status"]!="open" or job["count"]<1: await update.effective_message.reply_text("কাজটি আর খোলা নেই।"); return
        if job["buyer_id"]==uid: await update.effective_message.reply_text("নিজের কাজের প্রুফ দিতে পারবেন না।"); return
        try: cur=c.execute("INSERT INTO submissions(job_id,worker_id,proof,file_id,created) VALUES(?,?,?,?,?)",(jid,uid,proof,fileid,now())); sid=cur.lastrowid
        except sqlite3.IntegrityError: await update.effective_message.reply_text("এই কাজের প্রুফ আগে জমা দিয়েছেন।"); return
    ctx.user_data.pop("stage",None); ctx.user_data.pop("proof_job",None)
    await update.effective_message.reply_text(f"প্রুফ জমা হয়েছে। সাবমিশন #{sid}; অ্যাডমিন যাচাই করবেন।")
    await admin_msg(ctx,f"Submission #{sid}\nJob #{jid}: {job['title']}\nWorker: {uid}\nProof: {proof or '[photo]'}","submission",sid)
    if fileid and ADMIN:
        try: await ctx.bot.send_photo(ADMIN,fileid,caption=f"প্রুফ Submission #{sid}")
        except Exception: pass
async def photo(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("stage")!="proof": await update.effective_message.reply_text("আগে কোনো কাজের 'প্রুফ জমা দিন' বাটন চাপুন।"); return
    await save_proof(update,ctx,ctx.user_data.get("proof_job"),update.effective_message.caption or "Screenshot proof",update.effective_message.photo[-1].file_id)
async def callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); d=q.data or ""; uid=q.from_user.id; user_row(q.from_user)
    with conn() as c:
        banrow=c.execute("SELECT banned FROM users WHERE id=?",(uid,)).fetchone()
    if banrow and banrow["banned"] and not is_admin(uid):
        await q.message.reply_text(get_setting("banned_text").format(reason="অ্যাডমিনের সিদ্ধান্ত"),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support",url=get_setting("support_url"))]])); return
    if d.startswith("adm:"):
        if not is_admin(uid): await q.message.reply_text("⛔ শুধু অ্যাডমিন পারবেন।"); return
        parts=d.split(":")
        if d=="adm:home":
            await q.message.reply_text("👑 Admin Menu",reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👑 Admin Job Post (ডিপোজিট/ফি ছাড়া)",callback_data="adm:jobpost")],
                [InlineKeyboardButton("✏️ লেখা Edit",callback_data="adm:texts")],
                [InlineKeyboardButton("💰 ফি ও সীমা",callback_data="adm:fees")],
                [InlineKeyboardButton("💳 পেমেন্ট নম্বর",callback_data="adm:payments")],
                [InlineKeyboardButton("📊 Pending",callback_data="adm:pending")],
                [InlineKeyboardButton("👥 User List / Ban-Unban",callback_data="adm:users")]
            ])); return
        if d=="adm:jobpost":
            rows=[[InlineKeyboardButton(f"{name} — Admin Job",callback_data=f"cat:{i}")] for i,(name,price) in enumerate(CATS)]
            await q.message.reply_text(
                "👑 Admin Job Post\n\nক্যাটাগরি বেছে নিন। এই পথে পোস্ট করা কাজে Buyer Deposit বা ৫% Posting Fee লাগবে না।",
                reply_markup=InlineKeyboardMarkup(rows)
            ); return
        if d=="adm:texts": await admin_setting_prompt(q,ctx,"texts"); return
        if d=="adm:fees": await admin_setting_prompt(q,ctx,"fees"); return
        if d=="adm:payments": await admin_setting_prompt(q,ctx,"payments"); return
        if d=="adm:pending":
            with conn() as c:
                ds=c.execute("SELECT id,user_id,amount,method FROM deposits WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
                ws=c.execute("SELECT id,user_id,gross,method FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
                ss=c.execute("SELECT id,job_id,worker_id FROM submissions WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
                js=c.execute("SELECT id,title,buyer_id FROM jobs WHERE status='pending_admin' ORDER BY id DESC LIMIT 10").fetchall()
            lines=["📋 Pending Queue"]
            lines += [f"Deposit #{r['id']} | User {r['user_id']} | ৳{r['amount']} {r['method']}" for r in ds]
            lines += [f"Withdraw #{r['id']} | User {r['user_id']} | ৳{r['gross']} {r['method']}" for r in ws]
            lines += [f"Submission #{r['id']} | Job {r['job_id']} | Worker {r['worker_id']}" for r in ss]
            lines += [f"Job Approval #{r['id']} | Buyer {r['buyer_id']} | {r['title']}" for r in js]
            rows=[]
            for r in ds:
                rows.append([InlineKeyboardButton(f"✅ Approve Deposit #{r['id']}",callback_data=f"review:deposit:yes:{r['id']}"),InlineKeyboardButton("❌ Reject",callback_data=f"review:deposit:no:{r['id']}")])
            for r in js:
                rows.append([InlineKeyboardButton(f"✅ Publish Job #{r['id']}",callback_data=f"review:job:yes:{r['id']}"),InlineKeyboardButton("❌ Reject",callback_data=f"review:job:no:{r['id']}")])
            for r in ws:
                rows.append([InlineKeyboardButton(f"✅ Approve Withdraw #{r['id']}",callback_data=f"review:withdraw:yes:{r['id']}"),InlineKeyboardButton("❌ Reject",callback_data=f"review:withdraw:no:{r['id']}")])
            rows.append([InlineKeyboardButton("⬅️ Admin Menu",callback_data="adm:home")])
            await q.message.reply_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(rows) if rows else None); return
        if d=="adm:users":
            with conn() as c:
                total=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
                rs=c.execute("SELECT id,username,first_name,banned FROM users ORDER BY created DESC LIMIT 20").fetchall()
            rows=[]
            for r in rs:
                state="🚫 BANNED" if r["banned"] else "🟢 ACTIVE"
                uname=("@"+r["username"]) if r["username"] else "(no username)"
                rows.append([InlineKeyboardButton(f"{r['id']} | {uname} | {state}",callback_data=f"adm:user:{r['id']}")])
            rows.append([InlineKeyboardButton("🔎 Search User ID / Username",callback_data="adm:usersearch")])
            rows.append([InlineKeyboardButton("🔄 Refresh",callback_data="adm:users"),InlineKeyboardButton("⬅️ Admin Menu",callback_data="adm:home")])
            await q.message.reply_text(f"👥 User Management\nমোট নিবন্ধিত Telegram account: {total}\nসর্বশেষ ২০ জন দেখানো হচ্ছে।\n\nপ্রতিটি ইউজার খুলতে চাপুন:",reply_markup=InlineKeyboardMarkup(rows)); return
        if d=="adm:usersearch":
            ctx.user_data["admin_user_search"]=True
            await q.message.reply_text("Telegram User ID অথবা Username লিখুন:")
            return
        if len(parts)==3 and parts[1]=="user":
            target=int(parts[2])
            with conn() as c: r=c.execute("SELECT * FROM users WHERE id=?",(target,)).fetchone()
            if not r: await q.message.reply_text("ইউজার পাওয়া যায়নি।"); return
            status="🚫 Banned" if r["banned"] else "🟢 Active"
            uname="@"+(r["username"] or " নেই")
            action="unban" if r["banned"] else "ban"
            await q.message.reply_text(f"👤 User Details\nID: {r['id']}\nUsername: {uname}\nName: {r['first_name']}\nStatus: {status}\nBan reason: {r['ban_reason'] or '—'}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔓 Unban" if action=="unban" else "🚫 Ban",callback_data=f"adm:{action}:{target}")],[InlineKeyboardButton("⬅️ User List",callback_data="adm:users")]])); return
        if len(parts)==3 and parts[1] in ("ban","unban"):
            target=int(parts[2])
            if parts[1]=="ban":
                ctx.user_data["ban_target"]=target
                await q.message.reply_text("Ban করার কারণ লিখুন:"); return
            with conn() as c: c.execute("UPDATE users SET banned=0,ban_reason='' WHERE id=?",(target,))
            await q.message.reply_text(f"✅ User {target} Unban করা হয়েছে।"); return
        if len(parts)==3 and parts[1]=="edit":
            key=parts[2]; ctx.user_data["admin_edit_key"]=key
            await q.message.reply_text(f"✏️ নতুন মান/লেখা পাঠান।\n\nবর্তমান মান:\n{get_setting(key)}\n\nবাতিল করতে /cancel লিখুন।")
            return
    if d.startswith("dep_method:"):
        method=d.split(":",1)[1]
        if method not in ("bkash","nagad"): return
        ctx.user_data["form"]["method"]=method; ctx.user_data["stage"]="dep_txn"
        number=get_setting("bkash_number" if method=="bkash" else "nagad_number")
        await q.message.reply_text(f"💳 {method.upper()} পেমেন্ট\nপরিমাণ: ৳{m(ctx.user_data['form']['amount'])}\nপেমেন্ট পাঠানোর নম্বর: {number}\n\nপেমেন্ট করার পর Transaction ID লিখুন:")
        return
    if d.startswith("wdmethod:"):
        method=d.split(":",1)[1]
        if method not in ("bkash","nagad","recharge"): return
        ctx.user_data["wd_method"]=method; ctx.user_data["stage"]="wd_gross"
        minimum="50" if method=="recharge" else "100"
        fee=get_setting("recharge_fee" if method=="recharge" else "withdraw_fee") or "15"
        ctx.user_data["form"]={}
        await q.message.reply_text(f"পদ্ধতি: { {'bkash':'বিকাশ','nagad':'নগদ','recharge':'মোবাইল রিচার্জ'}[method] }\nসর্বনিম্ন: ৳{minimum}\nফি: {fee}%\nকত টাকা তুলবেন/রিচার্জ করবেন? BDT-তে পরিমাণ লিখুন:")
        return
    if d.startswith("cat:"):
        try: ci=int(d.split(":")[1]); name,tasks,bundle=PLATFORMS[ci]
        except: return
        ctx.user_data["form"]={"cat":ci,"tasks":[]}; ctx.user_data["stage"]="job_tasks"
        rows=[[InlineKeyboardButton(f"☐ {task}",callback_data=f"task:{ci}:{i}")] for i,task in enumerate(tasks)]
        rows.append([InlineKeyboardButton("✅ নির্বাচন শেষ",callback_data=f"tasks_done:{ci}")])
        await q.message.reply_text(f"প্ল্যাটফর্ম: {name}\nকাজের ধরন এক বা একাধিক বাছাই করুন। একাধিক বাছাই করলে নির্ধারিত Bundle Price প্রযোজ্য হতে পারে।",reply_markup=InlineKeyboardMarkup(rows))
    elif d.startswith("task:"):
        try: _,rawci,rawi=d.split(":"); ci=int(rawci); ti=int(rawi); name,tasks,bundle=PLATFORMS[ci]; task=tasks[ti]
        except: return
        if ctx.user_data.get("stage")!="job_tasks" or not ctx.user_data.get("form"): await q.message.reply_text("ফর্মটি আর সক্রিয় নেই।"); return
        selected=ctx.user_data["form"].setdefault("tasks",[])
        if task in selected: selected.remove(task)
        else: selected.append(task)
        rows=[[InlineKeyboardButton(("☑ " if x in selected else "☐ ")+x,callback_data=f"task:{ci}:{i}")] for i,x in enumerate(tasks)]
        rows.append([InlineKeyboardButton("✅ নির্বাচন শেষ",callback_data=f"tasks_done:{ci}")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
    elif d.startswith("tasks_done:"):
        ci=int(d.split(":")[1]); f=ctx.user_data.get("form") or {}; selected=f.get("tasks",[])
        if ctx.user_data.get("stage")!="job_tasks" or not selected: await q.message.reply_text("কমপক্ষে একটি কাজের ধরন বাছাই করুন।"); return
        f["task_label"]=", ".join(selected); ctx.user_data["stage"]="job_title"
        await q.message.reply_text(f"নির্বাচিত কাজ: {f['task_label']}\nএখন কাজের শিরোনাম লিখুন।")
    elif d=="job:no":
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); await q.message.reply_text("কাজ পোস্ট বাতিল।")
    elif d=="job:yes":
        if ctx.user_data.get("stage")!="job_confirm" or not ctx.user_data.get("form"): await q.message.reply_text("ফর্মটি আর সক্রিয় নেই।"); return
        f=ctx.user_data["form"]; total=Decimal(f["price"])*int(f["count"])
        with conn() as c:
            c.execute("BEGIN IMMEDIATE"); r=c.execute("SELECT buyer FROM users WHERE id=?",(uid,)).fetchone(); bal=Decimal(r["buyer"] or 0)
            admin_job=is_admin(uid)
            if not admin_job and bal<total: c.rollback(); await q.message.reply_text("বায়ার ব্যালেন্স কম। আগে ডিপোজিট করুন।"); return
            if not admin_job:
                c.execute("UPDATE users SET buyer=? WHERE id=?",(str(bal-total),uid))
            cur=c.execute("INSERT INTO jobs(buyer_id,cat,title,description,count,price,reserved,rules,status,created,is_admin_job) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uid,f["cat"],f["task_label"] + " | " + f["title"],f["desc"],f["count"],f["price"],str(total),f["rules"],"open" if admin_job else "pending_admin",now(),1 if admin_job else 0)); jid=cur.lastrowid
            if not admin_job:
                c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(uid,"buyer",str(-total),"escrow",f"Job #{jid}",now()))
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await q.message.reply_text(f"কাজ #{jid} জমা হয়েছে। মোট বাজেট ৳{m(total)}\n"+("👑 Admin Job: সরাসরি প্রকাশিত হয়েছে; ডিপোজিট/পোস্টিং ফি লাগেনি।" if is_admin(uid) else "বাজেট সংরক্ষিত হয়েছে। অ্যাডমিন অনুমোদনের পর কাজটি ফ্রিল্যান্সারদের দেখানো হবে।"))
        if not is_admin(uid): await admin_msg(ctx,f"নতুন Job Approval দরকার\nJob #{jid}: {f['title']} | Buyer {uid} | Budget ৳{m(total)}", "job", jid)
    elif d.startswith("submit:"):
        jid=int(d.split(":")[1])
        with conn() as c:
            j=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone(); ex=c.execute("SELECT 1 FROM submissions WHERE job_id=? AND worker_id=?",(jid,uid)).fetchone()
        if not j or j["status"]!="open" or j["count"]<1: await q.message.reply_text("কাজটি আর খোলা নেই।")
        elif j["buyer_id"]==uid: await q.message.reply_text("নিজের কাজ করা যাবে না।")
        elif ex: await q.message.reply_text("এই কাজের প্রুফ আগে জমা দিয়েছেন।")
        else:
            ctx.user_data["proof_job"]=jid; ctx.user_data["stage"]="proof"; await q.message.reply_text("কাজের প্রুফ লিখুন অথবা স্ক্রিনশট পাঠান। Cancel বাটন কাজ করবে।")
    elif d.startswith("review:"):
        if uid!=ADMIN: await q.message.reply_text("শুধু অ্যাডমিন পারবেন।"); return
        _,kind,decision,raw=d.split(":"); item=int(raw); yes=decision=="yes"
        await review(q,ctx,kind,item,yes)
async def review(q,ctx,kind,item,yes):
    target=0; usermsg=""
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if kind=="job":
            r=c.execute("SELECT * FROM jobs WHERE id=?",(item,)).fetchone()
            if not r or r["status"]!="pending_admin": await q.message.reply_text("কাজটি আর Pending নেই।"); return
            if yes:
                c.execute("UPDATE jobs SET status='open' WHERE id=? AND status='pending_admin'",(item,))
                target=r["buyer_id"]; usermsg=f"আপনার Job #{item} অ্যাডমিন অনুমোদন করেছেন। এখন ফ্রিল্যান্সাররা কাজটি দেখতে পারবেন।"
            else:
                c.execute("UPDATE jobs SET status='rejected',reserved='0' WHERE id=? AND status='pending_admin'",(item,))
                if not r["is_admin_job"]:
                    u=c.execute("SELECT buyer FROM users WHERE id=?",(r["buyer_id"],)).fetchone()
                    refund=Decimal(r["reserved"] or "0")
                    c.execute("UPDATE users SET buyer=? WHERE id=?",(str(Decimal(u["buyer"] or "0")+refund),r["buyer_id"]))
                    c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(r["buyer_id"],"buyer",str(refund),"job_refund",f"Rejected Job #{item}",now()))
                target=r["buyer_id"]; usermsg=f"আপনার Job #{item} বাতিল হয়েছে। সংরক্ষিত বাজেট ফেরত দেওয়া হয়েছে।"
        elif kind=="deposit":
            r=c.execute("SELECT * FROM deposits WHERE id=?",(item,)).fetchone()
            if not r or r["status"]!="pending": await q.message.reply_text("ইতিমধ্যে প্রসেস করা হয়েছে।"); return
            c.execute("UPDATE deposits SET status=?,reviewed=? WHERE id=? AND status='pending'",("approved" if yes else "rejected",now(),item))
            if yes:
                u=c.execute("SELECT buyer FROM users WHERE id=?",(r["user_id"],)).fetchone(); b=Decimal(u["buyer"] or 0)+Decimal(r["amount"]); c.execute("UPDATE users SET buyer=? WHERE id=?",(str(b),r["user_id"]))
                c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(r["user_id"],"buyer",r["amount"],"deposit",f"Deposit #{item}",now()))
            target=r["user_id"]; usermsg=f"ডিপোজিট #{item} {'অনুমোদিত' if yes else 'বাতিল'} হয়েছে।"
        elif kind=="withdraw":
            r=c.execute("SELECT * FROM withdrawals WHERE id=?",(item,)).fetchone()
            if not r or r["status"]!="pending": await q.message.reply_text("ইতিমধ্যে প্রসেস করা হয়েছে।"); return
            c.execute("UPDATE withdrawals SET status=?,reviewed=? WHERE id=? AND status='pending'",("approved" if yes else "rejected",now(),item))
            if not yes:
                u=c.execute("SELECT worker FROM users WHERE id=?",(r["user_id"],)).fetchone(); b=Decimal(u["worker"] or 0)+Decimal(r["gross"]); c.execute("UPDATE users SET worker=? WHERE id=?",(str(b),r["user_id"]))
            else:
                u=c.execute("SELECT referrer,ref_paid FROM users WHERE id=?",(r["user_id"],)).fetchone()
                if u and u["referrer"] and not u["ref_paid"]:
                    bonus=(Decimal(r["gross"])*REFERRAL).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                    ref=c.execute("SELECT worker FROM users WHERE id=?",(u["referrer"],)).fetchone()
                    if ref:
                        c.execute("UPDATE users SET worker=? WHERE id=?",(str(Decimal(ref["worker"] or 0)+bonus),u["referrer"]))
                        c.execute("UPDATE users SET ref_paid=1 WHERE id=?",(r["user_id"],))
                        c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(u["referrer"],"worker",str(bonus),"referral",f"Referral user {r['user_id']}",now()))
            target=r["user_id"]; usermsg=f"উইথড্র #{item} {'অনুমোদিত' if yes else 'বাতিল'} হয়েছে।"
        elif kind=="submission":
            r=c.execute("SELECT * FROM submissions WHERE id=?",(item,)).fetchone()
            if not r or r["status"]!="pending": await q.message.reply_text("ইতিমধ্যে প্রসেস করা হয়েছে।"); return
            j=c.execute("SELECT * FROM jobs WHERE id=?",(r["job_id"],)).fetchone()
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
                status="rejected"; usermsg=f"সাবমিশন #{item} বাতিল হয়েছে।"
            c.execute("UPDATE submissions SET status=?,reviewed=? WHERE id=? AND status='pending'",(status,now(),item)); target=r["worker_id"]
        else: await q.message.reply_text("অজানা রিভিউ।"); return
        c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(q.from_user.id,f"{kind}_{decision}",str(item),now()))
    await q.message.reply_text(f"{kind} #{item} সম্পন্ন।")
    try: await ctx.bot.send_message(target,usermsg)
    except Exception: pass
async def admin(update,ctx):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ শুধু অ্যাডমিনের জন্য।"); return
    with conn() as c:
        d=c.execute("SELECT COUNT(*) n FROM deposits WHERE status='pending'").fetchone()["n"]
        w=c.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
        s=c.execute("SELECT COUNT(*) n FROM submissions WHERE status='pending'").fetchone()["n"]
        users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    rows=[[InlineKeyboardButton("👑 Admin Job Post (ডিপোজিট/ফি ছাড়া)",callback_data="adm:jobpost")],
          [InlineKeyboardButton("✏️ লেখা/মেসেজ Edit",callback_data="adm:texts")],
          [InlineKeyboardButton("💰 ফি ও সীমা Edit",callback_data="adm:fees")],
          [InlineKeyboardButton("💳 পেমেন্ট নম্বর Edit",callback_data="adm:payments")],
          [InlineKeyboardButton("📊 Pending তালিকা",callback_data="adm:pending")],
          [InlineKeyboardButton("👥 User List / Ban-Unban",callback_data="adm:users")]]
    await update.effective_message.reply_text(f"👑 BABLU STAR BD — Admin Dashboard\n\n📥 Pending Deposit: {d}\n📤 Pending Withdrawal: {w}\n🧾 Pending Submission: {s}\n\nনিচের বাটন থেকে সেটিংস পরিবর্তন করুন।",reply_markup=InlineKeyboardMarkup(rows))

async def admin_setting_prompt(q,ctx,group):
    groups={
      "texts":[("welcome","স্বাগতম মেসেজ"),("rules","নিয়মাবলি"),("payment_info","পেমেন্ট তথ্য"),("support_email","Support Email"),("banned_text","Ban মেসেজ ( {reason} রাখবেন )"),("support_url","Support URL")],
      "fees":[("deposit_min","Minimum Deposit"),("withdraw_min","Minimum Withdrawal"),("recharge_min","Minimum Recharge"),("withdraw_fee","Withdrawal Fee %"),("post_fee","Job Posting Fee %"),("referral_bonus","Referral Bonus %"),("usd_rate","USD Rate ৳")],
      "payments":[("bkash_number","bKash Number"),("nagad_number","Nagad Number")]
    }
    rows=[]
    for key,label in groups[group]:
        val=get_setting(key)
        preview=val if len(val)<25 else val[:22]+"..."
        rows.append([InlineKeyboardButton(f"✏️ {label}: {preview}",callback_data=f"adm:edit:{key}")])
    rows.append([InlineKeyboardButton("⬅️ Admin Menu",callback_data="adm:home")])
    await q.message.reply_text("যে সেটিংস পরিবর্তন করতে চান সেটি চাপুন:",reply_markup=InlineKeyboardMarkup(rows))

async def err(update,ctx):
    log.exception("Bot error",exc_info=ctx.error)
    if update and update.effective_message:
        try: await update.effective_message.reply_text("সমস্যা হয়েছে। আবার চেষ্টা করুন।")
        except: pass
def main():
    if not TOKEN: raise SystemExit("Inject Cloud-এ BOT_TOKEN সেট করুন।")
    if not ADMIN: raise SystemExit("Inject Cloud-এ ADMIN_ID (numeric Telegram ID) সেট করুন।")
    init(); app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("cancel",lambda u,c: menu(u,c,"চলমান কাজ বাতিল হয়েছে।")))
    app.add_handler(CallbackQueryHandler(callback)); app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text)); app.add_error_handler(err)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__=="__main__": main()
