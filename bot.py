import os, re, random, sqlite3, threading
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import requests, telebot
from telebot import types

TOKEN=os.getenv("TELEGRAM_TOKEN")
AI_KEY=os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY")
CHAT_ID=os.getenv("CHANNEL_OR_CHAT_ID")
ADMIN_ID=str(os.getenv("ADMIN_ID", ""))
DB=os.getenv("DB_PATH", "bot_data.db")
MODEL=os.getenv("OPENROUTER_MODEL", "openrouter/auto")
bot=telebot.TeleBot(TOKEN) if TOKEN else None
lock=threading.Lock(); states={}; stop_event=threading.Event()
THEMES=["الوجود","الزمن","الوعي","الوحدة","الذاكرة","الحقيقة","الوهم","المصير","الحياة","الروح","النفس","العلاقات","الخوف","النجاح"]
STYLES={"deep":"فلسفي عميق","mystery":"غامض","psych":"نفسي","dark":"سوداوي","poetic":"شاعري","elegant":"تحفيزي راق","smart":"مزيج ذكي"}

def q(sql,p=(),fetch=False):
    with lock:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; x=c.execute(sql,p); r=x.fetchall() if fetch else None; c.commit(); c.close(); return r

def init_db():
    q("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL)")
    q("CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,ctype TEXT,theme TEXT,style TEXT,source TEXT,status TEXT,rating INTEGER DEFAULT 0,created TEXT,published TEXT)")
    q("CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,ctype TEXT,publish_at TEXT,status TEXT DEFAULT 'pending',created TEXT)")
    q("CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY AUTOINCREMENT,hhmm TEXT NOT NULL,ctype TEXT NOT NULL,enabled INTEGER DEFAULT 1,last_date TEXT DEFAULT '')")
    q("CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,cost REAL)")
    for k,v in {"auto_enabled":"0","auto_interval":"21600","next_auto":"","plan_enabled":"0"}.items():q("INSERT OR IGNORE INTO settings VALUES(?,?)",(k,v))

def gs(k,d=""):
    r=q("SELECT v FROM settings WHERE k=?",(k,),True); return r[0]["v"] if r else d
