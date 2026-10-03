import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- توكن البوت ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8669181055:AAGZ4BSyDcqJeb0AOUIA4BJs3330z6Vt_mI")
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📢 قناة البوت الرسمية", url="https://t.me/A_ToolsX"))
    
    bot.reply_to(
        message,
        "👋 **أهلاً بك يا مروان في بوت التحميل الشامل!** 📥\n\n"
        "أرسل لي رابط أي فيديو أو صوت من (يوتيوب، تيك توك، إلخ)، وسأتيح لك تحميله بسهولة وبجودة عالية.",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: message.text and ("http://" in message.text or "https://" in message.text))
def ask_quality(message):
    url = message.text.strip()
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 تحميل فيديو (HD - 720p)", callback_data=f"video|{url}"),
        InlineKeyboardButton("🎵 تحميل صوت فقط (MP3)", callback_data=f"audio|{url}")
    )
    
    bot.reply_to(
        message,
        "🎯 **تم استلام الرابط بنجاح!**\n\nاختر الصيغة التي ترغب بها من الأزرار أدناه:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    action, url = data.split("|", 1)
    
    bot.answer_callback_query(call.id, "⏳ جاري المعالجة والفحص...")
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⏳ **جاري جلب الملف ومعالجة البيانات، انتظر قليلاً...**",
            parse_mode="Markdown"
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
            video_title = info.get('title', 'ملف بدون عنوان')
            direct_download_url = info.get('url')

        # فحص حجم الملف (حد تيليجرام 50 ميجا)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        # لو الملف حجمه تجاوز الحد الأقصى، نقدم زر تحميل مباشر بشكل شيك ومرتب
        if file_size_mb > 48:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                
            markup_link = InlineKeyboardMarkup()
            if action == "video":
                markup_link.row(InlineKeyboardButton("📥 اضغط هنا لتحميل الفيديو مباشرة (HD)", url=direct_download_url))
                file_type_text = "الفيديو"
                icon = "🎬"
            else:
                markup_link.row(InlineKeyboardButton("📥 اضغط هنا لتحميل الصوت مباشرة (Audio)", url=direct_download_url))
                file_type_text = "الصوت"
                icon = "🎵"
                
            markup_link.row(InlineKeyboardButton("📢 زيارة القناة", url="https://t.me/A_ToolsX"))

            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"⚠️ **عذراً، حجم {file_type_text} كبير جداً ({file_size_mb:.1f}MB)**\n"
                     f"ويتجاوز الحد الأقصى المسموح به في تيليجرام (50MB).\n\n"
                     f"{icon} **تم توفير رابط تحميل مباشر وسريع لك أدناه:**",
                reply_markup=markup_link,
                parse_mode="Markdown"
            )
            return

        # لو حجمه طبيعي وأقل من 50 ميجا، يتم إرساله مباشرة بالشكل الصحيح
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="📤 **جاري إرسال الملف إليك، يرجى الانتظار...**",
                parse_mode="Markdown"
            )
        except:
            pass
        
        with open(file_path, 'rb') as f:
            if action == "audio":
                bot.send_audio(call.message.chat.id, f, caption=f"🎵 **{video_title}**\n✅ تم التحميل بنجاح بواسطة البوت", parse_mode="Markdown")
            else:
                bot.send_video(call.message.chat.id, f, caption=f"🎬 **{video_title} (HD)**\n✅ تم التحميل بنجاح بواسطة البوت", parse_mode="Markdown")

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
                text=f"❌ **حدث خطأ أثناء المعالجة:**\n`{str(e)}`",
                parse_mode="Markdown"
            )
        except:
            pass
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

print("🤖 البوت يعمل بكفاءة وبأزرار تفاعلية أنيقة...")
bot.infinity_polling()
