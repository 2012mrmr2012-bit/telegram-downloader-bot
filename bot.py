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
        "ابعت لي رابط من أي منصة (يوتيوب، تيك توك، إنستغرام، فيسبوك، تويتر، إلخ) وهسألك تحب تنزله بأي جودة!"
    )

# استقبال أي رسالة تحتوي على رابط
@bot.message_handler(func=lambda message: message.text and ("http://" in message.text or "https://www.youtube.com" in message.text or "youtu.be" in message.text or "tiktok.com" in message.text or "instagram.com" in message.text))
def ask_quality(message):
    url = message.text.strip()
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 فيديو (جودة جاهزة)", callback_data=f"video|{url}"),
        InlineKeyboardButton("🎵 صوت فقط (MP3/M4A)", callback_data=f"audio|{url}")
    )
    
    bot.reply_to(
        message,
        "🎯 تم استلام الرابط بنجاح!\nاختر الصيغة التي ترغب في تحميلها:",
        reply_markup=markup
    )

# التعامل مع الأزرار
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    action, url = data.split("|", 1)
    
    bot.answer_callback_query(call.id, "⏳ جاري التحميل، انتظر قليلاً...")
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⏳ جاري التحميل من المنصة، انتظر قليلاً..."
        )
    except:
        pass

    # إعدادات yt-dlp بدون أي حاجة لـ ffmpeg نهائياً
    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }

    if action == "video":
        # اختيار صيغة فيديو مدمجة جاهزة من يوتيوب لا تحتاج دمج
        ydl_opts['format'] = 'best[ext=mp4]/best'
    elif action == "audio":
        # اختيار أفضل صوت متاح بصيغته الأصلية بدون أي عمليات تحويل تطلب ffmpeg
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'

    file_path = None
    try:
        os.makedirs("downloads", exist_ok=True)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="📤 جاري إرسال الملف إليك..."
            )
        except:
            pass
        
        with open(file_path, 'rb') as f:
            if action == "audio":
                bot.send_audio(call.message.chat.id, f, caption="✅ تم تحميل الصوت بنجاح بواسطة البوت")
            else:
                bot.send_video(call.message.chat.id, f, caption="✅ تم تحميل الفيديو بنجاح بواسطة البوت")

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
                text=f"❌ حدث خطأ أثناء التحميل:\n{str(e)}"
            )
        except:
            pass
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

print("🤖 البوت يعمل الآن بكفاءة وبدون أي حاجة لـ FFmpeg...")
bot.infinity_polling()
