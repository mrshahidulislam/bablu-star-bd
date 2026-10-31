import os, sqlite3, logging, re
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

CATS = [
("Telegram Bot Start / Basic Check",2),("Telegram Channel Task",3),("Telegram Group Task",3),
("YouTube Permitted Task",3),("Facebook Permitted Promotion",3),("X (Twitter) Task",3),
("Instagram Permitted Task",3),("TikTok Permitted Task",3),("Like / Reaction",2),
("Comment / Genuine Feedback",5),("Share / Content Submission",3),("Email-related Valid Work",5),
("App Download / Testing",5),("App Signup / Onboarding",5),("Website Visit",2),
("Website Signup",5),("Copy-Paste",2),("Data Entry",5),("Form Fill-up",3),("Survey",5),
("Screenshot / Proof Collection",2),("Bengali Typing",5),("English Typing",7),("Translation",15),
("Research / Data Collection",10),("Excel / Spreadsheet",10),("Product Listing",5),
("Image Classification",5),("Graphic Design",15),("Video Editing / Bug Testing / Other",20)
]
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
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,buyer_id INTEGER,cat INTEGER,title TEXT,description TEXT,count INTEGER,price TEXT,reserved TEXT,rules TEXT,proof_type TEXT NOT NULL DEFAULT 'text',status TEXT DEFAULT 'open',created TEXT);
        CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER,worker_id INTEGER,proof TEXT,file_id TEXT DEFAULT '',status TEXT DEFAULT 'pending',created TEXT,reviewed TEXT,UNIQUE(job_id,worker_id));
        CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,amount TEXT,method TEXT,txn TEXT UNIQUE,status TEXT DEFAULT 'pending',created TEXT,reviewed TEXT);
        CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,gross TEXT,fee TEXT,net TEXT,method TEXT,account TEXT,status TEXT DEFAULT 'pending',created TEXT,reviewed TEXT);
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,wallet TEXT,amount TEXT,kind TEXT,note TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor INTEGER,action TEXT,details TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        """)
        # Additive migration: existing jobs remain available with text proof.
        cols={r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
        if "proof_type" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN proof_type TEXT NOT NULL DEFAULT 'text'")
        defaults={"deposit_min":"50","withdraw_min":"100","recharge_min":"50","withdraw_fee":"0.15","job_fee":"0.05","referral_rate":"0.10",
                  "withdraw_request_sent_text":"✅ আপনার Withdrawal Request সফলভাবে জমা হয়েছে।\n\n🕒 অ্যাডমিন যাচাই করার পর পেমেন্ট পাঠানো হবে।\n📌 অনুগ্রহ করে অপেক্ষা করুন।\n\n— BABLU STAR BD",
                  "withdraw_instructions_text":"💸 উইথড্রের পরিমাণ লিখুন। বিকাশ/নগদে সর্বনিম্ন ৳১০০, রিচার্জে ৳৫০। সার্ভিস ফি ১৫%।",
                  "withdraw_pending_text":"⏳ আপনার Withdrawal Request অপেক্ষমাণ আছে। অ্যাডমিন যাচাই করছেন।",
                  "withdraw_approved_text":"✅ আপনার Withdrawal Request অনুমোদিত হয়েছে।",
                  "withdraw_rejected_text":"❌ আপনার Withdrawal Request বাতিল হয়েছে। টাকা আপনার ব্যালেন্সে ফেরত দেওয়া হয়েছে।"}
        for k,v in defaults.items(): c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
def user_row(u):
    with conn() as c:
        r=c.execute("SELECT * FROM users WHERE id=?",(u.id,)).fetchone()
        if not r:
            c.execute("INSERT INTO users(id,username,first_name,created) VALUES(?,?,?,?)",(u.id,u.username or "",u.first_name or "",now()))
        else: c.execute("UPDATE users SET username=?,first_name=? WHERE id=?",(u.username or "",u.first_name or "",u.id))
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
async def menu(update,ctx,msg="মেনু থেকে অপশন বেছে নিন."):
    if update.effective_user and is_banned(update.effective_user.id):
        target=update.effective_message or update.callback_query.message
        await banned_notice(target); return
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
    if is_banned(u.id): await banned_notice(update.effective_message); return
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
        "🌟 BABLU STAR BD-তে আপনাকে স্বাগতম! 🌟\n\n"
        "💼 আপনার কাজ, আপনার উপার্জন!\n"
        "🛒 Buyer Mode — নিজের কাজ পোস্ট করুন\n"
        "🧑‍💻 Freelancer Mode — কাজ করুন ও আয় করুন\n\n"
        "👇 শুরু করতে নিচের একটি মোড বেছে নিন।"
    )
async def text(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; user_row(u); t=(update.effective_message.text or "").strip()
    with conn() as c:
        r=c.execute("SELECT banned FROM users WHERE id=?",(u.id,)).fetchone()
    if r and r["banned"]:
        if t=="🆘 সাহায্য":
            await update.effective_message.reply_text(f"📩 Support Email: {get_setting('support_email','mrshahidulislam8989@gmail.com')}\nআপনার Telegram ID: {u.id}")
        else: await banned_notice(update.effective_message)
        return
    if t in ("🔙 Back / ❌ Cancel","🔙 Back","❌ Cancel","/cancel"):
        await menu(update,ctx,"চলমান কাজ বাতিল হয়েছে।"); return
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
            cat=CATS[int(r["cat"])][0]
            msg=f"কাজ #{r['id']} — {cat}\n{r['title']}\n{r['description']}\nপ্রতি কাজ ৳{m(r['price'])} | বাকি {r['count']}\nপ্রুফের ধরন: {r['proof_type']}\nনিয়ম/প্রুফ: {r['rules']}"
            await update.effective_message.reply_text(msg,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📨 প্রুফ জমা দিন",callback_data=f"submit:{r['id']}")]]))
    elif t=="📨 আমার সাবমিশন" and mode=="worker":
        with conn() as c: rs=c.execute("SELECT s.id,s.job_id,s.status,j.title FROM submissions s JOIN jobs j ON j.id=s.job_id WHERE s.worker_id=? ORDER BY s.id DESC LIMIT 20",(u.id,)).fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} কাজ #{r['job_id']} {r['title']} — {r['status']}" for r in rs]) or "কোনো সাবমিশন নেই।")
    elif t=="💸 উইথড্র" and mode=="worker":
        ctx.user_data["stage"]="wd_gross"
        await update.effective_message.reply_text(
            "💸 উইথড্র রিকুয়েস্ট\n\nবিকাশ/নগদে সর্বনিম্ন উইথড্র: ৳১০০; মোবাইল রিচার্জ: ৳৫০\nসার্ভিস ফি: ১৫%\n"
            "উদাহরণ: ৳১০০ তুললে ফি ৳১৫, আপনি পাবেন ৳৮৫।\n\n"
            "মোট উইথড্রের পরিমাণ লিখুন (শুধু টাকা, যেমন 100)। এরপর পেমেন্ট পদ্ধতি বাটন থেকে বেছে নিতে পারবেন।"
        )
    elif t=="👥 রেফার লিংক" and mode=="worker":
        me=await ctx.bot.get_me(); await update.effective_message.reply_text(f"https://t.me/{me.username}?start=ref_{u.id}\nরেফার করা ব্যক্তির প্রথম অনুমোদিত উইথড্রয়ের মোট অঙ্কের ১০% একবার বোনাস।")
    elif t=="💳 পেমেন্ট তথ্য":
        await update.effective_message.reply_text(
            "💳 BABLU STAR BD — পেমেন্ট তথ্য\n\n"
            "➕ ডিপোজিট: বায়ার মোডে ডিপোজিট চাপুন, পরিমাণ লিখুন, বিকাশ/নগদ বেছে নিয়ে Transaction ID জমা দিন। অ্যাডমিন যাচাই করে ব্যালেন্স যোগ করবেন।\n\n"
            "💸 উইথড্র: ফ্রিল্যান্সার মোড → উইথড্র → পরিমাণ লিখুন → বিকাশ/নগদ/মোবাইল রিচার্জ বেছে নিন → নম্বর দিন।\n\n"
            "বিকাশ/নগদে সর্বনিম্ন উইথড্র ৳১০০, মোবাইল রিচার্জ ৳৫০। প্রতিটি উইথড্র/রিচার্জে ১৫% ফি। উদাহরণ: ৳১০০ থেকে ফি ৳১৫, পাবেন ৳৮৫।\n\n"
            "পেমেন্ট রিসিভারের নম্বর অ্যাডমিনের কাছ থেকে নিশ্চিত করুন; এখানে কোনো নম্বর অনুমান করে দেখানো হচ্ছে না।"
        )
    elif t=="📜 নিয়মাবলি":
        await update.effective_message.reply_text("• কাজের দাম ক্যাটাগরি মিনিমামের কম নয় (কোনোটিই ৳২-এর নিচে নয়)।\n• ভুয়া অ্যাকাউন্ট, প্রতারণা, স্প্যাম বা প্ল্যাটফর্মের নিয়মভঙ্গকারী কাজ নিষিদ্ধ।\n• বাজেট আগে বায়ার ব্যালেন্স থেকে সংরক্ষিত হবে।\n• প্রুফ অ্যাডমিন অনুমোদন করলে ফ্রিল্যান্সার টাকা পাবেন।\n• ডিপোজিট/উইথড্র ম্যানুয়ালি যাচাই হয়।")
    elif t=="🆘 সাহায্য":
        await update.effective_message.reply_text(
            "📩 BABLU STAR BD — Support Center\n\n"
            "আপনার কোনো সমস্যা বা প্রশ্ন থাকলে আমাদের ইমেইল করুন।\n\n"
            "✉️ Support Email:\n"
            "mrshahidulislam8989@gmail.com\n\n"
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
        elif key.startswith("withdraw_") and not 5<=len(t)<=3500:
            await msg.reply_text("মেসেজ ৫–৩৫০০ অক্ষরের মধ্যে লিখুন।")
            ctx.user_data["admin_setting_key"]=key
            return
        elif key in ("withdraw_fee","job_fee","referral_rate"):
            try: rate=Decimal(t)
            except Exception: rate=Decimal("-1")
            if not rate.is_finite() or rate < 0 or rate > 1:
                await msg.reply_text("শতকরা হার ০ থেকে ১-এর মধ্যে দশমিক হিসেবে দিন। যেমন ১৫% হলে 0.15")
                ctx.user_data["admin_setting_key"]=key
                return
        elif key in ("deposit_min","withdraw_min","recharge_min"):
            a=amount(t)
            if a is None:
                await msg.reply_text("সঠিক ধনাত্মক টাকার পরিমাণ লিখুন।")
                ctx.user_data["admin_setting_key"]=key
                return
            t=str(a)
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
        await msg.reply_text(f"ক্যাটাগরি: {CATS[f['cat']][0]}\nশিরোনাম: {f['title']}\nবিবরণ: {f['desc']}\nকাজ: {f['count']}\nপ্রতি কাজ: ৳{m(f['price'])}\nমোট বাজেট: ৳{m(total)}\nনিয়ম: {t}\nপোস্ট করবেন?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ পোস্ট",callback_data="job:yes"),InlineKeyboardButton("❌ বাতিল",callback_data="job:no")]]))
    elif st=="dep_amount":
        a=amount(t)
        if a is None or a<Decimal(get_setting("deposit_min","50")): await msg.reply_text(f"সর্বনিম্ন ডিপোজিট ৳{m(get_setting('deposit_min','50'))}।"); return
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
        except sqlite3.IntegrityError: await msg.reply_text("এই Txn ID আগে জমা হয়েছে।"); return
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await msg.reply_text("✅ আপনার ডিপোজিট অনুরোধ জমা হয়েছে। অ্যাডমিন Transaction ID যাচাই করে ব্যালেন্স যোগ করবেন।")
        await admin_msg(ctx,f"Deposit #{did}\nUser: {uid}\nAmount: ৳{f['amount']}\nMethod: {f['method']}\nTxn: {t}","deposit",did)
    elif st=="wd_gross":
        g=amount(t)
        if g is None or g<50: await msg.reply_text("সর্বনিম্ন পরিমাণ ৳৫০। বিকাশ/নগদে তুলতে কমপক্ষে ৳১০০ লাগবে।"); return
        fee=(g*Decimal(get_setting("withdraw_fee","0.15"))).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP); net=g-fee
        with conn() as c: r=c.execute("SELECT worker FROM users WHERE id=?",(uid,)).fetchone()
        if Decimal(r["worker"] or 0)<g: await msg.reply_text(f"ব্যালেন্স কম। আপনার ব্যালেন্স ৳{m(r['worker'])}।"); return
        ctx.user_data["form"]={"gross":str(g),"fee":str(fee),"net":str(net)}; ctx.user_data["stage"]="wd_method"
        await msg.reply_text(
            f"🧾 উইথড্র হিসাব\nমোট: ৳{m(g)}\nসার্ভিস ফি (১৫%): ৳{m(fee)}\nআপনি পাবেন: ৳{m(net)}\n\n"
            "নিচের বাটন থেকে পেমেন্ট পদ্ধতি বেছে নিন।",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟣 বিকাশ",callback_data="wdmethod:bKash"),InlineKeyboardButton("🟠 নগদ",callback_data="wdmethod:Nagad")],[InlineKeyboardButton("📱 মোবাইল রিচার্জ",callback_data="wdmethod:Recharge")]])
        )
    elif st=="wd_method":
        aliases={"বিকাশ":"bKash","বিকাশ (bKash)":"bKash","নগদ":"Nagad","নগদ (Nagad)":"Nagad","মোবাইল রিচার্জ":"Recharge","রিচার্জ":"Recharge","bKash":"bKash","Nagad":"Nagad","Recharge":"Recharge"}
        method=aliases.get(t)
        if method not in ("bKash","Nagad","Recharge"):
            await msg.reply_text("নিচের বাটন থেকে পেমেন্ট পদ্ধতি বেছে নিন।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟣 বিকাশ",callback_data="wdmethod:bKash"),InlineKeyboardButton("🟠 নগদ",callback_data="wdmethod:Nagad")],[InlineKeyboardButton("📱 মোবাইল রিচার্জ",callback_data="wdmethod:Recharge")]]))
            return
        ctx.user_data["form"]["method"]=method; ctx.user_data["stage"]="wd_account"
        label={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[method]
        await msg.reply_text(f"পেমেন্ট পদ্ধতি: {label}\nএখন আপনার মোবাইল/অ্যাকাউন্ট নম্বর লিখুন।")
    elif st=="wd_account":
        if len(t)<8 or len(t)>40: await msg.reply_text("সঠিক নম্বর লিখুন।"); return
        f=ctx.user_data["form"]; g=Decimal(f["gross"])
        with conn() as c:
            c.execute("BEGIN IMMEDIATE"); r=c.execute("SELECT worker FROM users WHERE id=?",(uid,)).fetchone(); bal=Decimal(r["worker"] or 0)
            if bal<g: c.rollback(); await msg.reply_text("ব্যালেন্স যথেষ্ট নেই।"); return
            c.execute("UPDATE users SET worker=? WHERE id=?",(str(bal-g),uid))
            cur=c.execute("INSERT INTO withdrawals(user_id,gross,fee,net,method,account,created) VALUES(?,?,?,?,?,?,?)",(uid,f["gross"],f["fee"],f["net"],f["method"],t,now())); wid=cur.lastrowid
        ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None)
        await msg.reply_text(get_setting("withdraw_request_sent_text", "✅ আপনার Withdrawal Request জমা হয়েছে। অ্যাডমিন যাচাই করে পেমেন্ট পাঠাবেন।") + "\n\n" + get_setting("withdraw_pending_text", "⏳ অ্যাডমিন যাচাই করছেন।"))
        await admin_msg(ctx,f"Withdrawal #{wid}\nUser: {uid}\nGross: ৳{f['gross']}\nFee: ৳{f['fee']}\nNet: ৳{f['net']}\nMethod: {f['method']}\nAccount: {t}","withdraw",wid)
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
        except sqlite3.IntegrityError: await update.effective_message.reply_text("এই কাজের প্রুফ আগে জমা দিয়েছেন।"); return
    ctx.user_data.pop("stage",None); ctx.user_data.pop("proof_job",None); ctx.user_data.pop("proof_type",None); ctx.user_data.pop("proof_file_id",None)
    await update.effective_message.reply_text(f"প্রুফ জমা হয়েছে। সাবমিশন #{sid}; অ্যাডমিন যাচাই করবেন।")
    await admin_msg(ctx,f"Submission #{sid}\nJob #{jid}: {job['title']}\nWorker: {uid}\nProof: {proof or '[photo]'}","submission",sid)
    if fileid and ADMIN:
        try: await ctx.bot.send_photo(ADMIN,fileid,caption=f"প্রুফ Submission #{sid}")
        except Exception: pass
async def photo(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id): await banned_notice(update.effective_message); return
    st=ctx.user_data.get("stage")
    if st not in ("proof_photo","proof_both_photo"): await update.effective_message.reply_text("আগে কাজের প্রুফ জমা দিন বাটন চাপুন এবং প্রয়োজনীয় ধাপ অনুসরণ করুন।"); return
    fid=update.effective_message.photo[-1].file_id
    if st=="proof_both_photo":
        ctx.user_data["proof_file_id"]=fid; ctx.user_data["stage"]="proof_both_text"
        await update.effective_message.reply_text("✅ Screenshot পেয়েছি। এখন প্রমাণের Text বা Link লিখুন।")
    else: await save_proof(update,ctx,ctx.user_data.get("proof_job"),"Screenshot proof",fid)
async def callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); d=q.data or ""; uid=q.from_user.id; user_row(q.from_user)
    if is_banned(uid) and d!="support": await banned_notice(q.message); return
    if d=="support":
        await q.message.reply_text(f"📩 Support Email: {get_setting('support_email','mrshahidulislam8989@gmail.com')}\nআপনার Telegram ID: {uid}"); return
    if d.startswith("adminusers:"):
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        page=max(0,int(d.split(":")[1])); size=10; offset=page*size
        with conn() as c:
            total=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            rows=c.execute("SELECT u.id,u.username,u.first_name,u.worker,u.banned,COALESCE((SELECT SUM(CAST(l.amount AS REAL)) FROM ledger l WHERE l.user_id=u.id AND l.wallet='worker' AND l.kind='earning'),0) earned FROM users u ORDER BY u.created DESC LIMIT ? OFFSET ?",(size,offset)).fetchall()
        if not rows:
            await q.message.reply_text("ব্যবহারকারী পাওয়া যায়নি।"); return
        for r in rows:
            label="BANNED" if r["banned"] else "ACTIVE"
            name=(r["first_name"] or "নাম নেই")[:40]
            uname=("@"+r["username"]) if r["username"] else "username নেই"
            action="unban" if r["banned"] else "ban"
            btn="♻️ Unban" if r["banned"] else "⛔ Ban"
            await q.message.reply_text(f"👤 {name} ({uname})\nID: {r['id']}\nমোট আয়: ৳{m(r['earned'])}\nবর্তমান ব্যালেন্স: ৳{m(r['worker'] or 0)}\nস্ট্যাটাস: {label}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(btn,callback_data=f"adminuser:{action}:{r['id']}" )]]))
        nav=[]
        if page>0: nav.append(InlineKeyboardButton("⬅️ আগের",callback_data=f"adminusers:{page-1}"))
        if offset+size<total: nav.append(InlineKeyboardButton("পরের ➡️",callback_data=f"adminusers:{page+1}"))
        if nav: await q.message.reply_text(f"ব্যবহারকারী {offset+1}-{min(offset+size,total)} / {total}",reply_markup=InlineKeyboardMarkup([nav]))
    elif d.startswith("adminuser:"):
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        _,action,raw=d.split(":"); target=int(raw)
        if target==ADMIN:
            await q.message.reply_text("নিজের অ্যাডমিন অ্যাকাউন্ট Ban করা যাবে না।"); return
        if action not in ("ban","unban"): return
        with conn() as c:
            c.execute("UPDATE users SET banned=? WHERE id=?",(1 if action=="ban" else 0,target))
            c.execute("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(uid,"user_"+action,str(target),now()))
        await q.message.reply_text(f"✅ User {target}: {action.upper()} সম্পন্ন।")
    elif d=="adminrefresh":
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        await q.message.reply_text("ড্যাশবোর্ড আপডেট করতে /admin লিখুন।")
    elif d.startswith("adminset:"):
        if uid!=ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        key=d.split(":",1)[1]
        allowed={"bkash":"bkash_number","nagad":"nagad_number","support_email":"support_email","withdraw_request_sent":"withdraw_request_sent_text","withdraw_instructions":"withdraw_instructions_text","withdraw_pending":"withdraw_pending_text","withdraw_approved":"withdraw_approved_text","withdraw_rejected":"withdraw_rejected_text","withdraw_fee":"withdraw_fee","job_fee":"job_fee","referral_rate":"referral_rate","deposit_min":"deposit_min","withdraw_min":"withdraw_min","recharge_min":"recharge_min"}
        if key not in allowed: return
        ctx.user_data["admin_setting_key"]=allowed[key]
        ctx.user_data["stage"]="admin_setting"
        labels={"bkash":"বিকাশের পেমেন্ট গ্রহণের নম্বর","nagad":"নগদের পেমেন্ট গ্রহণের নম্বর","support_email":"সাপোর্ট ইমেইল","withdraw_request_sent":"Withdrawal Request জমা দেওয়ার পর পাঠানোর মেসেজ","withdraw_instructions":"Withdrawal নির্দেশনা","withdraw_pending":"Withdrawal Pending মেসেজ","withdraw_approved":"Withdrawal Approved মেসেজ","withdraw_rejected":"Withdrawal Rejected মেসেজ","withdraw_fee":"উইথড্র ফি (১৫% হলে 0.15 লিখুন)","job_fee":"জব পোস্টিং ফি (৫% হলে 0.05 লিখুন)","referral_rate":"রেফারেল বোনাস (১০% হলে 0.10 লিখুন)","deposit_min":"সর্বনিম্ন ডিপোজিট","withdraw_min":"সর্বনিম্ন bKash/Nagad উইথড্র","recharge_min":"সর্বনিম্ন রিচার্জ উইথড্র"}
        await q.message.reply_text(f"{labels[key]} লিখুন। শুধু অ্যাডমিনের দেওয়া সঠিক তথ্য দিন। বাতিল করতে /cancel লিখুন।")
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
        await q.message.reply_text(f"ক্যাটাগরি: {CATS[f['cat']][0]}\nশিরোনাম: {f['title']}\nবিবরণ: {f['desc']}\nকাজ: {f['count']}\nপ্রতি কাজ: ৳{m(f['price'])}\nমোট বাজেট: ৳{m(total)}\nProof Type: {label}\nনিয়ম: {f['rules']}\nপোস্ট করবেন?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ পোস্ট",callback_data="job:yes"),InlineKeyboardButton("❌ বাতিল",callback_data="job:no")]]))
    elif d.startswith("cat:"):
        try: ci=int(d.split(":")[1]); name,mi=CATS[ci]
        except: return
        if ctx.user_data.get("admin_free_job") and uid != ADMIN:
            await q.message.reply_text("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।"); return
        ctx.user_data.setdefault("form", {})["cat"]=ci; ctx.user_data["stage"]="job_title"
        await q.message.reply_text(f"ক্যাটাগরি: {name}\nসর্বনিম্ন দাম ৳{mi}\nকাজের শিরোনাম লিখুন।")
    elif d.startswith("wdmethod:"):
        if ctx.user_data.get("stage")!="wd_method" or not ctx.user_data.get("form"):
            await q.message.reply_text("এই উইথড্র ফর্মটি আর সক্রিয় নেই। আবার উইথড্র শুরু করুন।"); return
        method=d.split(":",1)[1]
        if method not in ("bKash","Nagad","Recharge"): return
        g=Decimal(ctx.user_data["form"]["gross"])
        if method in ("bKash","Nagad") and g < Decimal(get_setting("withdraw_min","100")):
            await q.message.reply_text("বিকাশ/নগদে সর্বনিম্ন উইথড্র ৳১০০। আবার ৳১০০ বা তার বেশি দিয়ে শুরু করুন।"); ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); return
        if method == "Recharge" and g < Decimal(get_setting("recharge_min","50")):
            await q.message.reply_text("মোবাইল রিচার্জ সর্বনিম্ন ৳৫০।"); ctx.user_data.pop("stage",None); ctx.user_data.pop("form",None); return
        ctx.user_data["form"]["method"]=method; ctx.user_data["stage"]="wd_account"
        label={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[method]
        await q.message.reply_text(f"পেমেন্ট পদ্ধতি: {label}\nএখন আপনার মোবাইল/অ্যাকাউন্ট নম্বর লিখুন।")
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
        post_fee=Decimal("0") if is_admin_free else (total*Decimal(get_setting("job_fee","0.05"))).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        charge=Decimal("0") if is_admin_free else total+post_fee
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if not is_admin_free:
                r=c.execute("SELECT buyer FROM users WHERE id=?",(uid,)).fetchone(); bal=Decimal(r["buyer"] or 0)
                if bal<charge: c.rollback(); await q.message.reply_text(f"ব্যালেন্স কম। বাজেট ৳{m(total)} + ৫% পোস্ট ফি ৳{m(post_fee)} = মোট ৳{m(charge)} দরকার। আগে ডিপোজিট করুন।"); return
                c.execute("UPDATE users SET buyer=? WHERE id=?",(str(bal-charge),uid))
            cur=c.execute("INSERT INTO jobs(buyer_id,cat,title,description,count,price,reserved,rules,proof_type,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uid,f["cat"],f["title"],f["desc"],f["count"],f["price"],str(total),f["rules"],f.get("proof_type","text"),"open",now())); jid=cur.lastrowid
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
        await review(q,ctx,kind,item,yes)
async def review(q,ctx,kind,item,yes):
    target=0; usermsg=""
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if kind=="deposit":
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
                    bonus=(Decimal(r["gross"])*Decimal(get_setting("referral_rate","0.10"))).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                    ref=c.execute("SELECT worker FROM users WHERE id=?",(u["referrer"],)).fetchone()
                    if ref:
                        c.execute("UPDATE users SET worker=? WHERE id=?",(str(Decimal(ref["worker"] or 0)+bonus),u["referrer"]))
                        c.execute("UPDATE users SET ref_paid=1 WHERE id=?",(r["user_id"],))
                        c.execute("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(u["referrer"],"worker",str(bonus),"referral",f"Referral user {r['user_id']}",now()))
            target=r["user_id"]; usermsg=get_setting("withdraw_approved_text" if yes else "withdraw_rejected_text", "উইথড্র অনুমোদিত হয়েছে।" if yes else "উইথড্র বাতিল হয়েছে; টাকা ফেরত দেওয়া হয়েছে।")
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
        [InlineKeyboardButton("✏️ Withdrawal Request Sent",callback_data="adminset:withdraw_request_sent")],
        [InlineKeyboardButton("✏️ Withdrawal Instructions",callback_data="adminset:withdraw_instructions"),InlineKeyboardButton("✏️ Pending",callback_data="adminset:withdraw_pending")],
        [InlineKeyboardButton("✏️ Approved",callback_data="adminset:withdraw_approved"),InlineKeyboardButton("✏️ Rejected",callback_data="adminset:withdraw_rejected")],
        [InlineKeyboardButton("👥 ব্যবহারকারী তালিকা",callback_data="adminusers:0")],
        [InlineKeyboardButton("⚙️ ফি/বোনাস সেট",callback_data="adminset:withdraw_fee"),InlineKeyboardButton("⚙️ জব ফি",callback_data="adminset:job_fee")],
        [InlineKeyboardButton("🎁 রেফারেল হার",callback_data="adminset:referral_rate")],
        [InlineKeyboardButton("➕ ডিপোজিট মিনিমাম",callback_data="adminset:deposit_min"),InlineKeyboardButton("➖ উইথড্র মিনিমাম",callback_data="adminset:withdraw_min")],
        [InlineKeyboardButton("📱 রিচার্জ মিনিমাম",callback_data="adminset:recharge_min")],
        [InlineKeyboardButton("📥 ডিপোজিট যাচাই",callback_data="adminlist:deposit"),InlineKeyboardButton("📤 উত্তোলন যাচাই",callback_data="adminlist:withdraw")],
        [InlineKeyboardButton("🧾 কাজের প্রুফ যাচাই",callback_data="adminlist:submission"),InlineKeyboardButton("🔄 রিফ্রেশ",callback_data="adminrefresh")]
    ])
    await update.effective_message.reply_text(
        "🛠 BABLU STAR BD — অ্যাডমিন প্যানেল\n\n"
        f"👥 মোট ব্যবহারকারী: {users}\n📋 চালু কাজ: {jobs}\n"
        f"📥 অপেক্ষমাণ ডিপোজিট: {d}\n📤 অপেক্ষমাণ উত্তোলন: {w}\n🧾 অপেক্ষমাণ কাজের প্রুফ: {sub}\n\n"
        f"বিকাশ: {get_setting('bkash_number','সেট করা হয়নি')}\n"
        f"নগদ: {get_setting('nagad_number','সেট করা হয়নি')}\n"
        f"Support: {get_setting('support_email','mrshahidulislam8989@gmail.com')}\n"
        f"উইথড্র ফি: {Decimal(get_setting('withdraw_fee','0.15'))*100}% | জব ফি: {Decimal(get_setting('job_fee','0.05'))*100}% | রেফারেল: {Decimal(get_setting('referral_rate','0.10'))*100}%\n\n"
        "নম্বর, ইমেইল, ফি, মিনিমাম ও Withdrawal মেসেজ পরিবর্তন করতে নিচের বাটনে চাপ দিন।", reply_markup=kb)

async def cancel_cmd(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id): await banned_notice(update.effective_message); return
    await menu(update,ctx,"চলমান কাজ বাতিল হয়েছে।")
async def err(update,ctx):
    log.exception("Bot error",exc_info=ctx.error)
    if update and update.effective_message:
        try: await update.effective_message.reply_text("সমস্যা হয়েছে। আবার চেষ্টা করুন।")
        except: pass
def main():
    if not TOKEN: raise SystemExit("Inject Cloud-এ BOT_TOKEN সেট করুন।")
    if not ADMIN: raise SystemExit("Inject Cloud-এ ADMIN_ID (numeric Telegram ID) সেট করুন।")
    init(); app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("admin",admin)); app.add_handler(CommandHandler("ban",ban_cmd)); app.add_handler(CommandHandler("unban",unban_cmd))
    app.add_handler(CommandHandler("cancel",cancel_cmd))
    app.add_handler(CallbackQueryHandler(callback)); app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text)); app.add_error_handler(err)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__=="__main__": main()
