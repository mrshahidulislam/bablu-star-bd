import os ,logging ,re
import psycopg2
from psycopg2 .extras import RealDictCursor
from decimal import Decimal ,InvalidOperation ,ROUND_HALF_UP
from datetime import datetime ,timezone
from telegram import Update ,ReplyKeyboardMarkup ,InlineKeyboardMarkup ,InlineKeyboardButton
from telegram .ext import Application ,CommandHandler ,MessageHandler ,CallbackQueryHandler ,ContextTypes ,filters
TOKEN =os .getenv ("BOT_TOKEN","").strip ()
ADMIN =int (os .getenv ("ADMIN_ID","0")or "0")
DATABASE_URL =os .getenv ("DATABASE_URL","").strip ()
FEE =Decimal ("0.15")
REFERRAL =Decimal ("0.10")
logging .basicConfig (level =logging .INFO )
log =logging .getLogger ("bablu-star-bd")
CATS =[("📘 Facebook",2 ),("▶️ YouTube",2 ),("📸 Instagram",2 ),("🎵 TikTok",2 ),("𝕏 X (Twitter)",2 ),("👻 Snapchat",2 ),("✈️ Telegram",2 ),("💼 LinkedIn",2 ),]
TASK_TYPES =["👍 Like","👥 Follow","💬 Comment","🔁 Repost"]
BUYER =[["➕ কাজ পোস্ট","📋 আমার কাজ"],["💰 বায়ার ব্যালেন্স","➕ ডিপোজিট"],["📤 মোড বদল","🏠 মেইন মেনু"],["🔙 Back / ❌ Cancel"]]
WORKER =[["🔎 কাজ দেখুন","📨 আমার সাবমিশন"],["💰 ফ্রিল্যান্সার ব্যালেন্স","💸 উইথড্র"],["👥 রেফার লিংক","📤 মোড বদল"],["🏠 মেইন মেনু","🔙 Back / ❌ Cancel"]]
MAIN =[["🛒 Buyer Mode","🧑‍💻 Freelancer Mode"],["💳 পেমেন্ট তথ্য","📜 নিয়মাবলি"],["🆘 সাহায্য","🔙 Back / ❌ Cancel"]]
def now ():return datetime .now (timezone .utc ).isoformat (timespec ="seconds")
def m (v ):
    d =Decimal (str (v )).quantize (Decimal ("0.01"),rounding =ROUND_HALF_UP )
    return str (int (d ))if d ==d .to_integral_value ()else f"{d :.2f}"
def amount (s ):
    try :
        x =Decimal (str (s ).replace (",","").replace ("৳","").strip ())
        return x .quantize (Decimal ("0.01"),rounding =ROUND_HALF_UP )if x .is_finite ()and x >0 else None
    except (InvalidOperation ,ValueError ):return None
class PGCursor :
    def __init__ (self ,cursor ):
        self ._cursor =cursor
        self .lastrowid =None
        self .rowcount =-1
    def execute (self ,query ,params =None ):
        q =query .strip ()
        if q .upper ()=="BEGIN IMMEDIATE":
            q ="BEGIN"
        if q .upper ().startswith ("PRAGMA TABLE_INFO("):
            table =q [q .find ("(")+1 :q .rfind (")")].strip ().strip ("'\"")
            self ._cursor .execute ("SELECT column_name AS name FROM information_schema.columns "  "WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position",(table ,))
            rows =self ._cursor .fetchall ()
            self ._pragma_rows =[(i ,r ["name"])for i ,r in enumerate (rows )]
            return self
        ignore_insert =query .lstrip ().upper ().startswith ("INSERT OR IGNORE")
        if ignore_insert :
            q =re .sub (r"^\s*INSERT\s+OR\s+IGNORE\s+INTO","INSERT INTO",q ,flags =re .I )
            q =q .rstrip ().rstrip (";")+" ON CONFLICT DO NOTHING"
        q =q .replace ("?","%s")
        self .lastrowid =None
        if q .lstrip ().upper ().startswith ("INSERT INTO")and " RETURNING "not in q .upper ():
            table_match =re .match (r"\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)",q ,re .I )
            table =table_match .group (1 ).lower ()if table_match else ""
            if table in {"jobs","submissions","deposits","withdrawals","ledger","audit"}:
                q =q .rstrip ().rstrip (";")+" RETURNING id"
                self ._cursor .execute (q ,params or ())
                row =self ._cursor .fetchone ()
                if row :
                    self .lastrowid =row ["id"]
                self .rowcount =1
                return self
        self ._cursor .execute (q ,params or ())
        self .rowcount =self ._cursor .rowcount
        return self
    def fetchone (self ):
        if hasattr (self ,"_pragma_rows"):
            return self ._pragma_rows .pop (0 )if self ._pragma_rows else None
        return self ._cursor .fetchone ()
    def fetchall (self ):
        if hasattr (self ,"_pragma_rows"):
            rows ,self ._pragma_rows =self ._pragma_rows ,[]
            return rows
        return self ._cursor .fetchall ()
    def __iter__ (self ):
        return iter (self .fetchall ())
class PGConnection :
    def __init__ (self ):
        if not DATABASE_URL :
            raise RuntimeError ("Hostless Secrets-এ DATABASE_URL সেট করুন (Neon PostgreSQL connection string)।")
        self .raw =psycopg2 .connect (DATABASE_URL ,sslmode ="require",connect_timeout =20 )
        self .raw .autocommit =False
    def __enter__ (self ):
        return self
    def __exit__ (self ,exc_type ,exc ,tb ):
        try :
            if exc_type :
                self .raw .rollback ()
            else :
                self .raw .commit ()
        finally :
            self .raw .close ()
        return False
    def execute (self ,query ,params =None ):
        return PGCursor (self .raw .cursor (cursor_factory =RealDictCursor )).execute (query ,params )
    def executescript (self ,script ):
        cur =self .raw .cursor ()
        for statement in script .split (";"):
            statement =statement .strip ()
            if statement :
                cur .execute (statement )
        cur .close ()
    def commit (self ):
        self .raw .commit ()
    def rollback (self ):
        self .raw .rollback ()
def conn ():
    return PGConnection ()
