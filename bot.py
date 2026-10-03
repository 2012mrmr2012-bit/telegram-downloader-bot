import os
import uuid
import subprocess
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- توكن البوت (من متغير بيئة فقط، بدون قيمة افتراضية) ---
BOT_TOKEN = os.environ["8927019889:AAFTVSH0bPQsquSwlfByHwUR3wcXY6jJwQA"]
bot = telebot.TeleBot(8927019889:AAFTVSH0bPQsquSwlfByHwUR3wcXY6jJwQA)

SEND_LIMIT_MB = 48   # الحد اللي نبدأ عنده الضغط
TARGET_MB = 46       # الحجم المستهدف بعد الضغط (هامش أمان تحت 50MB)
MAX_FINAL_MB = 49    # أي ملف بعد الضغط أكبر من كده نرفضه

# تيليجرام يحدد callback_data بـ 64 بايت، فنخزن الروابط هنا ونمرر مفتاح قصير
pending_urls = {}


# ------------------------------------------------------------------
# دوال الضغط
# ------------------------------------------------------------------
def compress_video(input_path, duration, target_mb=TARGET_MB):
    """يضغط الفيديو لحجم مستهدف. يرجع مسار الملف الجديد أو None."""
    if not duration or duration <= 0:
        return None

    audio_kbps = 96
    total_kbps = (target_mb * 8192) / duration
    video_kbps = int(total_kbps - audio_kbps)

    # لو الجودة هتبقى سيئة جداً، الأفضل الرابط المباشر
    if video_kbps < 200:
        return None

    if video_kbps < 400:
        height = 360
    elif video_kbps < 900:
        height = 480
    else:
        height = 720

    output_path = os.path.splitext(input_path)[0] + "_compressed.mp4"

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-vf", f"scale=-2:'min(ih,{height})'",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=900)
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        return None

    if os.path.getsize(output_path) / (1024 * 1024) > MAX_FINAL_MB:
        os.remove(output_path)
        return None

    return output_path


def compress_audio(input_path, duration, target_mb=TARGET_MB):
    """يحول الصوت لـ MP3 بـ bitrate يناسب الحجم المستهدف."""
    if not duration or duration <= 0:
        return None

    audio_kbps = int((target_mb * 8192) / duration)
    audio_kbps = min(audio_kbps, 128)
    if audio_kbps < 32:
        return None

    output_path = os.path.splitext(input_path)[0] + "_compressed.mp3"

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", f"{audio_kbps}k",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=900)
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        return None

    if os.path.getsize(output_path) / (1024 * 1024) > MAX_FINAL_MB:
        os.remove(output_path)
        return None

    return output_path


def safe_edit(call, text, markup=None):
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    except Exception:
        pass


def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ------------------------------------------------------------------
# الأوامر
# ------------------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 **أهلاً بك في بوت Vortex downloader!** 📥\n\n"
        "أرسل لي رابط أي فيديو أو صوت، وسأتيح لك تحميله بجودة عالية، "
        "ولو الحجم كبير هضغطه تلقائياً أو أديك رابط مباشر.",
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda m: m.text and ("http://" in m.text or "https://" in m.text))
def ask_quality(message):
    url = message.text.strip()
    key = uuid.uuid4().hex[:10]
    pending_urls[key] = url

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 تحميل فيديو (HD - 720p)", callback_data=f"video|{key}"),
        InlineKeyboardButton("🎵 تحميل صوت فقط (MP3)", callback_data=f"audio|{key}")
    )

    bot.reply_to(
        message,
        "🎯 **تم استلام الرابط بنجاح!**\n\nاختر الصيغة التي ترغب بها من الأزرار أدناه:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    try:
        action, key = call.data.split("|", 1)
    except ValueError:
        bot.answer_callback_query(call.id, "طلب غير صالح")
        return

    url = pending_urls.get(key)
    if not url:
        bot.answer_callback_query(call.id, "انتهت صلاحية الطلب، أرسل الرابط مرة أخرى")
        return

    bot.answer_callback_query(call.id, "⏳ جاري المعالجة والفحص...")
    safe_edit(call, "⏳ **جاري جلب الملف ومعالجة البيانات، انتظر قليلاً...**")

    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        'socket_timeout': 60,
        'retries': 10,
    }

    if action == "video":
        ydl_opts['format'] = 'best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best'
    else:
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'

    file_path = None
    try:
        os.makedirs("downloads", exist_ok=True)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            video_title = info.get('title', 'ملف بدون عنوان')
            direct_download_url = info.get('url') or info.get('webpage_url') or url
            duration = info.get('duration')

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        # --- محاولة الضغط لو الملف أكبر من الحد ---
        if file_size_mb > SEND_LIMIT_MB:
            safe_edit(call, "🗜️ **الملف كبير، جاري ضغطه ليناسب تيليجرام... قد يستغرق دقائق**")

            if action == "video":
                compressed = compress_video(file_path, duration)
            else:
                compressed = compress_audio(file_path, duration)

            if compressed:
                safe_remove(file_path)
                file_path = compressed
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        # --- لو لسه كبير (أو الضغط فشل) → رابط مباشر ---
        if file_size_mb > SEND_LIMIT_MB:
            safe_remove(file_path)

            markup_link = InlineKeyboardMarkup()
            if action == "video":
                markup_link.row(InlineKeyboardButton("📥 🎬 تحميل الفيديو مباشرة (HD)", url=direct_download_url))
                file_type_text = "الفيديو"
            else:
                markup_link.row(InlineKeyboardButton("📥 🎵 تحميل الصوت مباشرة", url=direct_download_url))
                file_type_text = "الصوت"

            safe_edit(
                call,
                f"⚠️ **عذراً، حجم {file_type_text} كبير جداً ({file_size_mb:.1f}MB)**\n"
                f"ويتجاوز الحد الأقصى المسموح به في تيليجرام (50MB) "
                f"ولم أتمكن من ضغطه بجودة مقبولة.\n\n"
                f"✨ **تم توفير زر التحميل المباشر أدناه:**",
                markup_link
            )
            return

        safe_edit(call, "📤 **جاري إرسال الملف إليك، يرجى الانتظار...**")

        caption = f"{video_title}\n✅ تم التحميل بواسطة Vortex downloader"
        with open(file_path, 'rb') as f:
            if action == "audio":
                bot.send_audio(call.message.chat.id, f, caption="🎵 " + caption, timeout=300)
            else:
                bot.send_video(call.message.chat.id, f, caption="🎬 " + caption,
                               supports_streaming=True, timeout=300)

        safe_remove(file_path)
        pending_urls.pop(key, None)

        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    except Exception as e:
        safe_edit(call, f"❌ **حدث خطأ أثناء المعالجة:**\n`{str(e)[:300]}`")
        safe_remove(file_path)


print("🤖 Vortex downloader يعمل الآن...")
bot.infinity_polling()
