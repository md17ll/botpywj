import os
import re
import json
import time
import random
import sqlite3
import threading
from datetime import datetime, timedelta

import requests
import telebot
from telebot import types

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY")
CHAT_ID = os.getenv("CHANNEL_OR_CHAT_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
db_lock = threading.Lock()
user_state = {}
nav_stack = {}
scheduler_stop = threading.Event()

THEMES = ["الوجود", "الزمن", "الوعي", "الوحدة", "الذاكرة", "الحقيقة", "الوهم", "المصير", "الحياة", "الروح", "النفس", "العلاقات", "الخوف", "النجاح"]
STYLES = {
    "deep": "فلسفي عميق",
    "mystery": "غامض",
    "psych": "نفسي",
    "dark": "سوداوي",
    "poetic": "شاعري",
    "elegant": "تحفيزي راق",
    "smart": "مزيج ذكي",
}
CONTENT_TYPES = {
    "quote": "مقولة قصيرة",
    "thought": "خاطرة",
    "short_article": "مقال قصير",
    "article": "مقال طويل",
}


def db_execute(sql, params=(), fetch=False):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        conn.close()
        return rows


def init_db():
    db_execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db_execute("""CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, content_type TEXT,
        theme TEXT, style TEXT, source TEXT DEFAULT 'manual', status TEXT DEFAULT 'generated',
        rating INTEGER DEFAULT 0, created_at TEXT NOT NULL, published_at TEXT
    )""")
    db_execute("""CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, content_type TEXT,
        publish_at TEXT NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
    )""")
    db_execute("""CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
        prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0
    )""")
    defaults = {
        "auto_enabled": "0", "auto_interval": "21600", "auto_type": "mix",
        "next_auto_at": "", "custom_times": "[]", "clean_text": "1",
    }
    for key, value in defaults.items():
        db_execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))


def get_setting(key, default=""):
    rows = db_execute("SELECT value FROM settings WHERE key=?", (key,), True)
    return rows[0]["value"] if rows else default


