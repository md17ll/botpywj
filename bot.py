import os
import threading
import time
import random
import re
import requests
import telebot
from telebot import types

# Configuration from Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # مفتاح OpenRouter الخاص بك
CHAT_ID = os.getenv("CHANNEL_OR_CHAT_ID")
ADMIN_ID = os.getenv("ADMIN_ID")

# Initialize Telegram Client
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Global Variables for Control
pending_posts = {}
auto_post_interval = 0  # 0 means turned off
auto_post_type = "quote"  # default type: quote, article, or mix
auto_post_thread = None

def clean_text_for_article(text):
    """تنظيف النص من الهمزات وحركات التشكيل والجر وترك الحروف السادة فقط"""
    # 1. إزالة التشكيل بالكامل (الفتحة، الضمة، الكسرة، السكون، التنوين، الشدة)
    tashkeel_pattern = re.compile(r'[\u064B-\u0652]')
    text = re.sub(tashkeel_pattern, '', text)
    
    # 2. تحويل الألف بكافة أشكال الهمزات إلى ألف عادية (ا)
    text = re.sub(r'[أإآ]', 'ا', text)
    
    return text

def generate_content(prompt_type):
    try:
        url = "https://openrouter.ai/api/v1/chat/completions"
        
        random_themes = ["الوجود", "الزمن", "الوعي الذاتي", "العزلة", "الذاكرة", "الحقيقة", "الوهم", "المصير", "الحياة", "الروح"]
        chosen_theme = random.choice(random_themes)
        
        prompts = {
            "quote": f"اكتب مقولة فلسفية عميقة أو خاطرة فكرية قصيرة جداً (سطرين) تركز بشكل غير مباشر على مفهوم ({chosen_theme}). تحمل عمقاً نفسياً، بلغة عربية فصحى بليغة ورصينة. صِغ شيئاً فريداً وجديداً كلياً، واشترط عدم تكرار الأفكار المستهلكة. أعطني النص مباشرة دون مقدمات أو ترحيب أو هاشتاغات.",
            "article": f"اكتب مقالاً مصغراً وعميقاً (3 فقرات مكثفة) يناقش معضلة وجودية أو فكرة نفسية غامضة حول ({chosen_theme}). الأسلوب شاعري، رصين، وبليغ جداً، مبتكر وغير مكرر، دون مقدمات أو ترحيب."
        }
        
        payload = {
            "model": "openrouter/auto", 
            "messages": [
                {"role": "user", "content": prompts.get(prompt_type, prompts["quote"])}
            ],
            "temperature": 0.9
        }
        
        headers = {
            "Authorization": f"Bearer {GEMINI_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://railway.app",
            "X-Title": "Telegram Auto Bot"
        }
        
        response = requests.post(url, json=payload, headers=headers)
        response_data = response.json()
        
        if 'error' in response_data:
            print(f"⚠️ OpenRouter Error: {response_data['error']['message']}")
            return "حدث خطأ أثناء توليد المحتوى من السيرفر الوسيط."
            
        generated_text = response_data['choices'][0]['message']['content'].strip()
        
        # إذا كان المطلوب مقالاً، نقوم بتطبيق فلتر تنظيف الحروف والهمزات فوراً
        if prompt_type == "article":
            generated_text = clean_text_for_article(generated_text)
            
        return generated_text
        
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return "حدث خطأ أثناء توليد المحتوى."

def get_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_q = types.InlineKeyboardButton("🌌 توليد ومراجعة مقولة عميقة", callback_data="gen_quote")
    btn_a = types.InlineKeyboardButton("🧠 توليد ومراجعة مقال مكثف", callback_data="gen_article")
    btn_instant = types.InlineKeyboardButton("⚡ توليد ونشر فوري (للتجربة)", callback_data="instant_now")
    btn_auto = types.InlineKeyboardButton("⏳ ضبط النشر التلقائي الدوري", callback_data="show_auto_types")
    markup.add(btn_q, btn_a, btn_instant, btn_auto)
    return markup

def get_admin_keyboard(content_type="quote"):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_reg = types.InlineKeyboardButton("🔄 إعادة توليد", callback_data=f"regen_{content_type}")
    btn_pub = types.InlineKeyboardButton("📢 نشر فوري", callback_data="publish")
    btn_sch = types.InlineKeyboardButton("⏳ جدولة هذا المنشور", callback_data="show_schedule")
    
    if content_type == "quote":
        btn_expand = types.InlineKeyboardButton("📝 تحويل لمقال", callback_data="expand")
        markup.add(btn_reg, btn_expand)
    else:
        markup.add(btn_reg)
        
    markup.add(btn_pub, btn_sch)
    return markup

