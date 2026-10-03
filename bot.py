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
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            video_title = info.get('title', 'فيديو بدون عنوان')
            direct_download_url = info.get('url')

        # فحص حجم الملف (أقصى حد لتيليجرام 50 ميجا)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        # لو الملف أكبر من 48 ميجا، امسح الملف وابعث رابط التحميل المباشر للملف
        if file_size_mb > 48:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                
            markup_link = InlineKeyboardMarkup()
            markup_link.row(InlineKeyboardButton("📥 اضغط هنا لتحميل الملف مباشرة", url=direct_download_url))
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"⚠️ **عذراً، حجم الفيديو كبير جداً ({file_size_mb:.1f}MB)** ويتجاوز حدود تيليجرام (50MB).\n\n"
                     f"🔗 **رابط التحميل المباشر للملف (جاهز للتحميل الفوري):**",
                reply_markup=markup_link,
                parse_mode="Markdown"
            )
            return

        # لو حجمه طبيعي وأقل من 50 ميجا، ابعته كفيديو عادي
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

print("🤖 البوت يعمل بكفاءة وبدون أي شروط اشتراك...")
bot.infinity_polling()