def set_setting(key, value):
    db_execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def clean_text(text):
    """فلتر مركزي اجباري لكل نص قبل وصوله للقناة."""
    if not text:
        return ""
    text = re.sub(r'[أإآٱ]', 'ا', text)
    text = re.sub(r'[\u064B-\u065F\u0670\u06D6-\u06ED]', '', text)
    text = text.replace('ـ', '')
    text = text.replace('`', '').replace('"', '').replace("'", '')
    text = re.sub(r'[*_~]+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def safe_publish(text, source="manual", content_type="unknown"):
    final_text = clean_text(text)
    if not final_text:
        raise ValueError("النص فارغ بعد التنظيف")
    bot.send_message(CHAT_ID, final_text)
    now = datetime.now().isoformat(timespec="seconds")
    db_execute("INSERT INTO posts(text,content_type,source,status,created_at,published_at) VALUES(?,?,?,?,?,?)",
               (final_text, content_type, source, "published", now, now))
    return final_text


def recent_texts(limit=40):
    rows = db_execute("SELECT text FROM posts WHERE status='published' ORDER BY id DESC LIMIT ?", (limit,), True)
    return [r["text"] for r in rows]


def ai_request(prompt, temperature=0.9):
    if not OPENROUTER_API_KEY:
        return "حدث خطا: مفتاح OpenRouter غير موجود"
    try:
        payload = {
            "model": "openrouter/auto",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://railway.app",
            "X-Title": "Telegram Smart Content Manager",
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            return f"حدث خطا: {data['error'].get('message', 'OpenRouter')}"
        usage = data.get("usage") or {}
        db_execute("INSERT INTO ai_usage(created_at,prompt_tokens,completion_tokens,total_tokens,cost) VALUES(?,?,?,?,?)", (
            datetime.now().isoformat(timespec="seconds"), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
            usage.get("total_tokens", 0), float(usage.get("cost") or 0)))
        return clean_text(data["choices"][0]["message"]["content"])
    except Exception as exc:
        print("AI error:", exc)
        return "حدث خطا اثناء توليد المحتوى"


def generate_content(content_type="quote", theme=None, style="smart"):
    theme = theme or random.choice(THEMES)
    style_name = STYLES.get(style, STYLES["smart"])
    type_name = CONTENT_TYPES.get(content_type, "مقولة قصيرة")
    length_instruction = {
        "quote": "سطر او سطران فقط",
        "thought": "فقرة واحدة مكثفة",
        "short_article": "ثلاث فقرات قصيرة",
        "article": "اربع الى خمس فقرات مترابطة",
    }.get(content_type, "سطران")
    previous = recent_texts(12)
    avoid = "\n".join(f"- {x[:180]}" for x in previous)
    prompt = f"""اكتب {type_name} عربية فصحى حول {theme}. الاسلوب: {style_name}. الطول: {length_instruction}.
النص عميق واصلي وغير مبتذل ولا يحتوي هاشتاغات او مقدمات. اكتب النص فقط.
مهم جدا: لا تستخدم التشكيل او الحركات، وتجنب الهمزات قدر الامكان.
لا تكرر افكار النصوص السابقة التالية:\n{avoid}"""
    return ai_request(prompt)


def improve_content(text, mode):
    instructions = {
        "deeper": "اجعله اعمق فلسفيا",
        "mysterious": "اجعله اكثر غموضا",
        "shorter": "اختصره مع حفظ المعنى",
        "longer": "وسعه بعمق دون حشو",
        "poetic": "اجعله اكثر شاعرية",
        "psych": "اجعله اكثر نفسية",
        "stronger": "اجعل الصياغة اقوى",
        "rewrite": "اعد صياغته بالكامل مع حفظ الفكرة",
    }
    return ai_request(f"{instructions.get(mode, 'حسن النص')}\nاكتب النسخة الجديدة فقط بدون تشكيل او هاشتاغات:\n{text}", 0.8)


def pyramid(rows):
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(*[types.InlineKeyboardButton(label, callback_data=data) for label, data in row])
    return markup


def main_keyboard():
    return pyramid([
        [("🧠 توليد محتوى جديد", "content_menu")],
        [("🔄 النشر الدوري", "auto_menu"), ("⏳ المجدولة", "scheduled_menu")],
        [("🗃 الارشيف", "archive"), ("📊 الاحصائيات", "stats"), ("⚙️ الاعدادات", "settings")],
        [("🟢 حالة البوت", "status"), ("💰 استهلاك AI", "usage")],
    ])


def back_row(target="home"):
    return [("↩️ رجوع", f"go_{target}"), ("🏠 الرئيسية", "home")]


def content_keyboard():
    return pyramid([
        [("💭 مقولة", "ctype_quote")],
        [("✍️ خاطرة", "ctype_thought"), ("📄 مقال قصير", "ctype_short_article")],
        [("📚 مقال طويل", "ctype_article"), ("🎲 اختيار ذكي", "ctype_random")],
        back_row("home"),
    ])


def theme_keyboard():
    rows = [[(f"🎯 {t}", f"theme_{i}") for i, t in enumerate(THEMES[:2])],
            [(f"🎯 {t}", f"theme_{i}") for i, t in enumerate(THEMES[2:5], 2)],
            [("🎲 عشوائي", "theme_random")], back_row("content_menu")]
    return pyramid(rows)


def style_keyboard():
    items = list(STYLES.items())
    return pyramid([
        [("🧠 فلسفي عميق", "style_deep")],
        [("🌑 غامض", "style_mystery"), ("🪞 نفسي", "style_psych")],
        [("🖤 سوداوي", "style_dark"), ("🌙 شاعري", "style_poetic"), ("🔥 تحفيزي", "style_elegant")],
        [("🎲 مزيج ذكي", "style_smart")], back_row("content_menu")
    ])


def preview_keyboard(content_type):
    return pyramid([
        [("📢 نشر الان", "publish_current")],
        [("⏳ جدولة", "schedule_current"), ("🔄 اعادة توليد", "regen_current")],
        [("🧠 تحسين النص", "improve_menu"), ("✏️ تعديل يدوي", "edit_current"), ("⭐ حفظ", "save_current")],
        [("👍 ممتاز", "rate_up"), ("👎 لم يعجبني", "rate_down")],
        back_row("content_menu"),
    ])


def improve_keyboard():
    return pyramid([
        [("🧠 اعمق", "imp_deeper")],
        [("🌑 اكثر غموضا", "imp_mysterious"), ("✂️ اختصر", "imp_shorter")],
        [("📝 وسع", "imp_longer"), ("🌙 اكثر شاعرية", "imp_poetic"), ("🪞 اكثر نفسية", "imp_psych")],
        [("🔥 اقوى", "imp_stronger"), ("♻️ صياغة مختلفة", "imp_rewrite")],
        back_row("content_menu"),
    ])


def auto_keyboard():
    return pyramid([
        [("🔄 تشغيل / تعديل النشر الدوري", "auto_type")],
        [("⏱ الفترات الجاهزة", "auto_intervals"), ("🎯 ساعات مخصصة", "auto_custom")],
        [("📊 حالة النشر", "status"), ("🛑 ايقاف", "auto_stop")],
        back_row("home"),
    ])


def auto_type_keyboard():
    return pyramid([
        [("💭 مقولات", "atype_quote")],
        [("✍️ خواطر", "atype_thought"), ("📄 مقالات قصيرة", "atype_short_article")],
        [("📚 مقالات طويلة", "atype_article"), ("🎲 ميكس ذكي", "atype_mix")],
        back_row("auto_menu"),
    ])


def interval_keyboard():
    return pyramid([
        [("كل ساعة", "aint_3600")],
        [("كل 3 ساعات", "aint_10800"), ("كل 6 ساعات", "aint_21600")],
        [("كل 12 ساعة", "aint_43200"), ("كل 24 ساعة", "aint_86400"), ("كل 48 ساعة", "aint_172800")],
        [("🎯 عدد ساعات مخصص", "auto_custom")], back_row("auto_menu")
    ])


def schedule_keyboard():
    return pyramid([
        [("بعد ساعة", "sch_3600")],
        [("بعد 3 ساعات", "sch_10800"), ("بعد 6 ساعات", "sch_21600")],
        [("بعد 12 ساعة", "sch_43200"), ("بعد 24 ساعة", "sch_86400")],
        [("🎯 عدد ساعات مخصص", "sch_custom")], back_row("content_menu")
    ])


def edit_screen(call, text, keyboard):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=keyboard)


def state(uid):
    return user_state.setdefault(uid, {})


def schedule_post(text, content_type, seconds):
    publish_at = (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")
    db_execute("INSERT INTO schedules(text,content_type,publish_at,status,created_at) VALUES(?,?,?,?,?)",
               (clean_text(text), content_type, publish_at, "pending", datetime.now().isoformat(timespec="seconds")))
    return publish_at


def set_auto_interval(seconds):
    set_setting("auto_interval", seconds)
    set_setting("auto_enabled", "1")
    set_setting("next_auto_at", (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds"))


def scheduler_loop():
    while not scheduler_stop.is_set():
        try:
            now = datetime.now()
            due = db_execute("SELECT * FROM schedules WHERE status='pending' AND publish_at<=? ORDER BY publish_at LIMIT 10", (now.isoformat(timespec="seconds"),), True)
            for row in due:
                try:
                    safe_publish(row["text"], "scheduled", row["content_type"])
                    db_execute("UPDATE schedules SET status='published' WHERE id=?", (row["id"],))
                except Exception as exc:
                    print("Scheduled publish error:", exc)

            if get_setting("auto_enabled") == "1":
                next_at = get_setting("next_auto_at")
                interval = int(get_setting("auto_interval", "21600"))
                if not next_at:
                    set_setting("next_auto_at", (now + timedelta(seconds=interval)).isoformat(timespec="seconds"))
                elif now >= datetime.fromisoformat(next_at):
                    ctype = get_setting("auto_type", "mix")
                    if ctype == "mix":
                        ctype = random.choice(list(CONTENT_TYPES.keys()))
                    text = generate_content(ctype, random.choice(THEMES), random.choice(list(STYLES.keys())))
                    if "حدث خطا" not in text:
                        safe_publish(text, "automatic", ctype)
                    set_setting("next_auto_at", (datetime.now() + timedelta(seconds=interval)).isoformat(timespec="seconds"))
        except Exception as exc:
            print("Scheduler error:", exc)
        scheduler_stop.wait(20)


@bot.message_handler(commands=["start"])
def start(message):
    if str(message.from_user.id) != str(ADMIN_ID):
        return
    bot.send_message(message.chat.id, "🤖 لوحة ادارة المحتوى الذكية\n\nاختر القسم المطلوب:", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: str(m.from_user.id) == str(ADMIN_ID))
def text_input(message):
    uid = message.from_user.id
    st = state(uid)
    waiting = st.get("waiting")
    if waiting == "auto_custom":
        try:
            hours = float(message.text.replace(',', '.'))
            if hours <= 0:
                raise ValueError
            set_auto_interval(int(hours * 3600))
            st.pop("waiting", None)
            bot.send_message(message.chat.id, f"✅ تم تشغيل النشر التلقائي كل {hours:g} ساعة.", reply_markup=auto_keyboard())
        except ValueError:
            bot.send_message(message.chat.id, "ارسل عددا صحيحا للساعات، مثلا: 5 او 7.5")
    elif waiting == "sch_custom":
        try:
            hours = float(message.text.replace(',', '.'))
            if hours <= 0:
                raise ValueError
            publish_at = schedule_post(st["text"], st.get("content_type", "quote"), int(hours * 3600))
            st.pop("waiting", None)
            bot.send_message(message.chat.id, f"✅ تمت الجدولة الى {publish_at.replace('T', ' ')}", reply_markup=main_keyboard())
        except ValueError:
            bot.send_message(message.chat.id, "ارسل عدد الساعات المطلوب.")
    elif waiting == "edit":
        st["text"] = clean_text(message.text)
        st.pop("waiting", None)
        bot.send_message(message.chat.id, f"👁 المعاينة بعد التنظيف:\n\n{st['text']}", reply_markup=preview_keyboard(st.get("content_type", "quote")))


@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    if str(call.from_user.id) != str(ADMIN_ID):
        return
    uid = call.from_user.id
    st = state(uid)
    data = call.data
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if data in ("home", "go_home"):
        edit_screen(call, "🤖 لوحة ادارة المحتوى الذكية", main_keyboard())
    elif data == "content_menu" or data == "go_content_menu":
        edit_screen(call, "🧠 اختر نوع المحتوى:", content_keyboard())
    elif data.startswith("ctype_"):
        ctype = data[6:]
        if ctype == "random":
            ctype = random.choice(list(CONTENT_TYPES.keys()))
        st["content_type"] = ctype
        edit_screen(call, "🎯 اختر موضوع المحتوى:", theme_keyboard())
    elif data.startswith("theme_"):
        key = data[6:]
        st["theme"] = random.choice(THEMES) if key == "random" else THEMES[int(key)]
        edit_screen(call, f"🎯 الموضوع: {st['theme']}\n\nاختر اسلوب الكتابة:", style_keyboard())
    elif data.startswith("style_"):
        st["style"] = data[6:]
        edit_screen(call, "⏳ جاري توليد المحتوى...", pyramid([back_row("content_menu")]))
        text = generate_content(st.get("content_type", "quote"), st.get("theme"), st.get("style"))
        st["text"] = text
        edit_screen(call, f"👁 معاينة نظيفة:\n\n{text}", preview_keyboard(st.get("content_type", "quote")))
    elif data == "regen_current":
        text = generate_content(st.get("content_type", "quote"), st.get("theme"), st.get("style", "smart"))
        st["text"] = text
        edit_screen(call, f"👁 النسخة الجديدة:\n\n{text}", preview_keyboard(st.get("content_type", "quote")))
    elif data == "publish_current":
        final = safe_publish(st.get("text", call.message.text), "manual", st.get("content_type", "quote"))
        edit_screen(call, f"✅ تم النشر بنجاح.\n\n{final}", main_keyboard())
    elif data == "schedule_current":
        edit_screen(call, "⏳ اختر موعد النشر:", schedule_keyboard())
    elif data.startswith("sch_") and data != "sch_custom":
        seconds = int(data.split("_")[1])
        when = schedule_post(st.get("text", ""), st.get("content_type", "quote"), seconds)
        edit_screen(call, f"✅ تمت جدولة المنشور.\n🕐 {when.replace('T', ' ')}", main_keyboard())
    elif data == "sch_custom":
        st["waiting"] = "sch_custom"
        edit_screen(call, "🎯 ارسل عدد الساعات التي تريد النشر بعدها.\nمثال: 5", pyramid([back_row("content_menu")]))
    elif data == "edit_current":
        st["waiting"] = "edit"
        edit_screen(call, "✏️ ارسل النص المعدل، وساقوم بتنظيفه تلقائيا قبل المعاينة.", pyramid([back_row("content_menu")]))
    elif data == "improve_menu":
        edit_screen(call, "🧠 كيف تريد تحسين النص؟", improve_keyboard())
    elif data.startswith("imp_"):
        improved = improve_content(st.get("text", ""), data[4:])
        st["text"] = improved
        edit_screen(call, f"👁 النص المحسن:\n\n{improved}", preview_keyboard(st.get("content_type", "quote")))
    elif data == "save_current":
        db_execute("INSERT INTO posts(text,content_type,theme,style,status,created_at) VALUES(?,?,?,?,?,?)", (clean_text(st.get("text", "")), st.get("content_type"), st.get("theme"), st.get("style"), "saved", datetime.now().isoformat(timespec="seconds")))
        edit_screen(call, "⭐ تم حفظ المحتوى في الارشيف.", preview_keyboard(st.get("content_type", "quote")))
    elif data in ("rate_up", "rate_down"):
        rating = 1 if data == "rate_up" else -1
        db_execute("INSERT INTO posts(text,content_type,theme,style,status,rating,created_at) VALUES(?,?,?,?,?,?,?)", (clean_text(st.get("text", "")), st.get("content_type"), st.get("theme"), st.get("style"), "rated", rating, datetime.now().isoformat(timespec="seconds")))
        edit_screen(call, "⭐ تم تسجيل تقييمك.", preview_keyboard(st.get("content_type", "quote")))
    elif data == "auto_menu" or data == "go_auto_menu":
        edit_screen(call, "🔄 ادارة النشر الدوري التلقائي:", auto_keyboard())
    elif data == "auto_type":
        edit_screen(call, "🎯 اختر نوع المحتوى التلقائي:", auto_type_keyboard())
    elif data.startswith("atype_"):
        set_setting("auto_type", data[6:])
        edit_screen(call, "⏱ ممتاز. اختر الفترة الزمنية:", interval_keyboard())
    elif data == "auto_intervals":
        edit_screen(call, "⏱ اختر فترة النشر التلقائي:", interval_keyboard())
    elif data.startswith("aint_"):
        seconds = int(data.split("_")[1])
        set_auto_interval(seconds)
        edit_screen(call, f"✅ تم تشغيل النشر التلقائي كل {seconds // 3600} ساعة.\nسيولد وينظف وينشر وحده بدون تدخل منك.", auto_keyboard())
    elif data == "auto_custom":
        st["waiting"] = "auto_custom"
        edit_screen(call, "🎯 ارسل عدد الساعات المخصصة.\nمثال: 5 يعني كل 5 ساعات.", pyramid([back_row("auto_menu")]))
    elif data == "auto_stop":
        set_setting("auto_enabled", "0")
        set_setting("next_auto_at", "")
        edit_screen(call, "🛑 تم ايقاف النشر الدوري.", auto_keyboard())
    elif data == "scheduled_menu":
        rows = db_execute("SELECT * FROM schedules WHERE status='pending' ORDER BY publish_at LIMIT 10", fetch=True)
        text = "⏳ المنشورات المجدولة:\n\n" + ("\n".join(f"#{r['id']} • {r['publish_at'].replace('T',' ')} • {CONTENT_TYPES.get(r['content_type'], r['content_type'])}" for r in rows) if rows else "لا توجد منشورات مجدولة.")
        edit_screen(call, text, pyramid([back_row("home")]))
    elif data == "archive":
        rows = db_execute("SELECT * FROM posts ORDER BY id DESC LIMIT 8", fetch=True)
        text = "🗃 اخر محتويات الارشيف:\n\n" + ("\n\n".join(f"#{r['id']} • {r['status']}\n{r['text'][:120]}" for r in rows) if rows else "الارشيف فارغ.")
        edit_screen(call, text, pyramid([back_row("home")]))
    elif data == "stats":
        total = db_execute("SELECT COUNT(*) n FROM posts", fetch=True)[0]["n"]
        published = db_execute("SELECT COUNT(*) n FROM posts WHERE status='published'", fetch=True)[0]["n"]
        automatic = db_execute("SELECT COUNT(*) n FROM posts WHERE source='automatic'", fetch=True)[0]["n"]
        scheduled = db_execute("SELECT COUNT(*) n FROM schedules WHERE status='pending'", fetch=True)[0]["n"]
        usage_count = db_execute("SELECT COUNT(*) n FROM ai_usage", fetch=True)[0]["n"]
        edit_screen(call, f"📊 الاحصائيات\n\n🧠 محتويات مسجلة: {total}\n📢 منشورة: {published}\n🤖 تلقائية: {automatic}\n⏳ مجدولة: {scheduled}\n💡 طلبات AI: {usage_count}", pyramid([back_row("home")]))
    elif data == "usage":
        rows = db_execute("SELECT COUNT(*) calls, COALESCE(SUM(total_tokens),0) tokens, COALESCE(SUM(cost),0) cost FROM ai_usage WHERE created_at>=?", ((datetime.now()-timedelta(days=30)).isoformat(timespec="seconds"),), True)[0]
        edit_screen(call, f"💰 استهلاك AI خلال 30 يوم\n\n🔢 الطلبات: {rows['calls']}\n🧮 التوكنات: {rows['tokens']}\n💵 التكلفة المسجلة: ${rows['cost']:.4f}", pyramid([back_row("home")]))
    elif data == "status":
        enabled = get_setting("auto_enabled") == "1"
        interval = int(get_setting("auto_interval", "21600"))
        next_at = get_setting("next_auto_at") or "غير محدد"
        ctype = get_setting("auto_type", "mix")
        edit_screen(call, f"🟢 حالة البوت\n\n🤖 النشر الدوري: {'يعمل' if enabled else 'متوقف'}\n📝 النوع: {ctype}\n⏱ الفترة: كل {interval/3600:g} ساعة\n🕐 القادم: {next_at.replace('T',' ')}\n🧹 تنظيف النص: مفعل دائما", pyramid([back_row("home")]))
    elif data == "settings":
        edit_screen(call, "⚙️ الاعدادات\n\n🧹 تنظيف الهمزات والحركات: مفعل اجباريا\n💾 حفظ الجدولة والارشيف: مفعل\n⏱ مراقب النشر: خفيف ويعمل كل 20 ثانية", pyramid([back_row("home")]))


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("Smart Telegram Content Manager is running...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
