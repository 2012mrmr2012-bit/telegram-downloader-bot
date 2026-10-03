import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp

# --- التوكن (يأخذ من السيرفر بأمان، أو القيمة المباشرة كاحتياطي) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8669181055:AAGZ4BSyDcqJeb0AOUIA4BJs3330z6Vt_mI")
# --------------------

app = Client("downloader_bot", bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text(
        "👋 أهلاً بيك يا غالي!\n\n"
        "أنا بوت التحميل الشامل 📥\n"
        "ابعت لي رابط من أي منصة (يوتيوب، تيك توك، إنستغرام، فيسبوك، تويتر، إلخ) وهسألك تحب تنزله بأي جودة!"
    )

# استقبال أي رسالة تحتوي على رابط لأي منصة
@app.on_message(filters.text & (filters.regex(r"https?://[^\s]+") | filters.regex(r"www\.[^\s]+")))
async def ask_quality(client, message):
    url = message.text.strip()
    
    # صنع أزرار اختيار الجودة
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 جودة عالية (HD)", callback_data=f"hd|{url}"),
            InlineKeyboardButton("📱 جودة متوسطة (SD)", callback_data=f"sd|{url}")
        ],
        [
            InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data=f"mp3|{url}")
        ]
    ])
    
    await message.reply_text(
        "🎯 تم استلام الرابط بنجاح!\nاختر الجودة أو الصيغة التي ترغب في تحميلها:",
        reply_markup=keyboard
    )

# التعامل مع ضغطة المستخدم على الأزرار
@app.on_callback_query()
async def download_selected_quality(client, callback_query: CallbackQuery):
    data = callback_query.data
    quality, url = data.split("|", 1)
    
    await callback_query.message.edit_text("⏳ جاري التحميل من المنصة، انتظر قليلاً...")

    # إعدادات متوافقة مع كل المنصات لضمان عدم حدوث أخطاء
    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,  # تحميل الفيديو الفردي فقط لو الرابط لقائمة تشغيل
    }

    if quality == "hd":
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
    elif quality == "sd":
        ydl_opts['format'] = 'worst[ext=mp4]/worst'
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
            
            # لو تم تحويله لصوت، امتداد الملف هيتغير لـ mp3
            if quality == "mp3":
                file_path = os.path.splitext(file_path)[0] + ".mp3"

        await callback_query.message.edit_text("📤 جاري إرسال الملف إليك...")
        
        if quality == "mp3":
            await callback_query.message.reply_audio(audio=file_path, caption="✅ تم تحميل الصوت بنجاح بواسطة البوت")
        else:
            await callback_query.message.reply_video(video=file_path, caption="✅ تم تحميل الفيديو بنجاح بواسطة البوت")

        # حذف الملف من الكمبيوتر لتوفير المساحة
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            
        await callback_query.message.delete()

    except Exception as e:
        if quality == "hd" and ("Requested format is not available" in str(e) or "merge" in str(e).lower()):
            try:
                ydl_opts['format'] = 'best'
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    file_path = ydl.prepare_filename(info)
                
                await callback_query.message.edit_text("📤 جاري إرسال الملف إليك...")
                await callback_query.message.reply_video(video=file_path, caption="✅ تم تحميل الفيديو بنجاح")
                
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                await callback_query.message.delete()
                return
            except Exception as inner_e:
                e = inner_e

        await callback_query.message.edit_text(f"❌ حدث خطأ أثناء التحميل:\n{str(e)}")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

print("🤖 البوت يعمل الآن بكامل طاقته لدعم كل المنصات...")
app.run()