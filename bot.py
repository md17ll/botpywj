import os, re, random, sqlite3, threading
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import requests, telebot
from telebot import types
TOKEN=os.getenv("TELEGRAM_TOKEN"); AI_KEY=os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY"); CHAT_ID=os.getenv("CHANNEL_OR_CHAT_ID"); ADMIN_ID=str(os.getenv("ADMIN_ID","")); DB=os.getenv("DB_PATH","bot_data.db")
MODEL=os.getenv("OPENROUTER_MODEL","openrouter/auto")
bot=telebot.TeleBot(TOKEN) if TOKEN else None; lock=threading.Lock(); states={}; stop_event=threading.Event()
THEMES=["الوجود","الزمن","الوعي","الوحدة","الذاكرة","الحقيقة","الوهم","المصير","الحياة","الروح","النفس","العلاقات","الخوف","النجاح"]
STYLES={"deep":"فلسفي عميق","mystery":"غامض","psych":"نفسي","dark":"سوداوي","poetic":"شاعري","elegant":"تحفيزي راق","smart":"مزيج ذكي"}
def q(sql,p=(),fetch=False):
 with lock:
  c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; x=c.execute(sql,p); r=x.fetchall() if fetch else None; c.commit(); c.close(); return r
def init_db():
 q("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL)"); q("CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,ctype TEXT,theme TEXT,style TEXT,source TEXT,status TEXT,rating INTEGER DEFAULT 0,created TEXT,published TEXT)"); q("CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,cost REAL)")
 for k,v in {"auto_enabled":"0","auto_interval":"21600","next_auto":""}.items():q("INSERT OR IGNORE INTO settings VALUES(?,?)",(k,v))
def gs(k,d=""):
 r=q("SELECT v FROM settings WHERE k=?",(k,),True); return r[0]["v"] if r else d
