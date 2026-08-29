import os, re, json, time, random, sqlite3, threading
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import requests, telebot
from telebot import types

TOKEN=os.getenv("TELEGRAM_TOKEN")
AI_KEY=os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY")
CHAT_ID=os.getenv("CHANNEL_OR_CHAT_ID")
ADMIN_ID=str(os.getenv("ADMIN_ID",""))
DB=os.getenv("DB_PATH","bot_data.db")
MODEL=os.getenv("OPENROUTER_MODEL","openrouter/auto")
bot=telebot.TeleBot(TOKEN) if TOKEN else None
lock=threading.Lock(); states={}; stop_event=threading.Event()

THEMES=["الوجود","الزمن","الوعي","الوحدة","الذاكرة","الحقيقة","الوهم","المصير","الحياة","الروح","النفس","العلاقات","الخوف","النجاح"]
STYLES={"deep":"فلسفي عميق","mystery":"غامض","psych":"نفسي","dark":"سوداوي","poetic":"شاعري","elegant":"تحفيزي راق","smart":"مزيج ذكي"}
TYPES={"quote":"مقولة","thought":"خاطرة","short":"مقال قصير","article":"مقال طويل"}

def q(sql,p=(),fetch=False):
    with lock:
        c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
        x=c.execute(sql,p); r=x.fetchall() if fetch else None; c.commit(); c.close(); return r

def init_db():
    q("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL)")
    q("""CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,ctype TEXT,theme TEXT,style TEXT,source TEXT,status TEXT,rating INTEGER DEFAULT 0,created TEXT,published TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,ctype TEXT,publish_at TEXT,status TEXT DEFAULT 'pending',created TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY AUTOINCREMENT,hhmm TEXT NOT NULL,ctype TEXT NOT NULL,enabled INTEGER DEFAULT 1,last_date TEXT DEFAULT '')""")
    q("""CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,cost REAL)""")
    for k,v in {"auto_enabled":"0","auto_interval":"21600","auto_type":"mix","next_auto":"","plan_enabled":"0"}.items():
        q("INSERT OR IGNORE INTO settings VALUES(?,?)",(k,v))

def gs(k,d=""):
    r=q("SELECT v FROM settings WHERE k=?",(k,),True); return r[0]["v"] if r else d