def ss(k,v):q("INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(k,str(v)))
def clean(t):
    if not t:return ""
    t=re.sub(r'[أإآٱ]','ا',t).replace('ؤ','و').replace('ئ','ي').replace('ء',''); t=re.sub(r'[\u064B-\u065F\u0670\u06D6-\u06ED]','',t)
    t=t.replace('ـ','').replace('`','').replace('"','').replace("'",""); t=re.sub(r'[*_~#]+','',t); t=re.sub(r'[ \t]+',' ',t); t=re.sub(r'\s*\n\s*',' ',t); return t.strip()
def ai(prompt,temp=.9):
    if not AI_KEY:return "خطا: مفتاح OpenRouter غير موجود"
    try:
        r=requests.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {AI_KEY}","Content-Type":"application/json","HTTP-Referer":"https://railway.app","X-Title":"Telegram Content Manager"},json={"model":MODEL,"messages":[{"role":"user","content":prompt}],"temperature":temp,"max_tokens":90},timeout=(10,75))
        if r.status_code!=200:return f"خطا OpenRouter {r.status_code}: {r.text[:180]}"
        d=r.json(); u=d.get("usage") or {}
        if d.get("error"):return "خطا OpenRouter: "+str(d["error"].get("message","unknown"))
        q("INSERT INTO usage VALUES(NULL,?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),u.get("prompt_tokens",0),u.get("completion_tokens",0),u.get("total_tokens",0),float(u.get("cost") or 0)))
        return clean(d["choices"][0]["message"]["content"])
    except Exception as e:return "خطا اتصال OpenRouter: "+str(e)[:140]
def generate(theme=None,style=None):
    chosen=random.sample(THEMES,random.randint(2,4)) if not theme else [theme]+random.sample([x for x in THEMES if x!=theme],2); sty=STYLES.get(style,random.choice(list(STYLES.values()))) if style else random.choice(list(STYLES.values()))
    old=q("SELECT text FROM posts WHERE status='published' ORDER BY id DESC LIMIT 12",fetch=True); avoid="\n".join("- "+x["text"][:120] for x in old)
    return ai(f"""اكتب عبارة عربية فصحى واحدة قصيرة جدا وعميقة لها معنى واضح وقوي.
امزج بشكل طبيعي بين هذه الافكار: {', '.join(chosen)}. الاسلوب: {sty}.
جملة واحدة فقط من 8 الى 22 كلمة تقريبا. ممنوع المقالات والفقرات والشرح والمقدمات والهاشتاغات.
لا تستخدم التشكيل ولا عنوانا. اكتب الناتج فقط.
مثال على الطول والروح فقط: الوحدة ليست غياب الناس، بل اللحظة التي تسمع فيها نفسك بوضوح.
ابتكر معنى جديدا ولا تكرر:\n{avoid}""")
def similar(text):
    best=(0,None)
    for r in q("SELECT id,text FROM posts WHERE status='published' ORDER BY id DESC LIMIT 80",fetch=True):
        s=SequenceMatcher(None,clean(text),r["text"]).ratio()
        if s>best[0]:best=(s,r["id"])
    return best
def publish(text,source="manual"):
    t=clean(text)
    if not t or t.startswith("خطا"):raise ValueError(t or "النص فارغ")
    bot.send_message(CHAT_ID,t); now=datetime.now().isoformat(timespec="seconds"); q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(t,"short","","",source,"published",0,now,now)); return t
def kb(rows):
    m=types.InlineKeyboardMarkup()
    for row in rows:m.row(*[types.InlineKeyboardButton(a,callback_data=b) for a,b in row])
    return m
def back(to="home"):return [("↩️ رجوع","go_"+to),("🏠 الرئيسية","home")]
def mainkb():return kb([[("🧠 توليد عبارة","content")],[("🔄 النشر الدوري","auto"),("📅 خطة النشر","plan")],[("⏳ المجدولة","scheduled"),("🗃 الارشيف","archive"),("📊 الاحصائيات","stats")],[("💰 استهلاك AI","usage"),("🟢 حالة البوت","status"),("⚙️ الاعدادات","settings")]])
def themekb():return kb([[("🎲 مزيج ذكي","mix")],[("🌌 الوجود","th_0"),("😨 الخوف","th_12")],[("🌑 الوحدة","th_3"),("🧠 الوعي","th_2"),("🪞 النفس","th_10")],[("⏳ الزمن","th_1"),("❤️ العلاقات","th_11")],back()])
def stylekb():return kb([[("🎲 مزيج اساليب","st_smart")],[("🧠 فلسفي","st_deep"),("🌑 غامض","st_mystery")],[("🪞 نفسي","st_psych"),("🖤 سوداوي","st_dark"),("🌙 شاعري","st_poetic")],back("content")])
def previewkb():return kb([[("📢 نشر الان","pub")],[("⏳ جدولة","schedule"),("🔄 عبارة جديدة","regen")],[("🧠 تحسين","improve"),("✏️ تعديل","edit"),("⭐ حفظ","save")],[("👍 ممتاز","up"),("👎 لم يعجبني","down")],back()])
def schkb():return kb([[("بعد ساعة","sc_3600")],[("بعد 3 ساعات","sc_10800"),("بعد 6 ساعات","sc_21600")],[("بعد 12 ساعة","sc_43200"),("بعد 24 ساعة","sc_86400")],[("🎯 مخصص","sccustom")],back()])
def autokb():return kb([[("كل ساعة","int_3600")],[("كل 3 ساعات","int_10800"),("كل 6 ساعات","int_21600")],[("كل 12 ساعة","int_43200"),("كل 24 ساعة","int_86400"),("كل 48 ساعة","int_172800")],[("🎯 ساعات مخصصة","customint"),("🛑 ايقاف","autostop")],back()])
def edit_screen(c,text,markup):
    try:bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=markup)
    except Exception:bot.send_message(c.message.chat.id,text,reply_markup=markup)