def ss(k,v):q("INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(k,str(v)))
def clean(t):
 if not t:return ""
 t=re.sub(r'[\u064B-\u065F\u0670\u06D6-\u06ED]','',t); t=re.sub(r'[أإآٱ]','ا',t).replace('ؤ','و').replace('ئ','ي').replace('ء',''); t=t.replace('ـ','').replace('`','').replace('"','').replace("'",""); t=re.sub(r'[*_~#]+','',t); t=re.sub(r'[A-Za-z]+','',t); t=re.sub(r'[^\u0600-\u06FF\s،؛؟.!]','',t); return re.sub(r'\s+',' ',t).strip()
def normalize(t):return re.sub(r'[،؛؟.!\s]','',clean(t))
def is_usable(t):
 if not t or t.startswith("خطا"):return False
 return len(re.findall(r'[\u0621-\u064A]',t))>=10 and len(t.split())>=4
def too_similar(t):
 nt=normalize(t); rows=q("SELECT text FROM posts WHERE status='published' ORDER BY id DESC LIMIT 50",fetch=True)
 return any(SequenceMatcher(None,nt,normalize(r['text'])).ratio()>=0.90 for r in rows)
def ai(prompt,temp=.82):
 if not AI_KEY:return "خطا: مفتاح OpenRouter غير موجود"
 try:
  r=requests.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {AI_KEY}","Content-Type":"application/json","HTTP-Referer":"https://railway.app","X-Title":"Telegram Content Manager"},json={"model":MODEL,"messages":[{"role":"user","content":prompt}],"temperature":temp,"max_tokens":100},timeout=(5,25))
  if r.status_code!=200:return f"خطا AI: OpenRouter HTTP {r.status_code}"
  d=r.json(); choices=d.get("choices") or []; raw=((choices[0].get("message") or {}).get("content") or "") if choices else ""; text=clean(raw)
  if not text:return "خطا AI: OpenRouter رجع محتوى فارغ"
  u=d.get("usage") or {}; q("INSERT INTO usage VALUES(NULL,?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),u.get("prompt_tokens",0),u.get("completion_tokens",0),u.get("total_tokens",0),float(u.get("cost") or 0))); return text
 except Exception as e:return "خطا AI: "+str(e)[:120]
def generate(theme=None,style=None,smart=False):
 chosen=random.sample(THEMES,2) if smart or not theme else [theme]; sty=random.choice(list(STYLES.values())) if smart or not style else STYLES.get(style,style)
 old=q("SELECT text FROM posts WHERE status='published' ORDER BY id DESC LIMIT 8",fetch=True); avoid=" | ".join(clean(x['text'])[:60] for x in old)
 prompt=f"اكتب عبارة عربية فصحى واحدة قصيرة وواضحة وعميقة عن {', '.join(chosen)} باسلوب {sty}. اجعل معناها مفهوما من اول قراءة، من 7 الى 18 كلمة، بلا شرح ولا عنوان ولا هاشتاغ ولا انجليزية ولا تشكيل. تجنب الهمزات. لا تكرر هذه العبارات: {avoid}. اكتب العبارة فقط."
 text=ai(prompt)
 if is_usable(text) and not too_similar(text):return text
 text=ai(prompt,.9)
 if is_usable(text) and not too_similar(text):return text
 return text if text.startswith("خطا") else "خطا AI: لم يتم الحصول على عبارة مناسبة، حاول مرة اخرى"
def publish(text,source="manual"):
 t=clean(text)
 if not is_usable(t):raise ValueError("تم منع النشر: العبارة غير صالحة")
 if too_similar(t):raise ValueError("تم منع النشر: العبارة مكررة")
 bot.send_message(CHAT_ID,t); now=datetime.now().isoformat(timespec="seconds"); q("INSERT INTO posts VALUES(NULL,?,?,?,?,?,?,?,?,?)",(t,"short","","",source,"published",0,now,now)); return t
def kb(rows):
 m=types.InlineKeyboardMarkup()
 for row in rows:m.row(*[types.InlineKeyboardButton(a,callback_data=b) for a,b in row])
 return m
def mainkb():return kb([[("🧠 توليد عبارة","content")],[("⚡ جرب وانشر مباشرة","try_publish")],[("🔄 النشر الدوري الذكي","auto")],[("📊 الاحصائيات","stats"),("🟢 حالة البوت","status")],[("💰 استهلاك AI","usage")]])
def themekb():return kb([[("🎲 مزيج ذكي","mix")],[("🌌 الوجود","th_0"),("😨 الخوف","th_12")],[("🌑 الوحدة","th_3"),("🧠 الوعي","th_2")],[("🏠 الرئيسية","home")]])
def stylekb():return kb([[("🎲 مزيج اساليب","st_smart")],[("🧠 فلسفي عميق","st_deep"),("🌑 غامض","st_mystery")],[("🪞 نفسي","st_psych"),("🖤 سوداوي","st_dark")],[("🌙 شاعري","st_poetic")],[("🏠 الرئيسية","home")]])
def previewkb():return kb([[("📢 نشر الان","pub")],[("🔄 عبارة جديدة","regen")],[("🏠 الرئيسية","home")]])
def autokb():return kb([[("⏱ كل ساعة","int_3600")],[("⏱ كل 3 ساعات","int_10800"),("⏱ كل 6 ساعات","int_21600")],[("⏱ كل 12 ساعة","int_43200"),("⏱ كل 24 ساعة","int_86400")],[("🛑 ايقاف النشر الدوري","autostop")],[("🏠 الرئيسية","home")]])
def interval_name(sec):
 names={3600:"كل ساعة",10800:"كل 3 ساعات",21600:"كل 6 ساعات",43200:"كل 12 ساعة",86400:"كل 24 ساعة"}; return names.get(int(sec),f"كل {int(sec)/3600:g} ساعة")
def auto_text():
 enabled=gs("auto_enabled")=="1"; sec=int(gs("auto_interval","21600")); return "🔄 النشر الدوري دائما مزيج ذكي من كل المواضيع والاساليب\n\nالحالة: "+("🟢 شغال" if enabled else "🔴 مطفي")+"\nالفترة المختارة: ⏱ "+interval_name(sec)
def home_text():
 enabled=gs("auto_enabled")=="1"; sec=int(gs("auto_interval","21600")); return "🤖 لوحة العبارات الذكية\n\n🔄 النشر التلقائي: "+("🟢 شغال" if enabled else "🔴 مطفي")+"\n⏱ "+("النشر: " if enabled else "الفترة المحفوظة: ")+interval_name(sec)
def edit(c,text,markup):
 try:bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=markup)
 except:bot.send_message(c.message.chat.id,text,reply_markup=markup)
def scheduler():
 while not stop_event.wait(20):
  try:
   now=datetime.now(); nxt=gs("next_auto"); sec=int(gs("auto_interval","21600"))
   if gs("auto_enabled")=="1" and nxt and now>=datetime.fromisoformat(nxt):
    t=generate(smart=True)
    if not t.startswith("خطا"):
     try:publish(t,"automatic")
     except Exception as e:print("auto blocked",e)
    ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds"))
  except Exception as e:print("scheduler",e)
@bot.message_handler(commands=["start"])
def start(m):
 if str(m.from_user.id)==ADMIN_ID:bot.send_message(m.chat.id,home_text(),reply_markup=mainkb())
@bot.callback_query_handler(func=lambda c:True)
def cb(c):
 if str(c.from_user.id)!=ADMIN_ID:return
 try:bot.answer_callback_query(c.id)
 except:pass
 st=states.setdefault(c.from_user.id,{}); d=c.data
 if d=="home":edit(c,home_text(),mainkb())
 elif d=="content":edit(c,"🎯 اختر الموضوع",themekb())
 elif d=="mix":st["theme"]=None; edit(c,"🎨 اختر الاسلوب",stylekb())
 elif d.startswith("th_"):st["theme"]=THEMES[int(d[3:])]; edit(c,"🎨 اختر الاسلوب",stylekb())
 elif d.startswith("st_"):
  st["style"]=None if d=="st_smart" else d[3:]; st["text"]=generate(st.get("theme"),st.get("style")); edit(c,("❌ " if st["text"].startswith("خطا") else "👁 معاينة:\n\n")+st["text"],previewkb())
 elif d=="regen":st["text"]=generate(st.get("theme"),st.get("style")); edit(c,("❌ " if st["text"].startswith("خطا") else "👁 معاينة:\n\n")+st["text"],previewkb())
 elif d=="pub":
  try:edit(c,"✅ تم النشر:\n\n"+publish(st["text"]),mainkb())
  except Exception as e:edit(c,"❌ "+str(e),previewkb())
 elif d=="try_publish":
  edit(c,"⏳ جاري انشاء عبارة ذكية ونشرها...",kb([[("🏠 الرئيسية","home")]])); t=generate(smart=True)
  if t.startswith("خطا"):edit(c,"❌ "+t,mainkb())
  else:
   try:publish(t,"instant-smart"); edit(c,"✅ تم التوليد والنشر مباشرة:\n\n"+t,mainkb())
   except Exception as e:edit(c,"❌ "+str(e),mainkb())
 elif d=="auto":edit(c,auto_text(),autokb())
 elif d.startswith("int_"):
  sec=int(d[4:]); ss("auto_interval",sec); ss("auto_enabled","1"); ss("next_auto",(datetime.now()+timedelta(seconds=sec)).isoformat(timespec="seconds")); edit(c,auto_text(),autokb())
 elif d=="autostop":ss("auto_enabled","0"); ss("next_auto",""); edit(c,auto_text(),autokb())
 elif d=="status":edit(c,"🟢 البوت يعمل\nAI: "+("موجود" if AI_KEY else "مفقود")+"\nالموديل: "+MODEL,mainkb())
 elif d=="usage":
  r=q("SELECT COUNT(*) n,COALESCE(SUM(total_tokens),0)t,COALESCE(SUM(cost),0)c FROM usage",fetch=True)[0]; edit(c,f"💰 الطلبات: {r['n']}\nالتوكنات: {r['t']}\nالتكلفة: ${r['c']:.4f}",mainkb())
 elif d=="stats":
  r=q("SELECT COUNT(*) n FROM posts WHERE status='published'",fetch=True)[0]; edit(c,f"📊 المنشور: {r['n']}",mainkb())
def startup_checks():
 if not all([TOKEN,AI_KEY,CHAT_ID,ADMIN_ID]):raise RuntimeError("Missing environment variables")
 print("Telegram OK @"+bot.get_me().username,"Model:",MODEL)
if __name__=="__main__":
 init_db(); startup_checks(); threading.Thread(target=scheduler,daemon=True).start(); bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
