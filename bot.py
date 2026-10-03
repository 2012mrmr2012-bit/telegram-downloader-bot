import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- توكن البوت ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8669181055:AAGZ4BSyDcqJeb0AOUIA4BJs3330z6Vt_mI")
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 أهلاً بيك يا غالي!\n\n"
        "أنا بوت التحميل الشامل 📥\n"
        "ابعت لي رابط من أي منصة وهسألك تحب تنزله فيديو بجودة عالية أو صوت!"
    )

@bot.message_handler(func=lambda message: message.text and ("http://" in message.text or "https://" in message.text))
def ask_quality(message):
    url = message.text.strip()
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 فيديو (HD - 720p)", callback_data=f"video|{url}"),
        InlineKeyboardButton("🎵 صوت فقط (Audio MP3)", callback_data=f"audio|{url}")
    )
    
    bot.reply_to(
        message,
        "🎯 تم استلام الرابط بنجاح!\nاختر الصيغة المناسبة:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    action, url = data.split("|", 1)
    
    bot.answer_callback_query(call.id, "⏳ جاري فحص وتحميل الملف...")
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⏳ جاري المعالجة، انتظر قليلاً..."
        )
    except:
        pass

    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }

    if action == "video":
        ydl_opts['format'] = 'best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best'
    elif action == "audio":
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'

    file_path = None
    try:
        os.makedirs("downloads", exist_ok=True)
        
        # استخراج معلومات الفيديو أولاً بدون تحميل كامل لو أمكن، أو التحميل ثم الفحص
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            video_title = info.get('title', 'فيديو بدون عنوان')
            webpage_url = info.get('webpage_url', url)

        # فحص حجم الملف (أقصى حد لتيليجرام 50 ميجا)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        # لو الملف أكبر من 48 ميجا، نفذ الحل البديل (إرسال رابط مباشر عالي الجودة)
        if file_size_mb > 48:
            if file_path and os.path.exists(file_path):
                os.remove(file_path) # مسح الملف الكبير من السيرفر عشان ما يستهلكش مساحة
                
            markup_link = InlineKeyboardMarkup()
            markup_link.row(InlineKeyboardButton("🔗 مشاهدة / تحميل مباشر (HD)", url=webpage_url))
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"⚠️ **عذراً، حجم الفيديو كبير جداً ({file_size_mb:.1f}MB)** ويتجاوز حدود تيليجرام (50MB).\n\n"
                     f"💡 **الحل البديل:** يمكنك تحميله أو مشاهدته مباشرة بأعلى جودة عبر الرابط أدناه:",
                reply_markup=markup_link,
                parse_mode="Markdown"
            )
            return

        # لو حجمه طبيعي وأقل من 50 ميجا، ابعته عادي جداً
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="📤 جاري إرسال الفيديو إليك..."
            )
        except:
            pass
        
        with open(file_path, 'rb') as f:
            if action == "audio":
                bot.send_audio(call.message.chat.id, f, caption=f"✅ {video_title}")
            else:
                bot.send_video(call.message.chat.id, f, caption=f"✅ {video_title} (HD)")

        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass

    except Exception as e:
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ حدث خطأ أثناء المعالجة:\n{str(e)}"
            )
        except:
            pass
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

print("🤖 البوت يعمل بكفاءة مع نظام الحلول البديلة...")
bot.infinity_polling()