def init ():
    with conn ()as c :
        c .executescript ("""
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
        defaults ={"deposit_min":"50","withdraw_min":"100","recharge_min":"50","withdraw_fee":"0.15","job_fee":"0.05","referral_rate":"0.10","withdraw_request_sent_text":"✅ আপনার Withdrawal Request সফলভাবে জমা হয়েছে।\\n\\n🕒 অ্যাডমিন যাচাই করার পর পেমেন্ট পাঠানো হবে।\\n📌 অনুগ্রহ করে অপেক্ষা করুন।\\n\\n— BABLU STAR BD","withdraw_instructions_text":"💸 উইথড্রের পরিমাণ লিখুন। বিকাশ/নগদে সর্বনিম্ন ৳১০০, রিচার্জে ৳৫০। সার্ভিস ফি ১৫%।","withdraw_pending_text":"⏳ আপনার Withdrawal Request অপেক্ষমাণ আছে। অ্যাডমিন যাচাই করছেন।","withdraw_approved_text":"✅ আপনার Withdrawal Request অনুমোদিত হয়েছে।","withdraw_rejected_text":"❌ আপনার Withdrawal Request বাতিল হয়েছে। টাকা আপনার ব্যালেন্সে ফেরত দেওয়া হয়েছে."}
        for key ,value in defaults .items ():
            c .execute ("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",(key ,value ))
        c .execute ("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS proof_type TEXT NOT NULL DEFAULT 'text'")
        c .execute ("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS rejection_reason TEXT NOT NULL DEFAULT ''")
        c .execute ("ALTER TABLE users ADD COLUMN IF NOT EXISTS buyer TEXT DEFAULT '0'")
        c .execute ("ALTER TABLE users ADD COLUMN IF NOT EXISTS worker TEXT DEFAULT '0'")
        c .execute ("ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_reason TEXT DEFAULT ''")
def user_row (u ):
    with conn ()as c :
        r =c .execute ("SELECT * FROM users WHERE id=?",(u .id ,)).fetchone ()
        if not r :
            c .execute ("INSERT INTO users(id,username,first_name,created) VALUES(?,?,?,?)",(u .id ,u .username or "",u .first_name or "",now ()))
        else :c .execute ("UPDATE users SET username=?,first_name=? WHERE id=?",(u .username or "",u .first_name or "",u .id ))
def kb (mode ):
    return ReplyKeyboardMarkup (BUYER if mode =="buyer"else WORKER if mode =="worker"else MAIN ,resize_keyboard =True )
def is_banned (uid ):
    with conn ()as c :
        r =c .execute ("SELECT banned FROM users WHERE id=?",(uid ,)).fetchone ()
    return bool (r and r ["banned"])
def support_only_kb ():
    return ReplyKeyboardMarkup ([["🆘 সাহায্য"]],resize_keyboard =True )
async def banned_notice (target ):
    await target .reply_text ("⛔ আপনার অ্যাকাউন্ট সাময়িকভাবে বন্ধ আছে। আপিল বা সহায়তার জন্য নিচের 🆘 সাহায্য বাটন ব্যবহার করুন।",reply_markup =support_only_kb ())
async def menu (update ,ctx ,msg ="মেনু থেকে অপশন বেছে নিন."):
    if update .effective_user and is_banned (update .effective_user .id ):
        target =update .effective_message or update .callback_query .message
        await banned_notice (target );return
    for k in ("stage","form","proof_job","pending_submission_rejection","admin_setting_key","admin_text_key","proof_file_id"):ctx .user_data .pop (k ,None )
    target =update .effective_message or update .callback_query .message
    await target .reply_text (msg ,reply_markup =kb (ctx .user_data .get ("mode","main")))
async def admin_msg (ctx ,text ,kind =None ,item =None ):
    if not ADMIN :return
    markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("✅ Approve",callback_data =f"review:{kind }:yes:{item }"),InlineKeyboardButton ("❌ Reject",callback_data =f"review:{kind }:no:{item }")]])if kind else None
    try :await ctx .bot .send_message (ADMIN ,text ,reply_markup =markup )
    except Exception :log .exception ("admin notification failed")
async def start (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    u =update .effective_user ;user_row (u )
    if is_banned (u .id ):await banned_notice (update .effective_message );return
    if ctx .args and ctx .args [0 ].startswith ("ref_"):
        try :
            ref =int (ctx .args [0 ][4 :])
            if ref !=u .id :
                with conn ()as c :
                    r =c .execute ("SELECT referrer FROM users WHERE id=?",(u .id ,)).fetchone ()
                    if r and r ["referrer"]is None and c .execute ("SELECT 1 FROM users WHERE id=?",(ref ,)).fetchone ():
                        c .execute ("UPDATE users SET referrer=? WHERE id=?",(ref ,u .id ))
        except ValueError :pass
    ctx .user_data ["mode"]="main"
    await menu (update ,ctx ,get_bot_text ("welcome"))
async def text (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    u =update .effective_user ;user_row (u );t =(update .effective_message .text or "").strip ()
    with conn ()as c :
        r =c .execute ("SELECT banned FROM users WHERE id=?",(u .id ,)).fetchone ()
    if r and r ["banned"]:
        if t =="🆘 সাহায্য":
            await update .effective_message .reply_text (f"📩 Support Email: {get_setting ('support_email','mrshahidulislam8989@gmail.com')}\nআপনার Telegram ID: {u .id }")
        else :await banned_notice (update .effective_message )
        return
    if t in ("🔙 Back / ❌ Cancel","🔙 Back","❌ Cancel","/cancel"):
        await menu (update ,ctx ,get_bot_text ("cancelled"));return
    if u .id ==ADMIN and ctx .user_data .get ("admin_user_search"):
        ctx .user_data .pop ("admin_user_search",None )
        await show_user_list (update .effective_message ,search_term =t )
        return
    if ctx .user_data .get ("pending_submission_rejection"):
        sid =int (ctx .user_data .pop ("pending_submission_rejection"))
        if len (t )<3 or len (t )>1000 :
            ctx .user_data ["pending_submission_rejection"]=sid
            await update .effective_message .reply_text ("Reject করার কারণ ৩–১০০০ অক্ষরের মধ্যে লিখুন। বাতিল করতে /cancel লিখুন।")
            return
        await review_from_reason (update ,ctx ,sid ,t )
        return
    st =ctx .user_data .get ("stage")
    if st :
        await stage_input (update ,ctx ,st ,t );return
    mode =ctx .user_data .get ("mode","main")
    if t =="🛒 Buyer Mode":ctx .user_data ["mode"]="buyer";await menu (update ,ctx ,get_bot_text ("buyer_mode_on"))
    elif t =="🧑‍💻 Freelancer Mode":ctx .user_data ["mode"]="worker";await menu (update ,ctx ,get_bot_text ("worker_mode_on"))
    elif t =="📤 মোড বদল":ctx .user_data ["mode"]="worker"if mode =="buyer"else "buyer";await menu (update ,ctx ,get_bot_text ("mode_changed"))
    elif t =="🏠 মেইন মেনু":ctx .user_data ["mode"]="main";await menu (update ,ctx ,"মেইন মেনু।")
    elif t =="➕ কাজ পোস্ট"and mode =="buyer":
        rows =[[InlineKeyboardButton (f"{name } — মিনিমাম ৳{price }",callback_data =f"cat:{i }")]for i ,(name ,price )in enumerate (CATS )]
        await update .effective_message .reply_text (get_bot_text ("choose_category"),reply_markup =InlineKeyboardMarkup (rows ))
    elif t =="📋 আমার কাজ"and mode =="buyer":
        with conn ()as c :rs =c .execute ("SELECT * FROM jobs WHERE buyer_id=? ORDER BY id DESC LIMIT 20",(u .id ,)).fetchall ()
        if not rs :
            await update .effective_message .reply_text ("আপনার কোনো কাজ নেই।")
        else :
            for r in rs :
                count_row =None
                with conn ()as c :
                    count_row =c .execute ("SELECT COUNT(*) n FROM submissions WHERE job_id=?",(r ["id"],)).fetchone ()
                msg =f"কাজ #{r ['id']} — {r ['title']}\nবাকি: {r ['count']} | প্রতি কাজ: ৳{m (r ['price'])} | অবস্থা: {r ['status']}\nমোট সাবমিশন: {count_row ['n']}"
                await update .effective_message .reply_text (msg ,reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("📨 জমা দেওয়া প্রুফ দেখুন",callback_data =f"buyerjobsubs:{r ['id']}")]]))
    elif t =="💰 বায়ার ব্যালেন্স"and mode =="buyer":await balance (update ,u .id ,"buyer")
    elif t =="💰 ফ্রিল্যান্সার ব্যালেন্স"and mode =="worker":await balance (update ,u .id ,"worker")
    elif t =="➕ ডিপোজিট"and mode =="buyer":
        ctx .user_data ["stage"]="dep_amount";await update .effective_message .reply_text (get_bot_text ("deposit_amount_prompt"))
    elif t =="🔎 কাজ দেখুন"and mode =="worker":
        with conn ()as c :rs =c .execute ("SELECT * FROM jobs WHERE status='open' AND count>0 ORDER BY id DESC LIMIT 10").fetchall ()
        if not rs :await update .effective_message .reply_text (get_bot_text ("no_jobs"))
        for r in rs :
            cat =CATS [int (r ["cat"])][0 ]if 0 <=int (r ["cat"])<len (CATS )else "পুরোনো/বন্ধ ক্যাটাগরি"
            msg =f"কাজ #{r ['id']} — {cat }\n{r ['title']}\n{r ['description']}\nপ্রতি কাজ ৳{m (r ['price'])} | বাকি {r ['count']}\nপ্রুফের ধরন: {r ['proof_type']}\nনিয়ম/প্রুফ: {r ['rules']}"
            await update .effective_message .reply_text (msg ,reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("📨 প্রুফ জমা দিন",callback_data =f"submit:{r ['id']}")]]))
    elif t =="📨 আমার সাবমিশন"and mode =="worker":
        with conn ()as c :rs =c .execute ("SELECT s.id,s.job_id,s.status,j.title FROM submissions s JOIN jobs j ON j.id=s.job_id WHERE s.worker_id=? ORDER BY s.id DESC LIMIT 20",(u .id ,)).fetchall ()
        await update .effective_message .reply_text ("\n".join ([f"#{r ['id']} কাজ #{r ['job_id']} {r ['title']} — {r ['status']}"for r in rs ])or "কোনো সাবমিশন নেই।")
    elif t =="💸 উইথড্র"and mode =="worker":
        ctx .user_data ["stage"]="wd_gross"
        instructions =get_setting ("withdraw_instructions_text","💸 উইথড্রের পরিমাণ লিখুন। বিকাশ/নগদে সর্বনিম্ন ৳১০০, রিচার্জে ৳৫০। সার্ভিস ফি ১৫%।")
        await update .effective_message .reply_text (instructions )
    elif t =="👥 রেফার লিংক"and mode =="worker":
        me =await ctx .bot .get_me ();await update .effective_message .reply_text (f"https://t.me/{me .username }?start=ref_{u .id }\nরেফার করা ব্যক্তির প্রথম অনুমোদিত উইথড্রয়ের মোট অঙ্কের ১০% একবার বোনাস।")
    elif t =="💳 পেমেন্ট তথ্য":
        await update .effective_message .reply_text (get_bot_text ("payment_info"))
    elif t =="📜 নিয়মাবলি":
        await update .effective_message .reply_text (get_bot_text ("rules"))
    elif t =="🆘 সাহায্য":
        await update .effective_message .reply_text (get_bot_text ("support",email =get_setting ("support_email","mrshahidulislam8989@gmail.com"),uid =u .id ))
    else :await update .effective_message .reply_text (get_bot_text ("no_menu_button"))
async def balance (update ,uid ,wallet ):
    with conn ()as c :r =c .execute (f"SELECT {wallet } FROM users WHERE id=?",(uid ,)).fetchone ()
    await update .effective_message .reply_text (f"আপনার ব্যালেন্স: ৳{m (r [wallet ]or 0 )}")
async def stage_input (update ,ctx ,st ,t ):
    uid =update .effective_user .id ;msg =update .effective_message
    if st =="admin_text":
        if uid !=ADMIN :
            ctx .user_data .pop ("stage",None );ctx .user_data .pop ("admin_text_key",None )
            await msg .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        key =ctx .user_data .pop ("admin_text_key",None )
        if key not in EDITABLE_TEXTS :
            ctx .user_data .pop ("stage",None );await msg .reply_text ("লেখাটি পাওয়া যায়নি। /admin লিখুন।");return
        if not 1 <=len (t )<=3500 :
            ctx .user_data ["admin_text_key"]=key
            await msg .reply_text ("লেখা ১–৩৫০০ অক্ষরের মধ্যে দিন।")
            return
        storage_key =EDITABLE_TEXTS [key ][1 ]if key .startswith ("withdraw_")else "bot_text_"+key
        set_setting (storage_key ,t ,uid )
        ctx .user_data .pop ("stage",None )
        await msg .reply_text ("✅ লেখা সফলভাবে সেভ হয়েছে। পরিবর্তন সঙ্গে সঙ্গে কার্যকর হবে।",reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("📝 সব লেখা এডিট",callback_data ="admintext:list")],[InlineKeyboardButton ("🏠 Admin Panel",callback_data ="adminrefresh")]]))
        return
    if st =="admin_setting":
        if uid !=ADMIN :
            ctx .user_data .pop ("stage",None );ctx .user_data .pop ("admin_setting_key",None )
            await msg .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        key =ctx .user_data .pop ("admin_setting_key",None )
        if not key :
            ctx .user_data .pop ("stage",None );await msg .reply_text ("সেটিং পাওয়া যায়নি। /admin খুলুন।");return
        if key in ("bkash_number","nagad_number"):
            compact =re .sub (r"[\s-]","",t )
            if not re .fullmatch (r"\+?\d{10,15}",compact ):
                await msg .reply_text ("সঠিক মোবাইল নম্বর দিন (১০–১৫টি সংখ্যা, প্রয়োজনে শুরুতে +)।")
                ctx .user_data ["admin_setting_key"]=key
                return
            t =compact
        elif key =="support_email"and not re .fullmatch (r"[^\s@]+@[^\s@]+\.[^\s@]+",t ):
            await msg .reply_text ("সঠিক ইমেইল দিন।")
            ctx .user_data ["admin_setting_key"]=key
            return
        elif key .startswith ("withdraw_")and not 5 <=len (t )<=3500 :
            await msg .reply_text ("মেসেজ ৫–৩৫০০ অক্ষরের মধ্যে লিখুন।")
            ctx .user_data ["admin_setting_key"]=key
            return
        set_setting (key ,t ,uid );ctx .user_data .pop ("stage",None )
        await msg .reply_text ("✅ সেটিং সেভ হয়েছে। পরিবর্তন দেখতে /admin লিখুন।")
    elif st =="job_title":
        if not 3 <=len (t )<=100 :await msg .reply_text ("শিরোনাম ৩–১০০ অক্ষরের দিন।");return
        ctx .user_data ["form"]["title"]=t ;ctx .user_data ["stage"]="job_desc";await msg .reply_text (get_bot_text ("job_desc_prompt"))
    elif st =="job_desc":
        if not 10 <=len (t )<=2000 :await msg .reply_text ("বিবরণ ১০–২০০০ অক্ষরের দিন।");return
        ctx .user_data ["form"]["desc"]=t ;ctx .user_data ["stage"]="job_count";await msg .reply_text (get_bot_text ("job_count_prompt"))
    elif st =="job_count":
        try :n =int (t );assert 1 <=n <=100000
        except :await msg .reply_text ("১–১০০০০০-এর মধ্যে পূর্ণসংখ্যা দিন।");return
        ctx .user_data ["form"]["count"]=n ;ci =ctx .user_data ["form"]["cat"];name ,mi =CATS [ci ];ctx .user_data ["stage"]="job_price"
        await msg .reply_text (f"{name } ক্যাটাগরির সর্বনিম্ন ৳{mi }; প্রতি কাজের দাম ৳{mi } বা তার বেশি লিখুন। সর্বোচ্চ সীমা নেই।")
    elif st =="job_price":
        p =amount (t );f =ctx .user_data ["form"];mi =CATS [f ["cat"]][1 ]
        if p is None or p <mi or p <2 :await msg .reply_text (f"প্রতি কাজ কমপক্ষে ৳{mi } হতে হবে।");return
        f ["price"]=str (p );ctx .user_data ["stage"]="job_rules";await msg .reply_text (get_bot_text ("job_rules_prompt"))
    elif st =="job_rules":
        if len (t )<3 or len (t )>2000 :await msg .reply_text ("৩–২০০০ অক্ষরের নিয়ম লিখুন।");return
        f =ctx .user_data ["form"];f ["rules"]=t ;ctx .user_data ["stage"]="job_proof_type";await msg .reply_text (get_bot_text ("proof_type_prompt"),reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("📸 Screenshot only",callback_data ="proof_type:photo")],[InlineKeyboardButton ("📝 Text only",callback_data ="proof_type:text")],[InlineKeyboardButton ("📸 + 📝 Screenshot and text",callback_data ="proof_type:both")]]));return
    elif st =="dep_amount":
        a =amount (t )
        if a is None or a <50 :await msg .reply_text ("সর্বনিম্ন ডিপোজিট ৳৫০।");return
        ctx .user_data ["form"]={"amount":str (a )};ctx .user_data ["stage"]="dep_method"
        await msg .reply_text (get_bot_text ("deposit_method_prompt"),reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("🟣 বিকাশ (bKash)",callback_data ="depmethod:bKash"),InlineKeyboardButton ("🟠 নগদ (Nagad)",callback_data ="depmethod:Nagad")]]))
    elif st =="dep_method":
        aliases ={"বিকাশ":"bKash","বিকাশ (bKash)":"bKash","নগদ":"Nagad","নগদ (Nagad)":"Nagad","bKash":"bKash","Nagad":"Nagad"}
        method =aliases .get (t )
        if method not in ("bKash","Nagad"):
            await msg .reply_text ("নিচের বাটন থেকে বিকাশ অথবা নগদ বেছে নিন।",reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("🟣 বিকাশ (bKash)",callback_data ="depmethod:bKash"),InlineKeyboardButton ("🟠 নগদ (Nagad)",callback_data ="depmethod:Nagad")]]))
            return
        ctx .user_data ["form"]["method"]=method ;ctx .user_data ["stage"]="dep_txn";await msg .reply_text (get_bot_text ("deposit_txn_prompt"))
    elif st =="dep_txn":
        f =ctx .user_data ["form"]
        try :
            with conn ()as c :cur =c .execute ("INSERT INTO deposits(user_id,amount,method,txn,created) VALUES(?,?,?,?,?)",(uid ,f ["amount"],f ["method"],t ,now ()));did =cur .lastrowid
        except psycopg2 .IntegrityError :await msg .reply_text ("এই Txn ID আগে জমা হয়েছে।");return
        ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None )
        await msg .reply_text ("✅ আপনার ডিপোজিট অনুরোধ জমা হয়েছে। অ্যাডমিন Transaction ID যাচাই করে ব্যালেন্স যোগ করবেন।")
        await admin_msg (ctx ,f"Deposit #{did }\nUser: {uid }\nAmount: ৳{f ['amount']}\nMethod: {f ['method']}\nTxn: {t }","deposit",did )
    elif st =="wd_gross":
        g =amount (t )
        if g is None or g <50 :await msg .reply_text ("সর্বনিম্ন পরিমাণ ৳৫০। বিকাশ/নগদে তুলতে কমপক্ষে ৳১০০ লাগবে।");return
        fee =(g *Decimal (get_setting ("withdraw_fee","0.15"))).quantize (Decimal ("0.01"),rounding =ROUND_HALF_UP );net =g -fee
        with conn ()as c :r =c .execute ("SELECT worker FROM users WHERE id=?",(uid ,)).fetchone ()
        if Decimal (r ["worker"]or 0 )<g :await msg .reply_text (f"ব্যালেন্স কম। আপনার ব্যালেন্স ৳{m (r ['worker'])}।");return
        ctx .user_data ["form"]={"gross":str (g ),"fee":str (fee ),"net":str (net )};ctx .user_data ["stage"]="wd_method"
        await msg .reply_text (f"🧾 উইথড্র হিসাব\nমোট: ৳{m (g )}\nসার্ভিস ফি (১৫%): ৳{m (fee )}\nআপনি পাবেন: ৳{m (net )}\n\n" "নিচের বাটন থেকে পেমেন্ট পদ্ধতি বেছে নিন।",reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("🟣 বিকাশ",callback_data ="wdmethod:bKash"),InlineKeyboardButton ("🟠 নগদ",callback_data ="wdmethod:Nagad")],[InlineKeyboardButton ("📱 মোবাইল রিচার্জ",callback_data ="wdmethod:Recharge")]]))
    elif st =="wd_method":
        aliases ={"বিকাশ":"bKash","বিকাশ (bKash)":"bKash","নগদ":"Nagad","নগদ (Nagad)":"Nagad","মোবাইল রিচার্জ":"Recharge","রিচার্জ":"Recharge","bKash":"bKash","Nagad":"Nagad","Recharge":"Recharge"}
        method =aliases .get (t )
        if method not in ("bKash","Nagad","Recharge"):
            await msg .reply_text (get_bot_text ("withdraw_method_prompt"),reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("🟣 বিকাশ",callback_data ="wdmethod:bKash"),InlineKeyboardButton ("🟠 নগদ",callback_data ="wdmethod:Nagad")],[InlineKeyboardButton ("📱 মোবাইল রিচার্জ",callback_data ="wdmethod:Recharge")]]))
            return
        ctx .user_data ["form"]["method"]=method ;ctx .user_data ["stage"]="wd_account"
        label ={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[method ]
        await msg .reply_text (f"পেমেন্ট পদ্ধতি: {label }\n"+get_bot_text ("withdraw_account_prompt"))
    elif st =="wd_account":
        if len (t )<8 or len (t )>40 :await msg .reply_text ("সঠিক নম্বর লিখুন।");return
        f =ctx .user_data ["form"];g =Decimal (f ["gross"])
        with conn ()as c :
            c .execute ("BEGIN IMMEDIATE");r =c .execute ("SELECT worker FROM users WHERE id=?",(uid ,)).fetchone ();bal =Decimal (r ["worker"]or 0 )
            if bal <g :c .rollback ();await msg .reply_text ("ব্যালেন্স যথেষ্ট নেই।");return
            c .execute ("UPDATE users SET worker=? WHERE id=?",(str (bal -g ),uid ))
            cur =c .execute ("INSERT INTO withdrawals(user_id,gross,fee,net,method,account,created) VALUES(?,?,?,?,?,?,?)",(uid ,f ["gross"],f ["fee"],f ["net"],f ["method"],t ,now ()));wid =cur .lastrowid
        ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None )
        await msg .reply_text (get_setting ("withdraw_request_sent_text","✅ আপনার Withdrawal Request জমা হয়েছে। অ্যাডমিন যাচাই করে পেমেন্ট পাঠাবেন।")+"\n\n"+get_setting ("withdraw_pending_text","⏳ অ্যাডমিন যাচাই করছেন।"))
        await admin_msg (ctx ,f"Withdrawal #{wid }\nUser: {uid }\nGross: ৳{f ['gross']}\nFee: ৳{f ['fee']}\nNet: ৳{f ['net']}\nMethod: {f ['method']}\nAccount: {t }","withdraw",wid )
    elif st =="proof_text":
        await save_proof (update ,ctx ,ctx .user_data .get ("proof_job"),t ,"")
    elif st =="proof_both_text":
        await save_proof (update ,ctx ,ctx .user_data .get ("proof_job"),t ,ctx .user_data .get ("proof_file_id",""))
async def show_buyer_job_submissions (message ,buyer_id ,job_id ):
    with conn ()as c :
        job =c .execute ("SELECT id,title,buyer_id FROM jobs WHERE id=?",(job_id ,)).fetchone ()
        if not job or job ["buyer_id"]!=buyer_id :
            await message .reply_text ("এই কাজের তথ্য দেখার অনুমতি আপনার নেই।")
            return
        rows =c .execute ("SELECT s.*,u.username,u.first_name FROM submissions s LEFT JOIN users u ON u.id=s.worker_id WHERE s.job_id=? ORDER BY s.id DESC LIMIT 50",(job_id ,)).fetchall ()
    if not rows :
        await message .reply_text (f"কাজ #{job_id } — {job ['title']}\nএখনো কোনো সাবমিশন জমা পড়েনি।")
        return
    await message .reply_text (f"📨 কাজ #{job_id } — {job ['title']}\nমোট {len (rows )}টি সাম্প্রতিক সাবমিশন:")
    for row in rows :
        details =(f"Submission #{row ['id']}\nWorker ID: {row ['worker_id']}\n" f"Username: @{row ['username']or '—'}\nStatus: {row ['status']}\n" f"Proof: {row ['proof']or '[Screenshot attached]'}")
        if row .get ("rejection_reason"):
            details +=f"\nReject reason: {row ['rejection_reason']}"
        if row .get ("file_id"):
            try :
                await message .reply_photo (row ["file_id"],caption =details [:1024 ])
                if len (details )>1024 :
                    await message .reply_text (details [1024 :])
            except Exception :
                log .exception ("buyer submission photo display failed")
                await message .reply_text (details )
        else :
            await message .reply_text (details )
async def save_proof (update ,ctx ,jid ,proof ,fileid ):
    uid =update .effective_user .id
    if is_banned (uid ):await banned_notice (update .effective_message );return
    with conn ()as c :
        job =c .execute ("SELECT * FROM jobs WHERE id=?",(jid ,)).fetchone ()
        if not job or job ["status"]!="open"or job ["count"]<1 :await update .effective_message .reply_text ("কাজটি আর খোলা নেই।");return
        if job ["buyer_id"]==uid :await update .effective_message .reply_text ("নিজের কাজের প্রুফ দিতে পারবেন না।");return
        try :cur =c .execute ("INSERT INTO submissions(job_id,worker_id,proof,file_id,created) VALUES(?,?,?,?,?)",(jid ,uid ,proof ,fileid ,now ()));sid =cur .lastrowid
        except psycopg2 .IntegrityError :await update .effective_message .reply_text (get_bot_text ("proof_already_submitted"));return
    ctx .user_data .pop ("stage",None );ctx .user_data .pop ("proof_job",None );ctx .user_data .pop ("proof_type",None );ctx .user_data .pop ("proof_file_id",None )
    await update .effective_message .reply_text (get_bot_text ("proof_submitted",sid =sid ))
    proof_text =f"Submission #{sid }\nJob #{jid }: {job ['title']}\nWorker: {uid }\nProof: {proof or '[photo]'}"
    await admin_msg (ctx ,proof_text ,"submission",sid )
    if fileid and ADMIN :
        try :await ctx .bot .send_photo (ADMIN ,fileid ,caption =f"প্রুফ Submission #{sid }")
        except Exception :log .exception ("admin proof photo notification failed")
    try :
        buyer_id =job ["buyer_id"]
        if fileid :
            await ctx .bot .send_photo (buyer_id ,fileid ,caption =proof_text [:1024 ])
            if len (proof_text )>1024 :
                await ctx .bot .send_message (buyer_id ,proof_text [1024 :])
        else :
            await ctx .bot .send_message (buyer_id ,proof_text )
    except Exception :
        log .exception ("buyer proof notification failed")
async def photo (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    user_row (update .effective_user )
    if is_banned (update .effective_user .id )and update .effective_user .id !=ADMIN :
        await banned_notice (update .effective_message );return
    st =ctx .user_data .get ("stage")
    if st not in ("proof_photo","proof_both_photo"):await update .effective_message .reply_text ("আগে কাজের প্রুফ জমা দিন বাটন চাপুন এবং প্রয়োজনীয় ধাপ অনুসরণ করুন।");return
    fid =update .effective_message .photo [-1 ].file_id
    if st =="proof_both_photo":
        ctx .user_data ["proof_file_id"]=fid ;ctx .user_data ["stage"]="proof_both_text"
        await update .effective_message .reply_text (get_bot_text ("proof_photo_received"))
    else :await save_proof (update ,ctx ,ctx .user_data .get ("proof_job"),"Screenshot proof",fid )
async def show_user_list (message ,page =0 ,search_term =None ):
    if search_term :
        term =search_term .strip ()
        with conn ()as c :
            if term .isdigit ():
                rows =c .execute ("SELECT id,username,first_name,banned FROM users WHERE id=? ORDER BY id DESC LIMIT 20",(int (term ),)).fetchall ()
                total =len (rows )
            else :
                rows =c .execute ("SELECT id,username,first_name,banned FROM users WHERE username ILIKE ? OR first_name ILIKE ? ORDER BY id DESC LIMIT 20",(f"%{term }%",f"%{term }%")).fetchall ()
                total =len (rows )
        heading =f"🔎 Search: {term }\nম্যাচ: {total } জন"
        keyboard =[[InlineKeyboardButton (f"{r ['id']} | @{r ['username']or '—'} | {'⛔ BANNED'if r ['banned']else '🟢 ACTIVE'}",callback_data =f"userdetail:{r ['id']}")]for r in rows ]
        keyboard .append ([InlineKeyboardButton ("🔙 User List",callback_data ="adminusers:0")])
    else :
        page =max (0 ,int (page ));offset =page *20
        with conn ()as c :
            total =c .execute ("SELECT COUNT(*) n FROM users").fetchone ()["n"]
            rows =c .execute ("SELECT id,username,first_name,banned FROM users ORDER BY id DESC LIMIT 20 OFFSET ?",(offset ,)).fetchall ()
        pages =max (1 ,(int (total )+19 )//20 )
        heading =f"👥 User List\nমোট নিবন্ধিত ইউজার: {total } জন\nসর্বশেষ ২০ জন ইউজার\nপৃষ্ঠা: {page +1 }/{pages }"
        keyboard =[[InlineKeyboardButton (f"{r ['id']} | @{r ['username']or '—'} | {'⛔ BANNED'if r ['banned']else '🟢 ACTIVE'}",callback_data =f"userdetail:{r ['id']}")]for r in rows ]
        nav =[]
        if page >0 :nav .append (InlineKeyboardButton ("⬅️ Previous",callback_data =f"adminusers:{page -1 }"))
        if offset +20 <int (total ):nav .append (InlineKeyboardButton ("Next ➡️",callback_data =f"adminusers:{page +1 }"))
        if nav :keyboard .append (nav )
        keyboard .append ([InlineKeyboardButton ("🔍 Search User ID / Username",callback_data ="adminusers:search"),InlineKeyboardButton ("🔄 Refresh",callback_data =f"adminusers:{page }")])
        keyboard .append ([InlineKeyboardButton ("🏠 Admin Panel",callback_data ="adminrefresh")])
    if not rows :heading +="\n\nকোনো User পাওয়া যায়নি।"
    await message .reply_text (heading ,reply_markup =InlineKeyboardMarkup (keyboard ))
async def show_user_detail (message ,target_id ):
    with conn ()as c :
        r =c .execute ("SELECT id,username,first_name,buyer,worker,banned,created,ban_reason FROM users WHERE id=?",(target_id ,)).fetchone ()
    if not r :
        await message .reply_text ("ইউজার পাওয়া যায়নি।");return
    buyer =Decimal (r ['buyer']or 0 );worker =Decimal (r ['worker']or 0 );total =buyer +worker
    status ="⛔ BANNED"if r ['banned']else "🟢 ACTIVE"
    text_msg =(f"👤 User Details\n\n🆔 User ID: {r ['id']}\n👤 Username: @{r ['username']or '—'}\nনাম: {r ['first_name']or '—'}\n\n" f"💼 Freelancer Balance: ৳{m (worker )}\n🛒 Buyer Balance: ৳{m (buyer )}\n💰 Total Balance: ৳{m (total )}\n\n" f"Status: {status }\nনিবন্ধনের সময়: {r ['created']or '—'}\nBan Reason: {r ['ban_reason']or '—'}")
    action =InlineKeyboardButton ("✅ Unban",callback_data =f"userban:un:{r ['id']}")if r ['banned']else InlineKeyboardButton ("⛔ Ban",callback_data =f"userban:ban:{r ['id']}")
    await message .reply_text (text_msg ,reply_markup =InlineKeyboardMarkup ([[action ],[InlineKeyboardButton ("🔙 User List",callback_data ="adminusers:0")]]))
async def callback (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    q =update .callback_query ;await q .answer ();d =q .data or "";uid =q .from_user .id ;user_row (q .from_user )
    if is_banned (uid )and d !="support":await banned_notice (q .message );return
    if d =="support":
        await q .message .reply_text (f"📩 Support Email: {get_setting ('support_email','mrshahidulislam8989@gmail.com')}\nআপনার Telegram ID: {uid }");return
    if d =="adminrefresh":
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        await q .message .reply_text ("ড্যাশবোর্ড আপডেট করতে /admin লিখুন।")
    elif d =="admintext:list":
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        await show_editable_texts (q .message )
    elif d .startswith ("admintext:edit:"):
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        key =d .split (":",2 )[2 ]
        if key not in EDITABLE_TEXTS :
            await q .message .reply_text ("লেখাটি পাওয়া যায়নি।");return
        label ,default =EDITABLE_TEXTS [key ]
        current =get_setting (default if key .startswith ("withdraw_")else "bot_text_"+key ,"")
        if key .startswith ("withdraw_"):
            current =get_setting (default ,"")
        if not current :
            current =default if not key .startswith ("withdraw_")else ""
        ctx .user_data ["admin_text_key"]=key
        ctx .user_data ["stage"]="admin_text"
        await q .message .reply_text (f"✏️ {label }\n\nবর্তমান লেখা:\n{current or 'এখনো সেট করা হয়নি'}\n\nনতুন সম্পূর্ণ লেখা পাঠান। বাতিল করতে /cancel লিখুন।")
    elif d .startswith ("adminset:"):
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        key =d .split (":",1 )[1 ]
        allowed ={"bkash":"bkash_number","nagad":"nagad_number","support_email":"support_email","withdraw_request_sent":"withdraw_request_sent_text","withdraw_instructions":"withdraw_instructions_text","withdraw_pending":"withdraw_pending_text","withdraw_approved":"withdraw_approved_text","withdraw_rejected":"withdraw_rejected_text"}
        if key not in allowed :return
        ctx .user_data ["admin_setting_key"]=allowed [key ]
        ctx .user_data ["stage"]="admin_setting"
        labels ={"bkash":"বিকাশের পেমেন্ট গ্রহণের নম্বর","nagad":"নগদের পেমেন্ট গ্রহণের নম্বর","support_email":"সাপোর্ট ইমেইল","withdraw_request_sent":"Withdrawal Request জমা দেওয়ার পর পাঠানোর মেসেজ","withdraw_instructions":"Withdrawal নির্দেশনা","withdraw_pending":"Withdrawal Pending মেসেজ","withdraw_approved":"Withdrawal Approved মেসেজ","withdraw_rejected":"Withdrawal Rejected মেসেজ"}
        await q .message .reply_text (f"{labels [key ]} লিখুন। শুধু অ্যাডমিনের দেওয়া সঠিক তথ্য দিন। বাতিল করতে /cancel লিখুন।")
    elif d .startswith ("buyerjobsubs:"):
        job_id =int (d .split (":",1 )[1 ])
        await show_buyer_job_submissions (q .message ,uid ,job_id )
    elif d =="adminlist:users":
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        await show_user_list (q .message ,0 )
    elif d .startswith ("adminusers:"):
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        arg =d .split (":",1 )[1 ]
        if arg =="search":
            ctx .user_data ["admin_user_search"]=True
            await q .message .reply_text ("User ID অথবা Username লিখুন। যেমন: 123456789 অথবা username")
        else :
            await show_user_list (q .message ,int (arg ))
    elif d .startswith ("userdetail:"):
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        await show_user_detail (q .message ,int (d .split (":",1 )[1 ]))
    elif d .startswith ("userban:"):
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        _ ,action ,raw_id =d .split (":");target_id =int (raw_id )
        with conn ()as c :
            target =c .execute ("SELECT id,username,banned FROM users WHERE id=?",(target_id ,)).fetchone ()
            if not target :
                await q .message .reply_text ("ইউজার পাওয়া যায়নি।");return
            new_status =1 if action =="ban"else 0
            c .execute ("UPDATE users SET banned=?,ban_reason=? WHERE id=?",(new_status ,"Admin action"if new_status else "",target_id ))
            c .execute ("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(ADMIN ,"user_ban"if new_status else "user_unban",str (target_id ),now ()))
        try :
            if new_status :
                await ctx .bot .send_message (target_id ,"⛔ আপনার অ্যাকাউন্ট বন্ধ করা হয়েছে। সহায়তার জন্য 🆘 সাহায্য বাটন ব্যবহার করুন।",reply_markup =support_only_kb ())
            else :
                await ctx .bot .send_message (target_id ,"✅ আপনার অ্যাকাউন্ট চালু করা হয়েছে। /start লিখে আবার শুরু করুন।")
        except Exception :pass
        await show_user_detail (q .message ,target_id )
    elif d .startswith ("adminlist:"):
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        kind =d .split (":",1 )[1 ]
        table ={"deposit":"deposits","withdraw":"withdrawals","submission":"submissions"}.get (kind )
        if not table :return
        with conn ()as c :
            rows =c .execute (f"SELECT * FROM {table } WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall ()
        if not rows :
            await q .message .reply_text ("এখন কোনো অপেক্ষমাণ অনুরোধ নেই।");return
        for row in rows :
            if kind =="deposit":
                msg =f"ডিপোজিট #{row ['id']}\nUser: {row ['user_id']}\nAmount: ৳{row ['amount']}\nMethod: {row ['method']}\nTxn: {row ['txn']}"
            elif kind =="withdraw":
                msg =f"উইথড্র #{row ['id']}\nUser: {row ['user_id']}\nGross: ৳{row ['gross']}\nFee: ৳{row ['fee']}\nNet: ৳{row ['net']}\nMethod: {row ['method']}\nAccount: {row ['account']}"
            else :
                msg =f"সাবমিশন #{row ['id']}\nJob: #{row ['job_id']}\nWorker: {row ['worker_id']}\nProof: {row ['proof']}"
            markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("✅ Approve",callback_data =f"review:{kind }:yes:{row ['id']}"),InlineKeyboardButton ("❌ Reject",callback_data =f"review:{kind }:no:{row ['id']}")]])
            await q .message .reply_text (msg ,reply_markup =markup )
            if kind =="submission"and row .get ("file_id"):
                try :await q .message .reply_photo (row ["file_id"],caption =f"Submission #{row ['id']} Screenshot")
                except Exception :log .exception ("admin submission photo display failed")
    elif d =="adminjob:start":
        if uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        ctx .user_data ["admin_free_job"]=True
        ctx .user_data ["form"]={}
        ctx .user_data ["stage"]="job_category"
        rows =[]
        for i in range (0 ,len (CATS ),2 ):
            row =[InlineKeyboardButton (f"{CATS [i ][0 ]} (৳{CATS [i ][1 ]}+)",callback_data =f"cat:{i }")]
            if i +1 <len (CATS ):row .append (InlineKeyboardButton (f"{CATS [i +1 ][0 ]} (৳{CATS [i +1 ][1 ]}+)",callback_data =f"cat:{i +1 }"))
            rows .append (row )
        await q .message .reply_text ("🛠 অ্যাডমিন ফ্রি জব পোস্ট\nপ্রথমে কাজের ক্যাটাগরি বেছে নিন। অ্যাডমিন পোস্টে কোনো বাজেট/পোস্ট ফি কাটা হবে না।",reply_markup =InlineKeyboardMarkup (rows ))
    elif d .startswith ("proof_type:"):
        if ctx .user_data .get ("stage")!="job_proof_type"or not ctx .user_data .get ("form"):await q .message .reply_text ("ফর্মটি আর সক্রিয় নেই। আবার কাজ পোস্ট করুন।");return
        pt =d .split (":",1 )[1 ]
        if pt not in ("photo","text","both"):return
        f =ctx .user_data ["form"];f ["proof_type"]=pt ;total =Decimal (f ["price"])*int (f ["count"])
        if not ctx .user_data .get ("admin_free_job"):
            with conn ()as c :r =c .execute ("SELECT buyer FROM users WHERE id=?",(uid ,)).fetchone ()
            bal =Decimal (r ["buyer"]or 0 )
            fee =(total *Decimal (get_setting ("job_fee","0.05"))).quantize (Decimal ("0.01"),rounding =ROUND_HALF_UP )
            if bal <total +fee :await q .message .reply_text (f"ব্যালেন্স কম। বাজেট ৳{m (total )} + পোস্ট ফি ৳{m (fee )} = মোট ৳{m (total +fee )} দরকার।");ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None );return
        ctx .user_data ["stage"]="job_confirm"
        label ={"photo":"Screenshot only","text":"Text only","both":"Screenshot + Text"}[pt ]
        await q .message .reply_text (f"ক্যাটাগরি: {CATS [f ['cat']][0 ]}\nকাজের ধরন: {f .get ('task_type','—')}\nশিরোনাম: {f ['title']}\nবিবরণ: {f ['desc']}\nকাজ: {f ['count']}\nপ্রতি কাজ: ৳{m (f ['price'])}\nমোট বাজেট: ৳{m (total )}\nProof Type: {label }\nনিয়ম: {f ['rules']}\nপোস্ট করবেন?",reply_markup =InlineKeyboardMarkup ([[InlineKeyboardButton ("✅ পোস্ট",callback_data ="job:yes"),InlineKeyboardButton ("❌ বাতিল",callback_data ="job:no")]]))
    elif d .startswith ("cat:"):
        try :ci =int (d .split (":")[1 ]);name ,mi =CATS [ci ]
        except (ValueError ,IndexError ):return
        if ctx .user_data .get ("admin_free_job")and uid !=ADMIN :
            await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        ctx .user_data .setdefault ("form",{})["cat"]=ci
        rows =[[InlineKeyboardButton (label ,callback_data =f"tasktype:{i }")]for i ,label in enumerate (TASK_TYPES )]
        await q .message .reply_text (f"প্ল্যাটফর্ম: {name }\nকাজের ধরন বেছে নিন। শুধু Like, Follow, Comment অথবা Repost করা যাবে।",reply_markup =InlineKeyboardMarkup (rows ))
    elif d .startswith ("tasktype:"):
        if not ctx .user_data .get ("form")or "cat"not in ctx .user_data ["form"]:
            await q .message .reply_text ("কাজ পোস্টের ফর্মটি শেষ হয়েছে। আবার শুরু করুন।");return
        try :ti =int (d .split (":")[1 ]);task =TASK_TYPES [ti ]
        except (ValueError ,IndexError ):return
        ctx .user_data ["form"]["task_type"]=task
        ctx .user_data ["stage"]="job_title"
        ci =ctx .user_data ["form"]["cat"]
        await q .message .reply_text (f"{CATS [ci ][0 ]} — {task }\n"+get_bot_text ("job_title_prompt"))
    elif d .startswith ("wdmethod:"):
        if ctx .user_data .get ("stage")!="wd_method"or not ctx .user_data .get ("form"):
            await q .message .reply_text ("এই উইথড্র ফর্মটি আর সক্রিয় নেই। আবার উইথড্র শুরু করুন।");return
        method =d .split (":",1 )[1 ]
        if method not in ("bKash","Nagad","Recharge"):return
        g =Decimal (ctx .user_data ["form"]["gross"])
        if method in ("bKash","Nagad")and g <100 :
            await q .message .reply_text ("বিকাশ/নগদে সর্বনিম্ন উইথড্র ৳১০০। আবার ৳১০০ বা তার বেশি দিয়ে শুরু করুন।");ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None );return
        if method =="Recharge"and g <50 :
            await q .message .reply_text ("মোবাইল রিচার্জ সর্বনিম্ন ৳৫০।");ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None );return
        ctx .user_data ["form"]["method"]=method ;ctx .user_data ["stage"]="wd_account"
        label ={"bKash":"বিকাশ","Nagad":"নগদ","Recharge":"মোবাইল রিচার্জ"}[method ]
        await q .message .reply_text (f"পেমেন্ট পদ্ধতি: {label }\nএখন আপনার মোবাইল/অ্যাকাউন্ট নম্বর লিখুন।")
    elif d .startswith ("depmethod:"):
        if ctx .user_data .get ("stage")!="dep_method"or not ctx .user_data .get ("form"):
            await q .message .reply_text ("এই ডিপোজিট ফর্মটি আর সক্রিয় নেই। আবার ডিপোজিট শুরু করুন।");return
        method =d .split (":",1 )[1 ]
        if method not in ("bKash","Nagad"):return
        receiver =get_setting ("bkash_number"if method =="bKash"else "nagad_number","")
        if not receiver :
            await q .message .reply_text ("এই পেমেন্ট পদ্ধতির গ্রহণের নম্বর এখনো অ্যাডমিন সেট করেননি। পরে আবার চেষ্টা করুন বা অ্যাডমিনকে জানান।")
            ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None )
            return
        ctx .user_data ["form"]["method"]=method ;ctx .user_data ["stage"]="dep_txn"
        await q .message .reply_text (f"পেমেন্ট পদ্ধতি: {'বিকাশ'if method =='bKash'else 'নগদ'}\nপেমেন্ট গ্রহণের নম্বর: {receiver }\nএই নম্বরে টাকা পাঠিয়ে Transaction ID লিখুন।")
    elif d =="job:no":
        ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None );await q .message .reply_text (get_bot_text ("job_post_cancelled"))
    elif d =="job:yes":
        if ctx .user_data .get ("stage")!="job_confirm"or not ctx .user_data .get ("form"):await q .message .reply_text ("ফর্মটি আর সক্রিয় নেই।");return
        f =ctx .user_data ["form"];total =Decimal (f ["price"])*int (f ["count"]);is_admin_free =bool (ctx .user_data .get ("admin_free_job")and uid ==ADMIN )
        post_fee =Decimal ("0")if is_admin_free else (total *Decimal (get_setting ("job_fee","0.05"))).quantize (Decimal ("0.01"),rounding =ROUND_HALF_UP )
        charge =Decimal ("0")if is_admin_free else total +post_fee
        with conn ()as c :
            c .execute ("BEGIN IMMEDIATE")
            if not is_admin_free :
                r =c .execute ("SELECT buyer FROM users WHERE id=?",(uid ,)).fetchone ();bal =Decimal (r ["buyer"]or 0 )
                if bal <charge :c .rollback ();await q .message .reply_text (f"ব্যালেন্স কম। বাজেট ৳{m (total )} + ৫% পোস্ট ফি ৳{m (post_fee )} = মোট ৳{m (charge )} দরকার। আগে ডিপোজিট করুন।");return
                c .execute ("UPDATE users SET buyer=? WHERE id=?",(str (bal -charge ),uid ))
            cur =c .execute ("INSERT INTO jobs(buyer_id,cat,title,description,count,price,reserved,rules,proof_type,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uid ,f ["cat"],f"{f .get ('task_type','')} — {f ['title']}",f ["desc"],f ["count"],f ["price"],str (total ),f ["rules"],f .get ("proof_type","text"),"open",now ()));jid =cur .lastrowid
            if not is_admin_free :
                c .execute ("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(uid ,"buyer",str (-total ),"escrow",f"Job #{jid } budget",now ()))
                if post_fee :
                    c .execute ("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(uid ,"buyer",str (-post_fee ),"job_fee",f"Job #{jid } 5% posting fee",now ()))
            c .execute ("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(uid ,"job_posted_admin_free"if is_admin_free else "job_posted_buyer",f"Job #{jid }; budget {m (total )}; fee {m (post_fee )}",now ()))
        ctx .user_data .pop ("stage",None );ctx .user_data .pop ("form",None );ctx .user_data .pop ("admin_free_job",None )
        if is_admin_free :
            await q .message .reply_text (f"✅ অ্যাডমিন হিসেবে কাজ #{jid } বিনা খরচে পোস্ট হয়েছে। কোনো ডিপোজিট বা পোস্ট ফি কাটা হয়নি।")
        else :
            await q .message .reply_text ("✅ "+get_bot_text ("job_posted",jid =jid ,budget =m (total ),fee =m (post_fee ),charge =m (charge )))
        await admin_msg (ctx ,f"New job #{jid }: {f ['title']} | poster {uid } | budget ৳{m (total )} | admin_free={is_admin_free }")
    elif d .startswith ("submit:"):
        jid =int (d .split (":")[1 ])
        with conn ()as c :
            j =c .execute ("SELECT * FROM jobs WHERE id=?",(jid ,)).fetchone ();ex =c .execute ("SELECT 1 FROM submissions WHERE job_id=? AND worker_id=?",(jid ,uid )).fetchone ()
        if not j or j ["status"]!="open"or j ["count"]<1 :await q .message .reply_text (get_bot_text ("job_closed"))
        elif j ["buyer_id"]==uid :await q .message .reply_text ("নিজের কাজ করা যাবে না।")
        elif ex :await q .message .reply_text ("এই কাজের প্রুফ আগে জমা দিয়েছেন।")
        else :
            ctx .user_data ["proof_job"]=jid ;ctx .user_data ["proof_type"]=j ["proof_type"]or "text"
            if ctx .user_data ["proof_type"]=="photo":
                ctx .user_data ["stage"]="proof_photo";prompt =get_bot_text ("proof_photo_prompt")
            elif ctx .user_data ["proof_type"]=="both":
                ctx .user_data ["stage"]="proof_both_photo";prompt =get_bot_text ("proof_both_prompt")
            else :
                ctx .user_data ["stage"]="proof_text";prompt =get_bot_text ("proof_text_prompt")
            await q .message .reply_text (prompt +"\nএকটি কাজের জন্য একবারই সাবমিশন করা যাবে।")
    elif d .startswith ("review:"):
        if uid !=ADMIN :await q .message .reply_text ("শুধু অ্যাডমিন এই কাজটি করতে পারবেন।");return
        _ ,kind ,decision ,raw =d .split (":");item =int (raw );yes =decision =="yes"
        if kind =="submission"and not yes :
            with conn ()as c :
                pending =c .execute ("SELECT id FROM submissions WHERE id=? AND status='pending'",(item ,)).fetchone ()
            if not pending :
                await q .message .reply_text ("এই Submission আগেই Review করা হয়েছে।");return
            ctx .user_data ["pending_submission_rejection"]=item
            await q .message .reply_text (f"Submission #{item } Reject করার কারণ লিখুন (৩–১০০০ অক্ষর)। বাতিল করতে /cancel লিখুন।")
            return
        await review (q ,ctx ,kind ,item ,yes )
async def review (q ,ctx ,kind ,item ,yes ,rejection_reason =""):
    target =0 ;usermsg ="";buyer_target =None ;buyer_msg =""
    decision ="yes"if yes else "no"
    with conn ()as c :
        c .execute ("BEGIN IMMEDIATE")
        if kind =="deposit":
            r =c .execute ("SELECT * FROM deposits WHERE id=?",(item ,)).fetchone ()
            if not r or r ["status"]!="pending":await q .message .reply_text ("ইতিমধ্যে প্রসেস করা হয়েছে।");return
            c .execute ("UPDATE deposits SET status=?,reviewed=? WHERE id=? AND status='pending'",("approved"if yes else "rejected",now (),item ))
            if yes :
                u =c .execute ("SELECT buyer FROM users WHERE id=?",(r ["user_id"],)).fetchone ();b =Decimal (u ["buyer"]or 0 )+Decimal (r ["amount"]);c .execute ("UPDATE users SET buyer=? WHERE id=?",(str (b ),r ["user_id"]))
                c .execute ("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(r ["user_id"],"buyer",r ["amount"],"deposit",f"Deposit #{item }",now ()))
            target =r ["user_id"];usermsg =f"ডিপোজিট #{item } {'অনুমোদিত'if yes else 'বাতিল'} হয়েছে।"
        elif kind =="withdraw":
            r =c .execute ("SELECT * FROM withdrawals WHERE id=?",(item ,)).fetchone ()
            if not r or r ["status"]!="pending":await q .message .reply_text ("ইতিমধ্যে প্রসেস করা হয়েছে।");return
            c .execute ("UPDATE withdrawals SET status=?,reviewed=? WHERE id=? AND status='pending'",("approved"if yes else "rejected",now (),item ))
            if not yes :
                u =c .execute ("SELECT worker FROM users WHERE id=?",(r ["user_id"],)).fetchone ();b =Decimal (u ["worker"]or 0 )+Decimal (r ["gross"]);c .execute ("UPDATE users SET worker=? WHERE id=?",(str (b ),r ["user_id"]))
            else :
                u =c .execute ("SELECT referrer,ref_paid FROM users WHERE id=?",(r ["user_id"],)).fetchone ()
                if u and u ["referrer"]and not u ["ref_paid"]:
                    bonus =(Decimal (r ["gross"])*REFERRAL ).quantize (Decimal ("0.01"),rounding =ROUND_HALF_UP )
                    ref =c .execute ("SELECT worker FROM users WHERE id=?",(u ["referrer"],)).fetchone ()
                    if ref :
                        c .execute ("UPDATE users SET worker=? WHERE id=?",(str (Decimal (ref ["worker"]or 0 )+bonus ),u ["referrer"]))
                        c .execute ("UPDATE users SET ref_paid=1 WHERE id=?",(r ["user_id"],))
                        c .execute ("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(u ["referrer"],"worker",str (bonus ),"referral",f"Referral user {r ['user_id']}",now ()))
            target =r ["user_id"];usermsg =get_setting ("withdraw_approved_text"if yes else "withdraw_rejected_text","উইথড্র অনুমোদিত হয়েছে।"if yes else "উইথড্র বাতিল হয়েছে; টাকা ফেরত দেওয়া হয়েছে।")
        elif kind =="submission":
            r =c .execute ("SELECT * FROM submissions WHERE id=?",(item ,)).fetchone ()
            if not r or r ["status"]!="pending":await q .message .reply_text ("ইতিমধ্যে প্রসেস করা হয়েছে।");return
            j =c .execute ("SELECT * FROM jobs WHERE id=?",(r ["job_id"],)).fetchone ()
            if not j :await q .message .reply_text ("কাজ নেই।");return
            buyer_target =j ["buyer_id"]
            if yes and j ["count"]>0 and Decimal (j ["reserved"])>=Decimal (j ["price"]):
                pay =Decimal (j ["price"]);u =c .execute ("SELECT worker FROM users WHERE id=?",(r ["worker_id"],)).fetchone ()
                c .execute ("UPDATE users SET worker=? WHERE id=?",(str (Decimal (u ["worker"]or 0 )+pay ),r ["worker_id"]))
                left =Decimal (j ["reserved"])-pay ;count =j ["count"]-1
                c .execute ("UPDATE jobs SET reserved=?,count=?,status=? WHERE id=?",(str (max (left ,Decimal (0 ))),max (count ,0 ),"completed"if count <=0 or left <=0 else "open",j ["id"]))
                c .execute ("INSERT INTO ledger(user_id,wallet,amount,kind,note,created) VALUES(?,?,?,?,?,?)",(r ["worker_id"],"worker",str (pay ),"earning",f"Job #{j ['id']}",now ()))
                status ="approved";usermsg =f"সাবমিশন #{item } অনুমোদিত। ৳{m (pay )} ব্যালেন্সে যোগ হয়েছে।"
            elif yes :
                status ="rejected";usermsg =f"সাবমিশন #{item } বাতিল: কাজের বাজেট শেষ।"
            else :
                status ="rejected"
                usermsg =f"❌ সাবমিশন #{item } বাতিল হয়েছে।\nReject করার কারণ: {rejection_reason or 'কারণ উল্লেখ করা হয়নি'}"
            c .execute ("UPDATE submissions SET status=?,reviewed=?,rejection_reason=? WHERE id=? AND status='pending'",(status ,now (),rejection_reason if not yes else "",item ));target =r ["worker_id"]
            if status =="approved":
                buyer_msg =f"কাজ #{j ['id']} — {j ['title']}\nSubmission #{item } অনুমোদিত হয়েছে। Worker ID: {r ['worker_id']}"
            else :
                reason_text =rejection_reason if rejection_reason else "কাজের বাজেট শেষ।"
                buyer_msg =(f"কাজ #{j ['id']} — {j ['title']}\nSubmission #{item } বাতিল হয়েছে।\n" f"Worker ID: {r ['worker_id']}\nকারণ: {reason_text }")
        else :await q .message .reply_text ("অজানা রিভিউ।");return
        c .execute ("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(q .from_user .id ,f"{kind }_{decision }",str (item ),now ()))
    await q .message .reply_text (f"{kind } #{item } সম্পন্ন।")
    try :await ctx .bot .send_message (target ,usermsg )
    except Exception :log .exception ("worker review notification failed")
    if kind =="submission"and buyer_target :
        try :await ctx .bot .send_message (buyer_target ,buyer_msg )
        except Exception :log .exception ("buyer review notification failed")
async def review_from_reason (update ,ctx ,sid ,reason ):
    class _Query :
        def __init__ (self ,message ,user ):
            self .message =message
            self .from_user =user
    await review (_Query (update .effective_message ,update .effective_user ),ctx ,"submission",sid ,False ,reason )
EDITABLE_TEXTS ={"welcome":("স্বাগতম বার্তা","🌟 BABLU STAR BD-তে আপনাকে স্বাগতম! 🌟\n\n💼 আপনার কাজ, আপনার উপার্জন!\n🛒 Buyer Mode — নিজের কাজ পোস্ট করুন\n🧑‍💻 Freelancer Mode — কাজ করুন ও আয় করুন\n\n👇 শুরু করতে নিচের একটি মোড বেছে নিন।"),"main_menu":("মেইন মেনুর সাধারণ বার্তা","মেনু থেকে অপশন বেছে নিন."),"buyer_mode_on":("Buyer Mode চালু বার্তা","Buyer Mode চালু হয়েছে।"),"worker_mode_on":("Freelancer Mode চালু বার্তা","Freelancer Mode চালু হয়েছে।"),"mode_changed":("মোড পরিবর্তন বার্তা","মোড পরিবর্তন হয়েছে।"),"cancelled":("কাজ বাতিল বার্তা","চলমান কাজ বাতিল হয়েছে।"),"choose_category":("কাজের ক্যাটাগরি নির্বাচন","ক্যাটাগরি বেছে নিন। প্রতিটি ক্যাটাগরির মিনিমাম আলাদা; সর্বোচ্চ সীমা নেই।"),"no_jobs":("কাজ না থাকলে বার্তা","এখন কোনো খোলা কাজ নেই।"),"job_title_prompt":("কাজের শিরোনাম নির্দেশনা","কাজের শিরোনাম লিখুন।"),"job_desc_prompt":("কাজের বিস্তারিত নির্দেশনা","কাজের বিস্তারিত লিখুন (১০–২০০০ অক্ষর)।"),"job_count_prompt":("কতজন কাজ করবে নির্দেশনা","কতজন কাজ করবে? পূর্ণসংখ্যা দিন।"),"job_rules_prompt":("কাজের নিয়ম/প্রুফ নির্দেশনা","কাজের নিয়ম এবং কী প্রুফ দিতে হবে লিখুন।"),"proof_type_prompt":("প্রুফের ধরন নির্বাচন নির্দেশনা","এই কাজের জন্য Freelancer কী প্রুফ জমা দেবে? নিচের একটি ধরন বেছে নিন।"),"proof_photo_prompt":("শুধু Screenshot প্রুফ নির্দেশনা","📸 শুধু Screenshot পাঠান।"),"proof_both_prompt":("Screenshot ও Text প্রুফ নির্দেশনা","📸 প্রথমে Screenshot পাঠান; এরপর Text/Link লিখবেন।"),"proof_text_prompt":("শুধু Text প্রুফ নির্দেশনা","📝 এখন Text বা Link লিখুন।"),"proof_photo_received":("Screenshot পাওয়ার পর বার্তা","✅ Screenshot পেয়েছি। এখন প্রমাণের Text বা Link লিখুন।"),"proof_submitted":("প্রুফ জমা হওয়ার বার্তা","প্রুফ জমা হয়েছে। সাবমিশন #{sid}; অ্যাডমিন যাচাই করবেন।"),"payment_info":("পেমেন্ট তথ্যের সম্পূর্ণ লেখা","💳 BABLU STAR BD — পেমেন্ট তথ্য\n\n➕ ডিপোজিট: বায়ার মোডে ডিপোজিট চাপুন, পরিমাণ লিখুন, বিকাশ/নগদ বেছে নিয়ে Transaction ID জমা দিন। অ্যাডমিন যাচাই করে ব্যালেন্স যোগ করবেন।\n\n💸 উইথড্র: ফ্রিল্যান্সার মোড → উইথড্র → পরিমাণ লিখুন → বিকাশ/নগদ/মোবাইল রিচার্জ বেছে নিন → নম্বর দিন।\n\nবিকাশ/নগদে সর্বনিম্ন উইথড্র ৳১০০, মোবাইল রিচার্জ ৳৫০। প্রতিটি উইথড্র/রিচার্জে ১৫% ফি। উদাহরণ: ৳১০০ থেকে ফি ৳১৫, পাবেন ৳৮৫।\n\nপেমেন্ট রিসিভারের নম্বর অ্যাডমিনের কাছ থেকে নিশ্চিত করুন; এখানে কোনো নম্বর অনুমান করে দেখানো হচ্ছে না।"),"rules":("নিয়মাবলির সম্পূর্ণ লেখা","• কাজের দাম ক্যাটাগরি মিনিমামের কম নয় (কোনোটিই ৳২-এর নিচে নয়)।\n• ভুয়া অ্যাকাউন্ট, প্রতারণা, স্প্যাম বা প্ল্যাটফর্মের নিয়মভঙ্গকারী কাজ নিষিদ্ধ।\n• বাজেট আগে বায়ার ব্যালেন্স থেকে সংরক্ষিত হবে।\n• প্রুফ অ্যাডমিন অনুমোদন করলে ফ্রিল্যান্সার টাকা পাবেন।\n• ডিপোজিট/উইথড্র ম্যানুয়ালি যাচাই হয়।"),"support":("সাহায্য / Support Center লেখা","📩 BABLU STAR BD — Support Center\n\nআপনার কোনো সমস্যা বা প্রশ্ন থাকলে আমাদের ইমেইল করুন।\n\n✉️ Support Email:\n{email}\n\n🆔 আপনার Telegram ID: {uid}\n\nসমস্যার বিস্তারিত লিখে ইমেইল পাঠান।\nধন্যবাদ — BABLU STAR BD"),"no_menu_button":("ভুল মেনু অপশন বার্তা","মেনুর বাটন ব্যবহার করুন।"),"deposit_amount_prompt":("ডিপোজিটের পরিমাণ নির্দেশনা","ডিপোজিটের পরিমাণ লিখুন (সর্বনিম্ন ৳৫০)।"),"deposit_method_prompt":("ডিপোজিট পদ্ধতি নির্দেশনা","ডিপোজিটের পদ্ধতি বেছে নিন:"),"deposit_txn_prompt":("Transaction ID নির্দেশনা","পেমেন্টের Transaction ID লিখুন।"),"deposit_submitted":("ডিপোজিট জমা হওয়ার বার্তা","✅ আপনার ডিপোজিট অনুরোধ জমা হয়েছে। অ্যাডমিন Transaction ID যাচাই করে ব্যালেন্স যোগ করবেন।"),"withdraw_method_prompt":("উইথড্র পদ্ধতি নির্দেশনা","নিচের বাটন থেকে পেমেন্ট পদ্ধতি বেছে নিন।"),"withdraw_account_prompt":("উইথড্র নম্বর নির্দেশনা","এখন আপনার মোবাইল/অ্যাকাউন্ট নম্বর লিখুন।"),"proof_already_submitted":("আগে প্রুফ জমা দিলে বার্তা","এই কাজের প্রুফ আগে জমা দিয়েছেন।"),"job_closed":("কাজ বন্ধ থাকলে বার্তা","কাজটি আর খোলা নেই।"),"job_posted":("কাজ পোস্ট সফল বার্তা","কাজ #{jid} পোস্ট হয়েছে। বাজেট ৳{budget}; ৫% পোস্ট ফি ৳{fee}; মোট কাটা হয়েছে ৳{charge}।"),"job_post_cancelled":("কাজ পোস্ট বাতিল বার্তা","কাজ পোস্ট বাতিল।"),"withdraw_request_sent":("Withdrawal Request জমা হওয়ার বার্তা","withdraw_request_sent_text"),"withdraw_instructions":("Withdrawal নির্দেশনা","withdraw_instructions_text"),"withdraw_pending":("Withdrawal Pending বার্তা","withdraw_pending_text"),"withdraw_approved":("Withdrawal Approved বার্তা","withdraw_approved_text"),"withdraw_rejected":("Withdrawal Rejected বার্তা","withdraw_rejected_text"),}
def get_bot_text (key ,**values ):
    label ,default =EDITABLE_TEXTS [key ]
    if key in {"withdraw_request_sent","withdraw_instructions","withdraw_pending","withdraw_approved","withdraw_rejected"}:
        value =get_setting (default ,"")
    else :
        value =get_setting ("bot_text_"+key ,default )
    try :
        return value .format (**values )
    except (KeyError ,ValueError ):
        return value
async def show_editable_texts (message ):
    rows =[]
    for key ,(label ,_ )in EDITABLE_TEXTS .items ():
        rows .append ([InlineKeyboardButton ("✏️ "+label ,callback_data ="admintext:edit:"+key )])
    rows .append ([InlineKeyboardButton ("🔙 Admin Panel",callback_data ="adminrefresh")])
    await message .reply_text ("📝 **সব লেখা এডিট**\nযে লেখাটি পরিবর্তন করতে চান সেটি বেছে নিন।",reply_markup =InlineKeyboardMarkup (rows ),parse_mode ="Markdown")
def get_setting (key ,default =""):
    with conn ()as c :
        r =c .execute ("SELECT value FROM settings WHERE key=?",(key ,)).fetchone ()
    return r ["value"]if r else default
def set_setting (key ,value ,actor ):
    with conn ()as c :
        c .execute ("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key ,value ))
        c .execute ("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(actor ,"setting_update",key ,now ()))
async def ban_cmd (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    if update .effective_user .id !=ADMIN :await update .effective_message .reply_text ("শুধু অ্যাডমিনের জন্য।");return
    if len (ctx .args )!=1 or not ctx .args [0 ].isdigit ():await update .effective_message .reply_text ("ব্যবহার: /ban USER_ID");return
    target =int (ctx .args [0 ])
    with conn ()as c :
        if not c .execute ("SELECT 1 FROM users WHERE id=?",(target ,)).fetchone ():await update .effective_message .reply_text ("ইউজার পাওয়া যায়নি।");return
        c .execute ("UPDATE users SET banned=1 WHERE id=?",(target ,))
        c .execute ("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(ADMIN ,"user_ban",str (target ),now ()))
    await update .effective_message .reply_text (f"⛔ User {target } Ban করা হয়েছে।")
    try :await ctx .bot .send_message (target ,"⛔ আপনার অ্যাকাউন্ট বন্ধ করা হয়েছে। সহায়তার জন্য 🆘 সাহায্য বাটন ব্যবহার করুন।",reply_markup =support_only_kb ())
    except Exception :pass
async def unban_cmd (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    if update .effective_user .id !=ADMIN :await update .effective_message .reply_text ("শুধু অ্যাডমিনের জন্য।");return
    if len (ctx .args )!=1 or not ctx .args [0 ].isdigit ():await update .effective_message .reply_text ("ব্যবহার: /unban USER_ID");return
    target =int (ctx .args [0 ])
    with conn ()as c :
        cur =c .execute ("UPDATE users SET banned=0 WHERE id=?",(target ,))
        if not cur .rowcount :await update .effective_message .reply_text ("ইউজার পাওয়া যায়নি।");return
        c .execute ("INSERT INTO audit(actor,action,details,created) VALUES(?,?,?,?)",(ADMIN ,"user_unban",str (target ),now ()))
    await update .effective_message .reply_text (f"✅ User {target } Unban করা হয়েছে।")
    try :await ctx .bot .send_message (target ,"✅ আপনার অ্যাকাউন্ট চালু করা হয়েছে। /start লিখে আবার শুরু করুন.")
    except Exception :pass
async def admin (update ,ctx ):
    if update .effective_user .id !=ADMIN :
        await update .effective_message .reply_text ("শুধু অ্যাডমিনের জন্য।")
        return
    with conn ()as c :
        d =c .execute ("SELECT COUNT(*) n FROM deposits WHERE status='pending'").fetchone ()["n"]
        w =c .execute ("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone ()["n"]
        sub =c .execute ("SELECT COUNT(*) n FROM submissions WHERE status='pending'").fetchone ()["n"]
        users =c .execute ("SELECT COUNT(*) n FROM users").fetchone ()["n"]
        jobs =c .execute ("SELECT COUNT(*) n FROM jobs WHERE status='open'").fetchone ()["n"]
    kb =InlineKeyboardMarkup ([[InlineKeyboardButton ("➕ ফ্রিতে কাজ পোস্ট",callback_data ="adminjob:start")],[InlineKeyboardButton ("💳 বিকাশ নম্বর সেট",callback_data ="adminset:bkash"),InlineKeyboardButton ("💳 নগদ নম্বর সেট",callback_data ="adminset:nagad")],[InlineKeyboardButton ("📧 সাপোর্ট ইমেইল",callback_data ="adminset:support_email")],[InlineKeyboardButton ("📝 সব লেখা এডিট",callback_data ="admintext:list")],[InlineKeyboardButton ("✏️ Withdrawal Request Sent",callback_data ="adminset:withdraw_request_sent")],[InlineKeyboardButton ("✏️ Withdrawal Instructions",callback_data ="adminset:withdraw_instructions"),InlineKeyboardButton ("✏️ Pending",callback_data ="adminset:withdraw_pending")],[InlineKeyboardButton ("✏️ Approved",callback_data ="adminset:withdraw_approved"),InlineKeyboardButton ("✏️ Rejected",callback_data ="adminset:withdraw_rejected")],[InlineKeyboardButton ("📥 ডিপোজিট যাচাই",callback_data ="adminlist:deposit"),InlineKeyboardButton ("📤 উত্তোলন যাচাই",callback_data ="adminlist:withdraw")],[InlineKeyboardButton ("🧾 কাজের প্রুফ যাচাই",callback_data ="adminlist:submission"),InlineKeyboardButton ("👥 User List + Balance/Income",callback_data ="adminlist:users")],[InlineKeyboardButton ("🔄 রিফ্রেশ",callback_data ="adminrefresh")]])
    await update .effective_message .reply_text ("🛠 BABLU STAR BD — অ্যাডমিন প্যানেল\n\n" f"👥 মোট ব্যবহারকারী: {users }\n📋 চালু কাজ: {jobs }\n" f"📥 অপেক্ষমাণ ডিপোজিট: {d }\n📤 অপেক্ষমাণ উত্তোলন: {w }\n🧾 অপেক্ষমাণ কাজের প্রুফ: {sub }\n\n" f"বিকাশ: {get_setting ('bkash_number','সেট করা হয়নি')}\n" f"নগদ: {get_setting ('nagad_number','সেট করা হয়নি')}\n" f"Support: {get_setting ('support_email','mrshahidulislam8989@gmail.com')}\n\n" "নম্বর/ইমেইল/Withdrawal মেসেজ পরিবর্তন করতে নিচের বাটনে চাপ দিন।",reply_markup =kb )
async def cancel_cmd (update :Update ,ctx :ContextTypes .DEFAULT_TYPE ):
    if is_banned (update .effective_user .id ):await banned_notice (update .effective_message );return
    await menu (update ,ctx ,"চলমান কাজ বাতিল হয়েছে।")
async def err (update ,ctx ):
    log .exception ("Bot error",exc_info =ctx .error )
    if update and update .effective_message :
        try :await update .effective_message .reply_text ("সমস্যা হয়েছে। আবার চেষ্টা করুন।")
        except :pass
def main ():
    if not TOKEN :raise SystemExit ("Hostless Secrets-এ BOT_TOKEN সেট করুন।")
    if not ADMIN :raise SystemExit ("Hostless Secrets-এ ADMIN_ID (numeric Telegram ID) সেট করুন।")
    if not DATABASE_URL :raise SystemExit ("Hostless Secrets-এ DATABASE_URL (Neon PostgreSQL) সেট করুন।")
    init ();app =Application .builder ().token (TOKEN ).build ()
    app .add_handler (CommandHandler ("start",start ));app .add_handler (CommandHandler ("admin",admin ));app .add_handler (CommandHandler ("ban",ban_cmd ));app .add_handler (CommandHandler ("unban",unban_cmd ))
    app .add_handler (CommandHandler ("cancel",cancel_cmd ))
    app .add_handler (CallbackQueryHandler (callback ));app .add_handler (MessageHandler (filters .PHOTO ,photo ))
    app .add_handler (MessageHandler (filters .TEXT &~filters .COMMAND ,text ));app .add_error_handler (err )
    app .run_polling (allowed_updates =Update .ALL_TYPES )
if __name__ =="__main__":main ()