def S(uid):return states.setdefault(uid,{})
def schedule(text,seconds):
    at=(datetime.now()+timedelta(seconds=seconds)).isoformat(timespec="seconds"); q("INSERT INTO schedules VALUES(NULL,?,?,?,?,?)",(clean(text),"short",at,"pending",datetime.now().isoformat(timespec="seconds"))); return at
def scheduler():
    while not stop_event.wait(20):
        try:
            now=datetime.now(); iso=now.isoformat(timespec="seconds")
            for r in q("SELECT * FROM schedules WHERE status='pending' AND publish_at<=? ORDER BY publish_at LIMIT 10",(iso,),True):
                try:publish(r["text"],"scheduled"); q("UPDATE schedules SET status='published' WHERE id=?",(r["id"],))
                except Exception as e:print("schedule:",e)
            if gs("auto_enabled")=="1":
                nxt=gs("next_auto"); sec=int(gs("auto_interval","21600"))
                if not nxt:ss("next_auto",(now+timedelta(seconds=sec)).isoformat(timespec="seconds"))
                elif now>=datetime.fromisoformat(nxt):
                    t=generate()
                    if not t.startswith("خطا"):publish(t,"automatic")
                    ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds"))
            if gs("plan_enabled")=="1":
                for r in q("SELECT * FROM slots WHERE enabled=1",fetch=True):
                    if now.strftime("%H:%M")>=r["hhmm"] and r["last_date"]!=now.date().isoformat():
                        t=generate()
                        if not t.startswith("خطا"):publish(t,"plan"); q("UPDATE slots SET last_date=? WHERE id=?",(now.date().isoformat(),r["id"]))
        except Exception as e:print("scheduler:",e)
@bot.message_handler(commands=["start"])
def start(m):
    if str(m.from_user.id)==ADMIN_ID:bot.send_message(m.chat.id,"🤖 لوحة العبارات الذكية",reply_markup=mainkb())
@bot.message_handler(func=lambda m:str(m.from_user.id)==ADMIN_ID)
def text_input(m):
    st=S(m.from_user.id); w=st.get("wait")
    try:
        if w=="customint":
            h=float(m.text.replace(",",".")); assert h>0; sec=int(h*3600); ss("auto_interval",sec); ss("auto_enabled","1"); ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds")); st.pop("wait",None); bot.send_message(m.chat.id,f"✅ نشر تلقائي كل {h:g} ساعة",reply_markup=mainkb())
        elif w=="sccustom":
            h=float(m.text.replace(",",".")); assert h>0; at=schedule(st["text"],int(h*3600)); st.pop("wait",None); bot.send_message(m.chat.id,"✅ مجدول: "+at.replace("T"," "),reply_markup=mainkb())
        elif w=="edit":st["text"]=clean(m.text); st.pop("wait",None); bot.send_message(m.chat.id,"👁 معاينة:\n\n"+st["text"],reply_markup=previewkb())
        elif w=="slot":hh=m.text.strip(); datetime.strptime(hh,"%H:%M"); q("INSERT INTO slots(hhmm,ctype) VALUES(?,?)",(hh,"short")); st.pop("wait",None); bot.send_message(m.chat.id,"✅ اضيف الوقت "+hh,reply_markup=mainkb())
    except Exception:bot.send_message(m.chat.id,"❌ القيمة غير صحيحة")