def get_schedule_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_1h = types.InlineKeyboardButton("بعد ساعة واحدة", callback_data="sch_3600")
    btn_6h = types.InlineKeyboardButton("بعد 6 ساعات", callback_data="sch_21600")
    btn_12h = types.InlineKeyboardButton("بعد 12 ساعة", callback_data="sch_43200")
    btn_24h = types.InlineKeyboardButton("بعد 24 ساعة", callback_data="sch_86400")
    btn_back = types.InlineKeyboardButton("🔙 عودة للرئيسية", callback_data="back_to_home")
    markup.add(btn_1h, btn_6h, btn_12h, btn_24h)
    markup.add(btn_back)
    return markup

def get_auto_type_keyboard():
    """لوحة التحكم الشفافة الجديدة لاختيار نوع المحتوى التلقائي"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_type_q = types.InlineKeyboardButton("📝 نشر مقولات فلسفية فقط", callback_data="settype_quote")
    btn_type_a = types.InlineKeyboardButton("📚 نشر مقالات مكثفة (حروف سادة) فقط", callback_data="settype_article")
    btn_type_m = types.InlineKeyboardButton("🔀 نشر ميكس (مقولة أو مقال عشوائياً)", callback_data="settype_mix")
    btn_back = types.InlineKeyboardButton("🔙 عودة للرئيسية", callback_data="back_to_home")
    markup.add(btn_type_q, btn_type_a, btn_type_m, btn_back)
    return markup

def get_auto_post_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_1h = types.InlineKeyboardButton("كل ساعة", callback_data="set_auto_3600")
    btn_6h = types.InlineKeyboardButton("كل 6 ساعات", callback_data="set_auto_21600")
    btn_12h = types.InlineKeyboardButton("كل 12 ساعة", callback_data="set_auto_43200")
    btn_24h = types.InlineKeyboardButton("كل 24 ساعة", callback_data="set_auto_86400")
    btn_stop = types.InlineKeyboardButton("🛑 إيقاف النشر التلقائي الدوري", callback_data="stop_auto")
    btn_back = types.InlineKeyboardButton("🔙 عودة للرئيسية", callback_data="back_to_home")
    markup.add(btn_1h, btn_6h, btn_12h, btn_24h)
    markup.add(btn_stop)
    markup.add(btn_back)
    return markup

def delayed_publish(target_chat_id, text, delay_seconds):
    time.sleep(delay_seconds)
    try:
        bot.send_message(target_chat_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Failed to send scheduled post: {e}")

def auto_post_loop():
    global auto_post_interval, auto_post_type
    while auto_post_interval > 0:
        time.sleep(auto_post_interval)
        if auto_post_interval == 0:
            break
            
        # تحديد نوع المحتوى المطلوب بناء على اختيار الإعدادات
        current_run_type = auto_post_type
        if auto_post_type == "mix":
            current_run_type = random.choice(["quote", "article"])
            
        text = generate_content(current_run_type)
        if text and "حدث خطأ" not in text:
            try:
                bot.send_message(CHAT_ID, text, parse_mode="Markdown")
                print(f"Auto periodic post ({current_run_type}) sent.")
            except Exception as e:
                print(f"Auto post error: {e}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    bot.send_message(message.chat.id, "أهلاً بك في لوحة تحكم البوت الشاملة. اختر ما تريد القيام به الآن:", reply_markup=get_main_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if str(call.from_user.id) != ADMIN_ID:
        return

    global auto_post_interval, auto_post_thread, auto_post_type
    current_text = call.message.text

    if call.data == "instant_now":
        bot.edit_message_text("جاري التوليد والنشر التلقائي في القناة الآن... ⚡", call.message.chat.id, call.message.message_id)
        text = generate_content("quote")
        try:
            bot.send_message(CHAT_ID, text, parse_mode="Markdown")
            bot.edit_message_text(f"🚀 **تم التوليد والنشر بنجاح وبشكل فوري في القناة!**\n\nالنص المنشور:\n_{text}_", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
        except Exception as e:
            bot.edit_message_text(f"❌ **فشل النشر الفوري:**\nالخطأ: {e}", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())

    elif call.data in ["gen_quote", "regen_quote"]:
        bot.edit_message_text("جاري التفكير وتوليد المقولة... 🌌", call.message.chat.id, call.message.message_id)
        text = generate_content("quote")
        if "حدث خطأ" in text:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
        else:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_admin_keyboard("quote"))
        
    elif call.data in ["gen_article", "regen_article"]:
        bot.edit_message_text("جاري كتابة المقال العميق بدون همزات وحركات... 🧠", call.message.chat.id, call.message.message_id)
        text = generate_content("article")
        if "حدث خطأ" in text:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
        else:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_admin_keyboard("article"))

    # المرحلة الأولى لضبط النشر التلقائي: اختيار النوع
    elif call.data == "show_auto_types":
        bot.edit_message_text("📥 **الخطوة 1: اختر نوع المحتوى المراد نشره تلقائياً دورياً:**", call.message.chat.id, call.message.message_id, reply_markup=get_auto_type_keyboard())

    # حفظ النوع والفرع للخطوة الثانية: اختيار الوقت
    elif call.data.startswith("settype_"):
        selected_type = call.data.split("_")[1]
        auto_post_type = selected_type
        
        type_readable = {
            "quote": "المقولات فقط 📝",
            "article": "المقالات (بدون همزات وحركات) فقط 📚",
            "mix": "الميكس العشوائي 🔀"
        }.get(selected_type)
        
        status = f"كل {int(auto_post_interval/3600)} ساعة" if auto_post_interval > 0 else "متوقف حالياً 🛑"
        
        bot.edit_message_text(f"⚙️ **الخطوة 2: تحديد التوقيت الدوري:**\n\nالنوع المختار: {type_readable}\nالوضع الحالي: {status}\n\nاختر الآن الفاصل الزمني للنشر:", call.message.chat.id, call.message.message_id, reply_markup=get_auto_post_keyboard())

    elif call.data.startswith("set_auto_"):
        interval = int(call.data.split("_")[2])
        auto_post_interval = interval
        hours_text = {3600: "ساعة واحدة", 21600: "6 ساعات", 43200: "12 ساعة", 86400: "24 ساعة"}.get(interval)
        
        type_readable = {
            "quote": "مقولات فلسفية",
            "article": "مقالات مكثفة (حروف سادة)",
            "mix": "منشورات متنوعة (ميكس)"
        }.get(auto_post_type)
        
        if auto_post_thread is None or not auto_post_thread.is_alive():
            auto_post_thread = threading.Thread(target=auto_post_loop, daemon=True)
            auto_post_thread.start()
            
        bot.edit_message_text(f"✅ **تم تفعيل النشر التلقائي بنجاح!**\n\nسيعمل البوت تلقائياً على توليد ونشر **{type_readable}** جديدة كل **{hours_text}** في القناة بدون أدنى تدخل يدوي منك.", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())

    elif call.data == "stop_auto":
        auto_post_interval = 0
        bot.edit_message_text("🛑 **تم إيقاف نظام النشر التلقائي الدوري.** لن ينشر البوت مجدداً إلا بطلب يدوي منك.", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
        
    elif call.data == "expand":
        bot.edit_message_text("جاري التوسع في الفكرة وتحويلها لمقال بدون همزات... 📝", call.message.chat.id, call.message.message_id)
        text = generate_content("article")
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_admin_keyboard("article"))
        
    elif call.data == "publish":
        try:
            bot.send_message(CHAT_ID, current_text, parse_mode="Markdown")
            bot.edit_message_text(f"{current_text}\n\n✅ **تم النشر بنجاح فوراً في القناة!**", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
        except Exception as e:
            bot.edit_message_text(f"{current_text}\n\n❌ **فشل النشر:** تأكد من رتبة الآدمن.", call.message.chat.id, call.message.message_id, reply_markup=get_admin_keyboard())

    elif call.data == "show_schedule":
        pending_posts[call.message.message_id] = current_text
        bot.edit_message_text("اختر وقت النشر التلقائي لهذا المنشور المحدد فقط:", call.message.chat.id, call.message.message_id, reply_markup=get_schedule_keyboard())

    elif call.data.startswith("sch_"):
        delay = int(call.data.split("_")[1])
        hours_text = {3600: "ساعة واحدة", 21600: "6 ساعات", 43200: "12 ساعة", 86400: "24 ساعة"}.get(delay)
        saved_text = pending_posts.get(call.message.message_id, current_text)
        
        threading.Thread(target=delayed_publish, args=(CHAT_ID, saved_text, delay), daemon=True).start()
        
        bot.edit_message_text(f"{saved_text}\n\n⏳ **تمت جدولة المنشور بنجاح! سينشر تلقائياً بعد {hours_text}.**", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
        if call.message.message_id in pending_posts:
            del pending_posts[call.message.message_id]

    elif call.data == "back_to_home":
        if call.message.message_id in pending_posts:
            del pending_posts[call.message.message_id]
        bot.edit_message_text("لوحة التحكم الرئيسية:", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())

if __name__ == "__main__":
    print("Periodic Auto-Post Control Bot is running...")
    bot.infinity_polling(skip_pending=True)