def ss(k,v): q("INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(k,str(v)))

def clean(t):
    if not t:return ""
    t=re.sub(r'[أإآٱ]','ا',t); t=re.sub(r'[\u064B-\u065F\u0670\u06D6-\u06ED]','',t)
    t=t.replace('ـ','').replace('`','').replace('"','').replace("'","")
    t=re.sub(r'[*_~]+','',t); t=re.sub(r'[ \t]+',' ',t); t=re.sub(r'\n[ \t]+','\n',t); t=re.sub(r'\n{3,}','\n\n',t)
    return t.strip()

def ai(prompt,temp=.85):
    if not AI_KEY:return "خطا: مفتاح OpenRouter غير موجود"
    try:
        r=requests.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {AI_KEY}","Content-Type":"application/json","HTTP-Referer":"https://railway.app","X-Title":"Telegram Content Manager"},json={"model":MODEL,"messages":[{"role":"user","content":prompt}],"temperature":temp},timeout=(10,75))
        if r.status_code!=200:return f"خطا OpenRouter {r.status_code}: {r.text[:180]}"
        d=r.json()
        if d.get("error"):return "خطا OpenRouter: "+str(d["error"].get("message","unknown"))
        u=d.get("usage") or {}; q("INSERT INTO usage VALUES(NULL,?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),u.get("prompt_tokens",0),u.get("completion_tokens",0),u.get("total_tokens",0),float(u.get("cost") or 0)))
        return clean(d["choices"][0]["message"]["content"])
    except Exception as e:return "خطا اتصال OpenRouter: "+str(e)[:140]

def similar(text):
    rows=q("SELECT id,text FROM posts WHERE status='published' ORDER BY id DESC LIMIT 80",fetch=True)
    n=clean(text); best=(0,None)
    for r in rows:
        s=SequenceMatcher(None,n,r["text"]).ratio()
        if s>best[0]:best=(s,r["id"])
    return best

def generate(ctype="quote",theme=None,style="smart"):
    theme=theme or random.choice(THEMES); style=STYLES.get(style,STYLES["smart"])
    lengths={"quote":"سطر او سطران","thought":"فقرة واحدة","short":"3 فقرات قصيرة","article":"4 الى 5 فقرات"}
    old=q("SELECT text FROM posts WHERE status='published' ORDER BY id DESC LIMIT 10",fetch=True)
    avoid="\n".join("- "+x["text"][:130] for x in old)
    return ai(f"""اكتب {TYPES.get(ctype,'مقولة')} عربية فصحى حول {theme}. الاسلوب {style}. الطول {lengths.get(ctype,'سطران')}.
اكتب النص فقط بلا مقدمات ولا هاشتاغات. لا تستخدم التشكيل. تجنب الهمزات. ابتكر فكرة غير مكررة.
تجنب هذه النصوص السابقة:
{avoid}""")

def publish(text,source="manual",ctype="quote"):
    t=clean(text)
    if not t or t.startswith("خطا"):raise ValueError(t or "النص فارغ")
    bot.send_message(CHAT_ID,t)
    now=datetime.now().isoformat(timespec="seconds")
    q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(t,ctype,"","",source,"published",0,now,now)); return t

def kb(rows):
    m=types.InlineKeyboardMarkup()
    for row in rows:m.row(*[types.InlineKeyboardButton(a,callback_data=b) for a,b in row])
    return m
def back(to="home"):return [("↩️ رجوع","go_"+to),("🏠 الرئيسية","home")]
def mainkb():return kb([[("🧠 توليد محتوى","content")],[("🔄 النشر الدوري","auto"),("📅 خطة النشر","plan")],[("⏳ المجدولة","scheduled"),("🗃 الارشيف","archive"),("📊 الاحصائيات","stats")],[("🧵 سلسلة","series"),("🗓 مدير المحتوى","manager"),("💰 استهلاك AI","usage")],[("🟢 حالة البوت","status"),("⚙️ الاعدادات","settings")]])
def contentkb():return kb([[("💭 مقولة","ct_quote")],[("✍️ خاطرة","ct_thought"),("📄 مقال قصير","ct_short")],[("📚 مقال طويل","ct_article"),("🎲 اختيار ذكي","ct_random")],back()])
def themekb():return kb([[("🌌 الوجود","th_0")],[("⏳ الزمن","th_1"),("🧠 الوعي","th_2")],[("🌑 الوحدة","th_3"),("🪞 النفس","th_10"),("❤️ العلاقات","th_11")],[("😨 الخوف","th_12"),("🔥 النجاح","th_13")],[("🎲 عشوائي","th_rand")],back("content")])
def stylekb():return kb([[("🧠 فلسفي","st_deep")],[("🌑 غامض","st_mystery"),("🪞 نفسي","st_psych")],[("🖤 سوداوي","st_dark"),("🌙 شاعري","st_poetic"),("🔥 تحفيزي","st_elegant")],[("🎲 مزيج ذكي","st_smart")],back("content")])
def previewkb():return kb([[("📢 نشر الان","pub")],[("⏳ جدولة","schedule"),("🔄 اعادة توليد","regen")],[("🧠 تحسين","improve"),("✏️ تعديل","edit"),("⭐ حفظ","save")],[("📑 3 خيارات","variants_3"),("📚 5 خيارات","variants_5")],[("👍 ممتاز","up"),("👎 لم يعجبني","down")],back("content")])
def improvekb():return kb([[("🧠 اعمق","im_deeper")],[("🌑 اكثر غموضا","im_mystery"),("✂️ اختصر","im_short")],[("📝 وسع","im_long"),("🌙 شاعري","im_poetic"),("🪞 نفسي","im_psych")],[("🔥 اقوى","im_strong"),("♻️ صياغة جديدة","im_rewrite")],back("content")])
def autokb():return kb([[("🎯 نوع المحتوى","atype")],[("⏱ فترات جاهزة","intervals"),("🎯 ساعات مخصصة","customint")],[("📊 الحالة","status"),("🛑 ايقاف","autostop")],back()])
def atypekb():return kb([[("💭 مقولات","at_quote")],[("✍️ خواطر","at_thought"),("📄 قصير","at_short")],[("📚 طويل","at_article"),("🎲 ميكس","at_mix")],back("auto")])
def intkb():return kb([[("كل ساعة","int_3600")],[("كل 3 ساعات","int_10800"),("كل 6 ساعات","int_21600")],[("كل 12 ساعة","int_43200"),("كل 24 ساعة","int_86400"),("كل 48 ساعة","int_172800")],[("🎯 مخصص","customint")],back("auto")])
def schkb():return kb([[("بعد ساعة","sc_3600")],[("بعد 3 ساعات","sc_10800"),("بعد 6 ساعات","sc_21600")],[("بعد 12 ساعة","sc_43200"),("بعد 24 ساعة","sc_86400")],[("🎯 مخصص","sccustom")],back("content")])

def edit(call,text,markup):
    try:bot.edit_message_text(text,call.message.chat.id,call.message.message_id,reply_markup=markup)
    except Exception:bot.send_message(call.message.chat.id,text,reply_markup=markup)
def S(uid):return states.setdefault(uid,{})
def schedule(text,ctype,seconds):
    at=(datetime.now()+timedelta(seconds=seconds)).isoformat(timespec="seconds")
    q("INSERT INTO schedules VALUES(NULL,?,?,?,?,?)",(clean(text),ctype,at,"pending",datetime.now().isoformat(timespec="seconds"))); return at

def make_manager(days):
    out=[]; patterns=["quote","thought","short"]
    for d in range(days):
        for idx,h in enumerate((10,16,21)):
            ct=patterns[idx]; t=generate(ct,random.choice(THEMES),random.choice(list(STYLES)))
            if not t.startswith("خطا"):
                at=(datetime.now()+timedelta(days=d)).replace(hour=h,minute=0,second=0,microsecond=0)
                if at<=datetime.now():at+=timedelta(days=1)
                q("INSERT INTO schedules VALUES(NULL,?,?,?,?,?)",(t,ct,at.isoformat(timespec="seconds"),"pending",datetime.now().isoformat(timespec="seconds"))); out.append(at)
    return len(out)

def scheduler():
    while not stop_event.wait(20):
        try:
            now=datetime.now(); iso=now.isoformat(timespec="seconds")
            for r in q("SELECT * FROM schedules WHERE status='pending' AND publish_at<=? ORDER BY publish_at LIMIT 10",(iso,),True):
                try:publish(r["text"],"scheduled",r["ctype"]);q("UPDATE schedules SET status='published' WHERE id=?",(r["id"],))
                except Exception as e:print("schedule:",e)
            if gs("auto_enabled")=="1":
                nxt=gs("next_auto"); interval=int(gs("auto_interval","21600"))
                if not nxt:ss("next_auto",(now+timedelta(seconds=interval)).isoformat(timespec="seconds"))
                elif now>=datetime.fromisoformat(nxt):
                    ct=gs("auto_type","mix"); ct=random.choice(list(TYPES)) if ct=="mix" else ct; t=generate(ct)
                    if not t.startswith("خطا"):publish(t,"automatic",ct)
                    ss("next_auto",(datetime.now()+timedelta(seconds=interval)).isoformat(timespec="seconds"))
            if gs("plan_enabled")=="1":
                for r in q("SELECT * FROM slots WHERE enabled=1",fetch=True):
                    if now.strftime("%H:%M")>=r["hhmm"] and r["last_date"]!=now.date().isoformat():
                        ct=random.choice(list(TYPES)) if r["ctype"]=="mix" else r["ctype"]; t=generate(ct)
                        if not t.startswith("خطا"):publish(t,"plan",ct);q("UPDATE slots SET last_date=? WHERE id=?",(now.date().isoformat(),r["id"]))
        except Exception as e:print("scheduler:",e)

@bot.message_handler(commands=["start"])
def start(m):
    if str(m.from_user.id)!=ADMIN_ID:return
    bot.send_message(m.chat.id,"🤖 لوحة ادارة المحتوى الذكية",reply_markup=mainkb())

@bot.message_handler(func=lambda m: str(m.from_user.id)==ADMIN_ID)
def input_text(m):
    st=S(m.from_user.id); w=st.get("wait")
    try:
        if w=="customint":
            h=float(m.text.replace(",",".")); assert h>0; sec=int(h*3600);ss("auto_interval",sec);ss("auto_enabled","1");ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds"));st.pop("wait",None);bot.send_message(m.chat.id,f"✅ نشر تلقائي كل {h:g} ساعة",reply_markup=autokb())
        elif w=="sccustom":
            h=float(m.text.replace(",",".")); assert h>0; at=schedule(st["text"],st["ctype"],int(h*3600));st.pop("wait",None);bot.send_message(m.chat.id,"✅ مجدول: "+at.replace("T"," "),reply_markup=mainkb())
        elif w=="edit":st["text"]=clean(m.text);st.pop("wait",None);bot.send_message(m.chat.id,"👁 المعاينة:\n\n"+st["text"],reply_markup=previewkb())
        elif w=="slot":
            parts=m.text.strip().split(); hh=parts[0]; datetime.strptime(hh,"%H:%M"); ct=parts[1] if len(parts)>1 and parts[1] in list(TYPES)+["mix"] else "mix";q("INSERT INTO slots(hhmm,ctype) VALUES(?,?)",(hh,ct));st.pop("wait",None);bot.send_message(m.chat.id,f"✅ اضيف {hh} • {ct}",reply_markup=mainkb())
        elif w=="series":
            n=max(2,min(10,int(m.text))); base=st.get("theme",random.choice(THEMES)); texts=[]
            for i in range(n):texts.append(ai(f"اكتب الجزء {i+1} من سلسلة من {n} اجزاء عن {base}. اجعله مترابطا مع السلسلة. النص فقط بلا تشكيل."))
            st["text"]="\n\n".join(f"الجزء {i+1}\n{x}" for i,x in enumerate(texts));st["ctype"]="series";st.pop("wait",None);bot.send_message(m.chat.id,st["text"],reply_markup=previewkb())
    except Exception:bot.send_message(m.chat.id,"❌ القيمة غير صحيحة، حاول مرة ثانية.")

@bot.callback_query_handler(func=lambda c:True)
def cb(c):
    if str(c.from_user.id)!=ADMIN_ID:return
    try:bot.answer_callback_query(c.id)
    except:pass
    st=S(c.from_user.id); d=c.data
    if d in ("home","go_home"):edit(c,"🤖 لوحة ادارة المحتوى الذكية",mainkb())
    elif d in ("content","go_content"):edit(c,"🧠 اختر نوع المحتوى",contentkb())
    elif d.startswith("ct_"):
        ct=d[3:];st["ctype"]=random.choice(list(TYPES)) if ct=="random" else ct;edit(c,"🎯 اختر الموضوع",themekb())
    elif d.startswith("th_"):
        x=d[3:];st["theme"]=random.choice(THEMES) if x=="rand" else THEMES[int(x)];edit(c,"🎨 اختر الاسلوب",stylekb())
    elif d.startswith("st_"):
        st["style"]=d[3:];edit(c,"⏳ جاري التوليد...",kb([back("content")]));st["text"]=generate(st["ctype"],st["theme"],st["style"]);edit(c,"👁 معاينة نظيفة:\n\n"+st["text"],previewkb())
    elif d=="regen":st["text"]=generate(st.get("ctype","quote"),st.get("theme"),st.get("style","smart"));edit(c,"👁 نسخة جديدة:\n\n"+st["text"],previewkb())
    elif d=="pub":
        sim,pid=similar(st.get("text",""))
        if sim>=.78 and not st.get("force"):st["dup"]=(sim,pid);edit(c,f"⚠️ تشابه {sim:.0%} مع منشور #{pid}. هل تريد النشر؟",kb([[("♻️ اعادة توليد","regen"),("✅ نشر رغم ذلك","forcepub")],back("content")]))
        else:edit(c,"✅ تم النشر:\n\n"+publish(st["text"],"manual",st.get("ctype","quote")),mainkb())
    elif d=="forcepub":st["force"]=1;edit(c,"✅ تم النشر:\n\n"+publish(st["text"],"manual",st.get("ctype","quote")),mainkb());st.pop("force",None)
    elif d=="schedule":edit(c,"⏳ اختر الموعد",schkb())
    elif d.startswith("sc_"):at=schedule(st["text"],st.get("ctype","quote"),int(d[3:]));edit(c,"✅ مجدول: "+at.replace("T"," "),mainkb())
    elif d=="sccustom":st["wait"]="sccustom";edit(c,"ارسل عدد الساعات، مثال 5",kb([back("content")]))
    elif d=="edit":st["wait"]="edit";edit(c,"✏️ ارسل النص المعدل",kb([back("content")]))
    elif d=="improve":edit(c,"🧠 اختر التحسين",improvekb())
    elif d.startswith("im_"):
        mp={"deeper":"اعمق فلسفيا","mystery":"اكثر غموضا","short":"اختصره","long":"وسعه","poetic":"اكثر شاعرية","psych":"اكثر نفسية","strong":"اقوى","rewrite":"اعد صياغته"};st["text"]=ai(mp.get(d[3:],"حسن")+"، حافظ على الفكرة واكتب النص فقط بلا تشكيل:\n"+st["text"]);edit(c,"👁 النص المحسن:\n\n"+st["text"],previewkb())
    elif d=="save":q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(clean(st["text"]),st.get("ctype"),st.get("theme"),st.get("style"),"manual","saved",0,datetime.now().isoformat(timespec="seconds"),None));edit(c,"⭐ تم الحفظ",previewkb())
    elif d in ("up","down"):q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(clean(st["text"]),st.get("ctype"),st.get("theme"),st.get("style"),"rating","rated",1 if d=="up" else -1,datetime.now().isoformat(timespec="seconds"),None));edit(c,"⭐ تم تسجيل التقييم",previewkb())
    elif d.startswith("variants_"):
        n=int(d.split("_")[1]); opts=[generate(st.get("ctype","quote"),st.get("theme"),st.get("style","smart")) for _ in range(n)];st["variants"]=opts;rows=[[(f"الخيار {i+1}",f"pick_{i}")] for i in range(n)]+[back("content")];edit(c,"\n\n".join(f"{i+1}) {x}" for i,x in enumerate(opts)),kb(rows))
    elif d.startswith("pick_"):st["text"]=st["variants"][int(d[5:])];edit(c,"👁 اخترت:\n\n"+st["text"],previewkb())
    elif d in ("auto","go_auto"):edit(c,"🔄 النشر الدوري التلقائي",autokb())
    elif d=="atype":edit(c,"اختر النوع",atypekb())
    elif d.startswith("at_"):ss("auto_type",d[3:]);edit(c,"⏱ اختر الفترة",intkb())
    elif d=="intervals":edit(c,"⏱ اختر الفترة",intkb())
    elif d.startswith("int_"):
        sec=int(d[4:]);ss("auto_interval",sec);ss("auto_enabled","1");ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds"));edit(c,f"✅ يعمل كل {sec/3600:g} ساعة تلقائيا",autokb())
    elif d=="customint":st["wait"]="customint";edit(c,"ارسل عدد الساعات، مثال 5 او 7.5",kb([back("auto")]))
    elif d=="autostop":ss("auto_enabled","0");ss("next_auto","");edit(c,"🛑 تم الايقاف",autokb())
    elif d=="scheduled":
        rs=q("SELECT * FROM schedules WHERE status='pending' ORDER BY publish_at LIMIT 12",fetch=True);rows=[[(f"#{r['id']} {r['publish_at'][5:16].replace('T',' ')}",f"sched_{r['id']}")] for r in rs]+[back()];edit(c,"⏳ المجدولة" if rs else "لا توجد مجدولات",kb(rows))
    elif d.startswith("sched_"):
        i=int(d[6:]);r=q("SELECT * FROM schedules WHERE id=?",(i,),True)[0];st["sid"]=i;edit(c,r["text"]+"\n\n🕐 "+r["publish_at"].replace("T"," "),kb([[("📢 نشر الان","schedpub"),("❌ الغاء","schedcancel")],back("scheduled")]))
    elif d=="schedpub":r=q("SELECT * FROM schedules WHERE id=?",(st["sid"],),True)[0];publish(r["text"],"scheduled-now",r["ctype"]);q("UPDATE schedules SET status='published' WHERE id=?",(st["sid"],));edit(c,"✅ نشر الآن",mainkb())
    elif d=="schedcancel":q("UPDATE schedules SET status='cancelled' WHERE id=?",(st["sid"],));edit(c,"❌ الغيت الجدولة",mainkb())
    elif d=="archive":rs=q("SELECT * FROM posts ORDER BY id DESC LIMIT 10",fetch=True);edit(c,"🗃 الارشيف\n\n"+"\n\n".join(f"#{r['id']} {r['status']}\n{r['text'][:110]}" for r in rs),kb([back()]))
    elif d=="stats":
        a=q("SELECT COUNT(*) n FROM posts",fetch=True)[0]["n"];p=q("SELECT COUNT(*) n FROM posts WHERE status='published'",fetch=True)[0]["n"];au=q("SELECT COUNT(*) n FROM posts WHERE source IN ('automatic','plan')",fetch=True)[0]["n"];edit(c,f"📊 الاحصائيات\n\nالمسجل: {a}\nالمنشور: {p}\nالتلقائي: {au}",kb([back()]))
    elif d=="usage":r=q("SELECT COUNT(*) n,COALESCE(SUM(total_tokens),0)t,COALESCE(SUM(cost),0)c FROM usage WHERE created>=?",((datetime.now()-timedelta(days=30)).isoformat(timespec="seconds"),),True)[0];edit(c,f"💰 آخر 30 يوم\nالطلبات: {r['n']}\nالتوكنات: {r['t']}\nالتكلفة: ${r['c']:.4f}",kb([back()]))
    elif d=="status":edit(c,f"🟢 حالة البوت\n\nالنشر الدوري: {'يعمل' if gs('auto_enabled')=='1' else 'متوقف'}\nالفترة: {int(gs('auto_interval','21600'))/3600:g} ساعة\nالقادم: {gs('next_auto') or 'غير محدد'}\nالخطة اليومية: {'تعمل' if gs('plan_enabled')=='1' else 'متوقفة'}\nAI: {'مفتاح موجود' if AI_KEY else 'المفتاح مفقود'}\nالنموذج: {MODEL}",kb([back()]))
    elif d=="settings":edit(c,"⚙️ التنظيف الاجباري مفعل\n💾 البيانات محفوظة بقاعدة SQLite\n🔐 يقبل OPENROUTER_API_KEY او GEMINI_API_KEY\n🧹 كل نشر يمر بفلتر مركزي",kb([back()]))
    elif d=="plan":
        rs=q("SELECT * FROM slots ORDER BY hhmm",fetch=True);txt="📅 خطة النشر اليومية\n\n"+("\n".join(f"#{r['id']} {r['hhmm']} • {r['ctype']}" for r in rs) if rs else "لا توجد اوقات");edit(c,txt,kb([[("➕ اضافة وقت","slotadd")],[("▶️ تشغيل","planon"),("⏸ ايقاف","planoff")],back()]))
    elif d=="slotadd":st["wait"]="slot";edit(c,"ارسل الوقت والنوع هكذا:\n21:30 quote\nالانواع: quote thought short article mix",kb([back()]))
    elif d=="planon":ss("plan_enabled","1");edit(c,"▶️ تم تشغيل الخطة",mainkb())
    elif d=="planoff":ss("plan_enabled","0");edit(c,"⏸ تم ايقاف الخطة",mainkb())
    elif d=="manager":edit(c,"🗓 جهز محتوى وجدوله تلقائيا",kb([[("يوم واحد","mgr_1")],[("3 ايام","mgr_3"),("اسبوع","mgr_7")],back()]))
    elif d.startswith("mgr_"):n=int(d[4:]);edit(c,"⏳ جاري تجهيز الخطة...",kb([back()]));count=make_manager(n);edit(c,f"✅ تم تجهيز وجدولة {count} منشور",mainkb())
    elif d=="series":st["wait"]="series";st["theme"]=random.choice(THEMES);edit(c,"🧵 ارسل عدد اجزاء السلسلة من 2 الى 10",kb([back()]))

def startup_checks():
    missing=[x for x,v in [("TELEGRAM_TOKEN",TOKEN),("CHANNEL_OR_CHAT_ID",CHAT_ID),("ADMIN_ID",ADMIN_ID),("OPENROUTER_API_KEY/GEMINI_API_KEY",AI_KEY)] if not v]
    if missing:raise RuntimeError("Missing environment variables: "+", ".join(missing))
    me=bot.get_me(); print("Telegram OK @"+me.username)
    try:r=requests.get("https://openrouter.ai/api/v1/models",headers={"Authorization":f"Bearer {AI_KEY}"},timeout=15);print("OpenRouter HTTP",r.status_code)
    except Exception as e:print("OpenRouter check failed:",e)

if __name__=="__main__":
    init_db(); startup_checks(); threading.Thread(target=scheduler,daemon=True).start()
    print("Smart Telegram Content Manager is running - single polling instance")
    bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
