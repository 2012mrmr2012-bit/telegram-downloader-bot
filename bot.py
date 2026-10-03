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
        InlineKeyboardButton("🎬 جودة عالية (HD)", callback_data=f"hd|{url}"),
        InlineKeyboardButton("📱 جودة متوسطة (SD)", callback_data=f"sd|{url}")
    )
    markup.row(
        InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data=f"mp3|{url}")
    )
    
    bot.reply_to(
        message,
        "🎯 تم استلام الرابط بنجاح!\nاختر الجودة أو الصيغة التي ترغب في تحميلها:",
        reply_markup=markup
    )

# التعامل مع الأزرار
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    quality, url = data.split("|", 1)
    
    bot.answer_callback_query(call.id, "⏳ جاري التحميل، انتظر قليلاً...")
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⏳ جاري التحميل من المنصة، انتظر قليلاً..."
        )
    except:
        pass

    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
    }

    if quality == "hd" or quality == "sd":
        # استخدام صيغة مدمجة جاهزة لتجنب مشاكل الـ ffmpeg لو مش متوفر بشكل كامل
        ydl_opts['format'] = 'best[ext=mp4]/best'
    elif quality == "mp3":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    file_path = None
    try:
        os.makedirs("downloads", exist_ok=True)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            
            if quality == "mp3":
                file_path = os.path.splitext(file_path)[0] + ".mp3"

        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="📤 جاري إرسال الملف إليك..."
            )
        except:
            pass
        
        with open(file_path, 'rb') as f:
            if quality == "mp3":
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

print("🤖 البوت يعمل الآن باستخدام Telebot وبدون أي مشاكل...")
bot.infinity_polling()