@bot.callback_query_handler(func=lambda c:True)
def cb(c):
    if str(c.from_user.id)!=ADMIN_ID:return
    try:bot.answer_callback_query(c.id)
    except Exception:pass
    st=S(c.from_user.id); d=c.data
    if d in ("home","go_home"):edit_screen(c,"🤖 لوحة العبارات الذكية",mainkb())
    elif d=="content":edit_screen(c,"🎯 اختر مزيج الموضوع",themekb())
    elif d=="mix":st["theme"]=None; edit_screen(c,"🎨 اختر الاسلوب",stylekb())
    elif d.startswith("th_"):st["theme"]=THEMES[int(d[3:])]; edit_screen(c,"🎨 اختر الاسلوب",stylekb())
    elif d.startswith("st_"):st["style"]=None if d=="st_smart" else d[3:]; edit_screen(c,"⏳ جاري التوليد...",kb([back()])); st["text"]=generate(st.get("theme"),st.get("style")); edit_screen(c,"👁 معاينة نظيفة:\n\n"+st["text"],previewkb())
    elif d=="regen":st["text"]=generate(st.get("theme"),st.get("style")); edit_screen(c,"👁 عبارة جديدة:\n\n"+st["text"],previewkb())
    elif d=="pub":
        sim,pid=similar(st.get("text",""))
        if sim>=.78:edit_screen(c,f"⚠️ تشابه {sim:.0%} مع #{pid}",kb([[("🔄 غيرها","regen"),("✅ انشرها","forcepub")],back()]))
        else:edit_screen(c,"✅ تم النشر:\n\n"+publish(st["text"]),mainkb())
    elif d=="forcepub":edit_screen(c,"✅ تم النشر:\n\n"+publish(st["text"]),mainkb())
    elif d=="schedule":edit_screen(c,"⏳ اختر الموعد",schkb())
    elif d.startswith("sc_"):at=schedule(st["text"],int(d[3:])); edit_screen(c,"✅ مجدول: "+at.replace("T"," "),mainkb())
    elif d=="sccustom":st["wait"]="sccustom"; edit_screen(c,"ارسل عدد الساعات",kb([back()]))
    elif d=="edit":st["wait"]="edit"; edit_screen(c,"✏️ ارسل العبارة المعدلة",kb([back()]))
    elif d=="improve":st["text"]=ai("حسن هذه العبارة مع الحفاظ على معناها. اجعلها جملة واحدة قصيرة جدا من 8 الى 22 كلمة، عميقة ومرتبة. لا توسعها ولا تشرحها. اكتب الناتج فقط:\n"+st["text"]); edit_screen(c,"👁 المحسن:\n\n"+st["text"],previewkb())
    elif d=="save":q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(clean(st["text"]),"short",st.get("theme") or "mix",st.get("style") or "mix","manual","saved",0,datetime.now().isoformat(timespec="seconds"),None)); edit_screen(c,"⭐ تم الحفظ",previewkb())
    elif d in ("up","down"):q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(clean(st["text"]),"short",st.get("theme") or "mix",st.get("style") or "mix","rating","rated",1 if d=="up" else -1,datetime.now().isoformat(timespec="seconds"),None)); edit_screen(c,"⭐ تم تسجيل التقييم",previewkb())
    elif d=="auto":edit_screen(c,"🔄 اختر فترة النشر التلقائي",autokb())
    elif d.startswith("int_"):
        sec=int(d[4:]); ss("auto_interval",sec); ss("auto_enabled","1"); ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds")); edit_screen(c,f"✅ يعمل كل {sec/3600:g} ساعة",mainkb())
    elif d=="customint":st["wait"]="customint"; edit_screen(c,"ارسل عدد الساعات",kb([back()]))
    elif d=="autostop":ss("auto_enabled","0"); ss("next_auto",""); edit_screen(c,"🛑 تم الايقاف",mainkb())
    elif d=="scheduled":
        rs=q("SELECT * FROM schedules WHERE status='pending' ORDER BY publish_at LIMIT 12",fetch=True); rows=[[(f"#{r['id']} {r['publish_at'][5:16].replace('T',' ')}",f"sched_{r['id']}")] for r in rs]+[back()]; edit_screen(c,"⏳ المجدولة" if rs else "لا توجد مجدولات",kb(rows))
    elif d.startswith("sched_"):r=q("SELECT * FROM schedules WHERE id=?",(int(d[6:]),),True)[0]; st["sid"]=r["id"]; edit_screen(c,r["text"],kb([[("📢 نشر الان","schedpub"),("❌ الغاء","schedcancel")],back()]))
    elif d=="schedpub":r=q("SELECT * FROM schedules WHERE id=?",(st["sid"],),True)[0]; publish(r["text"],"scheduled-now"); q("UPDATE schedules SET status='published' WHERE id=?",(st["sid"],)); edit_screen(c,"✅ تم النشر",mainkb())
    elif d=="schedcancel":q("UPDATE schedules SET status='cancelled' WHERE id=?",(st["sid"],)); edit_screen(c,"❌ الغيت الجدولة",mainkb())
    elif d=="archive":rs=q("SELECT * FROM posts ORDER BY id DESC LIMIT 10",fetch=True); edit_screen(c,"🗃 الارشيف\n\n"+"\n\n".join(f"#{r['id']} {r['status']}\n{r['text']}" for r in rs),kb([back()]))
    elif d=="stats":a=q("SELECT COUNT(*) n FROM posts",fetch=True)[0]["n"]; p=q("SELECT COUNT(*) n FROM posts WHERE status='published'",fetch=True)[0]["n"]; edit_screen(c,f"📊 المسجل: {a}\nالمنشور: {p}",kb([back()]))
    elif d=="usage":r=q("SELECT COUNT(*) n,COALESCE(SUM(total_tokens),0)t,COALESCE(SUM(cost),0)c FROM usage",fetch=True)[0]; edit_screen(c,f"💰 الطلبات: {r['n']}\nالتوكنات: {r['t']}\nالتكلفة: ${r['c']:.4f}",kb([back()]))
    elif d=="status":edit_screen(c,f"🟢 البوت يعمل\nالنشر الدوري: {'يعمل' if gs('auto_enabled')=='1' else 'متوقف'}\nAI: {'موجود' if AI_KEY else 'مفقود'}",kb([back()]))
    elif d=="settings":edit_screen(c,"⚙️ وضع العبارات القصيرة مفعل اجباريا\n🧹 بدون تشكيل وهمزات\n📝 جملة واحدة قصيرة فقط",kb([back()]))
    elif d=="plan":
        rs=q("SELECT * FROM slots ORDER BY hhmm",fetch=True); edit_screen(c,"📅 خطة النشر\n"+("\n".join(r['hhmm'] for r in rs) if rs else "لا توجد اوقات"),kb([[("➕ اضافة وقت","slotadd")],[("▶️ تشغيل","planon"),("⏸ ايقاف","planoff")],back()]))
    elif d=="slotadd":st["wait"]="slot"; edit_screen(c,"ارسل الوقت مثل 21:30",kb([back()]))
    elif d=="planon":ss("plan_enabled","1"); edit_screen(c,"▶️ تم تشغيل الخطة",mainkb())
    elif d=="planoff":ss("plan_enabled","0"); edit_screen(c,"⏸ تم ايقاف الخطة",mainkb())
def startup_checks():
    missing=[x for x,v in [("TELEGRAM_TOKEN",TOKEN),("CHANNEL_OR_CHAT_ID",CHAT_ID),("ADMIN_ID",ADMIN_ID),("OPENROUTER_API_KEY/GEMINI_API_KEY",AI_KEY)] if not v]
    if missing:raise RuntimeError("Missing environment variables: "+", ".join(missing))
    me=bot.get_me(); print("Telegram OK @"+me.username)
    try:r=requests.get("https://openrouter.ai/api/v1/models",headers={"Authorization":f"Bearer {AI_KEY}"},timeout=15); print("OpenRouter HTTP",r.status_code)
    except Exception as e:print("OpenRouter check failed:",e)
if __name__=="__main__":
    init_db(); startup_checks(); threading.Thread(target=scheduler,daemon=True).start(); print("Short Content Manager running - single polling instance"); bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
