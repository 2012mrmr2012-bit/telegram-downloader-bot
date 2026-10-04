import os
import re
import json
import time
import hmac
import uuid
import shutil
import hashlib
import threading
import subprocess
import urllib.parse
import socket
import secrets
import ipaddress

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import yt_dlp
from flask import Flask, request, jsonify, send_file, abort
from waitress import serve

# ------------------------------------------------------------------
# الإعدادات
# ------------------------------------------------------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(BOT_TOKEN, num_threads=8)

PORT = int(os.getenv("PORT", "8080"))
_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
# رابط الميني اب: يتحدد تلقائياً من دومين Railway، أو يدوياً من متغير WEBAPP_URL
WEBAPP_URL = (os.getenv("WEBAPP_URL") or (f"https://{_domain}" if _domain else "")).rstrip("/")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")

SEND_LIMIT_MB = 48              # الحد اللي نبدأ عنده الضغط
TARGET_MB = 46                  # الحجم المستهدف بعد الضغط
MAX_FINAL_MB = 49               # أي ملف بعد الضغط أكبر من كده نرفضه
MAX_TRIM_SOURCE_SEC = 2 * 3600  # أقصى مدة للفيديو الأصلي عند القص (ساعتين)
MAX_PREVIEW_BYTES = 1_500_000_000
JOB_TTL = 45 * 60               # صلاحية الطلب (بالثواني)
LINK_TTL = int(os.getenv("LINK_TTL_MIN", "60")) * 60      # صلاحية رابط التحميل المباشر
LINK_MAX_MB = int(os.getenv("LINK_MAX_MB", "1500"))        # أقصى حجم نسمح بتحميله للرابط المباشر

# كوكيز يوتيوب (اختياري): محتوى ملف cookies.txt مشفّر base64 في متغير YT_COOKIES_B64
COOKIES_B64 = os.getenv("YT_COOKIES_B64", "")
COOKIES_PATH = os.path.join(BASE_DIR, "cookies.txt")
# بروكسي سكني (اختياري) لتجاوز حظر IP السيرفر: مثال http://user:pass@host:port
YT_PROXY = os.getenv("YT_PROXY", "")

# الطلبات: key -> {url, user_id, chat_id, state, ts, path, title, duration, direct, error}
# state: new | downloading | ready | processing | error
jobs = {}
jobs_lock = threading.Lock()

# روابط التحميل المباشر: token -> {path, name, exp}
links = {}
links_lock = threading.Lock()

# أقصى عدد عمليات ثقيلة (تحميل/قص/ضغط) في نفس الوقت، عشان ما تنفد الذاكرة/القرص
heavy = threading.BoundedSemaphore(int(os.getenv("MAX_PARALLEL", "2")))
# أقصى عدد طلبات نشطة للمستخدم الواحد
MAX_JOBS_PER_USER = 3


# ------------------------------------------------------------------
# أدوات مساعدة
# ------------------------------------------------------------------
def safe_edit(chat_id, msg_id, text, markup=None):
    try:
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                              reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass


def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def fmt_time(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(text):
    """يحول 90 أو 1:30 أو 01:02:03 إلى ثواني. يرجع None لو الصيغة غلط."""
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums):
        return None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def parse_range(text):
    """يقبل: 00:30 01:45  أو  00:30 - 01:45  أو  30 to 105  (وأرقام عربية)."""
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    text = text.replace("：", ":").replace("إلى", " ").replace("الى", " ")
    parts = [p for p in re.split(r"\s*(?:-|–|—|to)\s*|\s+", text.strip()) if p]
    if len(parts) != 2:
        return None
    start, end = parse_time(parts[0]), parse_time(parts[1])
    if start is None or end is None:
        return None
    return start, end


URL_RE = re.compile(r"https?://[^\s]+")


def is_safe_url(url):
    """يرفض الروابط اللي تشاور على عناوين داخلية (localhost / شبكة Railway الداخلية)."""
    try:
        host = urllib.parse.urlparse(url).hostname
        if not host:
            return False
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


def verify_init_data(init_data, max_age=86400):
    """يتحقق من توقيع تيليجرام لبيانات الميني اب ويرجع بيانات المستخدم أو None."""
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        if time.time() - int(parsed.get("auth_date", "0")) > max_age:
            return None
        return json.loads(parsed["user"])
    except Exception:
        return None


# ------------------------------------------------------------------
# التحميل
# ------------------------------------------------------------------
def ydl_base_opts():
    opts = {'noplaylist': True, 'quiet': True}
    if os.path.exists(COOKIES_PATH):
        opts['cookiefile'] = COOKIES_PATH
    if YT_PROXY:
        opts['proxy'] = YT_PROXY
    return opts


def download_media(url, action, max_bytes=None):
    """يحمّل الملف ويرجع (المسار، العنوان، المدة، الرابط المباشر)."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    token = uuid.uuid4().hex[:8]  # عشان أكثر من مستخدم لنفس الفيديو ما يتعارضوش

    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/{token}_%(id)s.%(ext)s',
        'noplaylist': True,
        'socket_timeout': 60,
        'retries': 10,
        'merge_output_format': 'mp4',
    }
    if os.path.exists(COOKIES_PATH):
        opts['cookiefile'] = COOKIES_PATH
    if YT_PROXY:
        opts['proxy'] = YT_PROXY
    if max_bytes:
        opts['max_filesize'] = max_bytes

    if action == "audio":
        opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
    elif shutil.which("ffmpeg"):
        # ffmpeg موجود → ندمج أفضل فيديو مع أفضل صوت
        opts['format'] = ('bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/'
                          'best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best')
    else:
        # ffmpeg غير موجود → صيغ جاهزة بملف واحد (بدون دمج)
        opts['format'] = 'best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best'

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        rd = info.get('requested_downloads') or []
        file_path = rd[0].get('filepath') if rd else None
        if not file_path or not os.path.exists(file_path):
            file_path = ydl.prepare_filename(info)

    if not os.path.exists(file_path):
        raise RuntimeError("تعذّر تحميل الملف (قد يكون حجمه كبيراً جداً)")

    title = info.get('title', 'ملف بدون عنوان')
    direct = info.get('url') or info.get('webpage_url') or url
    return file_path, title, info.get('duration'), direct


# ------------------------------------------------------------------
# ffmpeg: قص وضغط
# ------------------------------------------------------------------
def run_ffmpeg(cmd):
    if not shutil.which("ffmpeg"):
        print("[ERROR] ffmpeg غير مثبت على السيرفر!", flush=True)
        return False
    try:
        cmd = [cmd[0], "-nostdin", "-hide_banner", "-loglevel", "error"] + cmd[1:]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=900)
    except subprocess.TimeoutExpired:
        print("[ERROR] ffmpeg تجاوز المهلة (900 ثانية)", flush=True)
        return False
    except Exception as e:
        print(f"[ERROR] ffmpeg: {e}", flush=True)
        return False
    if r.returncode != 0:
        hint = " (غالباً نفاد الذاكرة OOM)" if r.returncode in (-9, 137) else ""
        print(f"[ERROR] ffmpeg فشل (code={r.returncode}){hint}:\n" + r.stderr[-1500:], flush=True)
        return False
    return True


def trim_video(input_path, start, end):
    output_path = os.path.splitext(input_path)[0] + "_trim.mp4"
    length = f"{end - start:.3f}"

    # المحاولة 1: إعادة ترميز خفيفة على الذاكرة (دقيقة على الثانية)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-i", input_path,
        "-t", length,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-threads", "2", "-x264-params", "rc-lookahead=0:sync-lookahead=0",
        "-pix_fmt", "yuv420p",
        "-fps_mode", "cfr",                       # يثبّت معدل الإطارات (فيسبوك/تيك توك غالباً VFR)
        "-af", "aresample=async=1:first_pts=0",   # يضبط الصوت على الصورة
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    if run_ffmpeg(cmd) and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path
    safe_remove(output_path)

    # المحاولة 2 (احتياطية): نسخ بدون ترميز، سريعة وبلا ذاكرة (قد تتأخر البداية لأقرب إطار مفتاحي)
    print("[WARN] فشل إعادة الترميز، نجرب القص بالنسخ المباشر", flush=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-i", input_path,
        "-t", length,
        "-c", "copy", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        output_path,
    ]
    if run_ffmpeg(cmd) and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path
    safe_remove(output_path)
    return None


def compress_video(input_path, duration, target_mb=TARGET_MB):
    if not duration or duration <= 0:
        print("[WARN] مدة الفيديو غير معروفة، لا يمكن الضغط", flush=True)
        return None

    audio_kbps = 96
    total_kbps = (target_mb * 8192) / duration
    video_kbps = int(total_kbps - audio_kbps)

    if video_kbps < 200:
        print(f"[WARN] الفيديو طويل جداً ({duration}s)، تخطي الضغط", flush=True)
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
        "-c:v", "libx264", "-preset", "veryfast", "-threads", "2",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-vf", f"scale=-2:'min(ih,{height})'",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        output_path,
    ]
    if not run_ffmpeg(cmd):
        safe_remove(output_path)
        return None

    if os.path.getsize(output_path) / (1024 * 1024) > MAX_FINAL_MB:
        print("[WARN] الملف بعد الضغط ما زال كبيراً", flush=True)
        safe_remove(output_path)
        return None
    return output_path


def compress_audio(input_path, duration, target_mb=TARGET_MB):
    if not duration or duration <= 0:
        return None

    audio_kbps = min(int((target_mb * 8192) / duration), 128)
    if audio_kbps < 32:
        return None

    output_path = os.path.splitext(input_path)[0] + "_compressed.mp3"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn",
           "-c:a", "libmp3lame", "-b:a", f"{audio_kbps}k", output_path]
    if not run_ffmpeg(cmd):
        safe_remove(output_path)
        return None

    if os.path.getsize(output_path) / (1024 * 1024) > MAX_FINAL_MB:
        safe_remove(output_path)
        return None
    return output_path


# ------------------------------------------------------------------
# التسليم: قص ← ضغط ← إرسال (ويمسح الملف في الآخر)
# ------------------------------------------------------------------
def deliver(chat_id, msg_id, file_path, title, duration, action, direct_url, trim=None):
    keep = False   # True لما الملف يتحفظ لرابط التحميل المباشر
    try:
        if trim:
            safe_edit(chat_id, msg_id, "✂️ **جاري قص الفيديو...**")
            trimmed = trim_video(file_path, trim[0], trim[1])
            safe_remove(file_path)
            file_path = trimmed
            if not trimmed:
                if not shutil.which("ffmpeg"):
                    safe_edit(chat_id, msg_id,
                              "❌ **فشل القص: ffmpeg غير مثبت على السيرفر.**\n"
                              "أضف المتغير `RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg` في Railway.")
                else:
                    safe_edit(chat_id, msg_id,
                              "❌ **فشل قص الفيديو.** تفاصيل الخطأ في logs السيرفر (`[ERROR] ffmpeg`).")
                return
            duration = trim[1] - trim[0]
            title += f"\n✂️ من {fmt_time(trim[0])} إلى {fmt_time(trim[1])}"

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        # --- أكبر من حد تيليجرام → رابط تحميل مباشر من السيرفر ---
        if file_size_mb > SEND_LIMIT_MB and WEBAPP_URL:
            token = secrets.token_urlsafe(16)
            ext = os.path.splitext(file_path)[1] or (".mp3" if action == "audio" else ".mp4")
            base = re.sub(r'[\\/:*?"<>|\r\n]+', ' ', title.split("\n")[0]).strip()[:80] or "file"
            with links_lock:
                links[token] = {'path': file_path, 'name': base + ext, 'exp': time.time() + LINK_TTL}
            keep = True
            kind = "الصوت" if action == "audio" else "الفيديو"
            markup_link = InlineKeyboardMarkup()
            markup_link.row(InlineKeyboardButton(f"📥 تحميل {kind} مباشرة", url=f"{WEBAPP_URL}/dl/{token}"))
            safe_edit(
                chat_id, msg_id,
                f"⚠️ **حجم {kind} كبير ({file_size_mb:.1f}MB)** ويتجاوز حد تيليجرام (50MB).\n\n"
                f"✨ **تم تجهيز رابط تحميل مباشر، صالح لمدة {LINK_TTL // 60} دقيقة:**",
                markup_link
            )
            return

        # --- بدون دومين عام: نضغط الملف كحل بديل ---
        if file_size_mb > SEND_LIMIT_MB:
            safe_edit(chat_id, msg_id, "🗜️ **الملف كبير، جاري ضغطه ليناسب تيليجرام... قد يستغرق دقائق**")
            if action == "video":
                compressed = compress_video(file_path, duration)
            else:
                compressed = compress_audio(file_path, duration)
            if compressed:
                safe_remove(file_path)
                file_path = compressed
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if file_size_mb > SEND_LIMIT_MB:
            safe_edit(chat_id, msg_id,
                      f"⚠️ **الملف كبير جداً ({file_size_mb:.1f}MB)** ولم أتمكن من ضغطه.")
            return

        safe_edit(chat_id, msg_id, "📤 **جاري إرسال الملف إليك، يرجى الانتظار...**")

        caption = f"{title}\n✅ تم التحميل بواسطة Vortex downloader"
        with open(file_path, 'rb') as f:
            if action == "audio":
                bot.send_audio(chat_id, f, caption="🎵 " + caption, timeout=300)
            else:
                bot.send_video(chat_id, f, caption="🎬 " + caption,
                               supports_streaming=True, timeout=300)

        try:
            bot.delete_message(chat_id, msg_id)
        except Exception:
            pass

    except Exception as e:
        safe_edit(chat_id, msg_id, f"❌ **حدث خطأ أثناء المعالجة:**\n`{str(e)[:300]}`")
    finally:
        if not keep:
            safe_remove(file_path)


def process_request(chat_id, msg_id, url, action, trim=None):
    """المسار العادي: تحميل ثم تسليم (ويدعم القص النصي كبديل للميني اب)."""
    file_path = None
    heavy.acquire()
    try:
        if trim:
            with yt_dlp.YoutubeDL(ydl_base_opts()) as ydl:
                meta = ydl.extract_info(url, download=False)
            total = meta.get('duration')
            start, end = trim
            if total:
                if start >= total:
                    safe_edit(chat_id, msg_id,
                              f"❌ **وقت البداية بعد نهاية الفيديو.**\nمدة الفيديو: `{fmt_time(total)}`")
                    return
                if total > MAX_TRIM_SOURCE_SEC:
                    safe_edit(chat_id, msg_id, "❌ **الفيديو طويل جداً للقص (أكثر من ساعتين).**")
                    return
                end = min(end, total)
            trim = (start, end)

        safe_edit(chat_id, msg_id, "⏳ **جاري جلب الملف ومعالجة البيانات، انتظر قليلاً...**")
        file_path, title, duration, direct = download_media(url, action, max_bytes=LINK_MAX_MB * 1024 * 1024)
        deliver(chat_id, msg_id, file_path, title, duration, action, direct, trim)
    except Exception as e:
        safe_edit(chat_id, msg_id, f"❌ **حدث خطأ أثناء المعالجة:**\n`{str(e)[:300]}`")
        safe_remove(file_path)
    finally:
        heavy.release()


# ------------------------------------------------------------------
# الميني اب: السيرفر
# ------------------------------------------------------------------
app = Flask(__name__)


def auth_job():
    """يتحقق من توقيع تيليجرام ومن إن الطلب يخص نفس المستخدم، ويرجع سبب الرفض."""
    data = request.get_json(silent=True) or {}
    key = str(data.get("key", ""))
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    job = jobs.get(key)
    if not user:
        reason = "bad_signature"
    elif not job:
        reason = "expired"
    elif user.get("id") != job["user_id"]:
        reason = "wrong_user"
    else:
        return job, key, data, None
    print(f"[AUTH] رفض الطلب: {reason} (key={key}, عدد الطلبات={len(jobs)})", flush=True)
    return None, key, data, reason


def prepare_job(key):
    job = jobs.get(key)
    if not job:
        return
    heavy.acquire()
    try:
        with yt_dlp.YoutubeDL(ydl_base_opts()) as ydl:
            meta = ydl.extract_info(job['url'], download=False)
        total = meta.get('duration')
        if total and total > MAX_TRIM_SOURCE_SEC:
            raise ValueError("الفيديو طويل جداً للقص (أكثر من ساعتين)")
        path, title, duration, direct = download_media(job['url'], "video", MAX_PREVIEW_BYTES)
        job.update(path=path, title=title, duration=duration or total,
                   direct=direct, state='ready')
    except Exception as e:
        print(f"[ERROR] prepare_job: {e}", flush=True)
        job.update(state='error', error=str(e)[:200])
    finally:
        heavy.release()


def run_trim_job(key, start, end):
    job = jobs.get(key)
    if not job:
        return
    heavy.acquire()
    try:
        status = bot.send_message(job['chat_id'], "✂️ **جاري قص المقطع...**", parse_mode="Markdown")
        deliver(job['chat_id'], status.message_id, job['path'], job['title'],
                job['duration'], "video", job.get('direct'), (start, end))
    except Exception as e:
        print(f"[ERROR] run_trim_job: {e}", flush=True)
    finally:
        heavy.release()
        safe_remove(job.get('path'))
        with jobs_lock:
            jobs.pop(key, None)


@app.get("/")
def health():
    return "ok"


@app.get("/app")
def mini_app():
    resp = send_file(INDEX_PATH, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/create")
def api_create():
    """ينشئ طلباً جديداً من الميني اب (عند الفتح من زر Open بدون رابط)."""
    data = request.get_json(silent=True) or {}
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user:
        return jsonify(ok=False, error="bad_signature"), 403
    m = URL_RE.search(str(data.get("url", "")))
    url = m.group(0) if m else ""
    if not url or not is_safe_url(url):
        return jsonify(ok=False, error="bad_url"), 400
    uid = user["id"]
    if sum(1 for j in list(jobs.values()) if j['user_id'] == uid) >= MAX_JOBS_PER_USER:
        return jsonify(ok=False, error="too_many"), 429
    key = uuid.uuid4().hex[:16]
    jobs[key] = {'url': url, 'user_id': uid, 'chat_id': uid,
                 'state': 'new', 'ts': time.time()}
    return jsonify(ok=True, key=key)


def run_download_job(key, action):
    job = jobs.get(key)
    if not job:
        return
    try:
        status = bot.send_message(job['chat_id'], "⏳ **جاري جلب الملف ومعالجة البيانات...**",
                                  parse_mode="Markdown")
        process_request(job['chat_id'], status.message_id, job['url'], action)
    except Exception as e:
        print(f"[ERROR] run_download_job: {e}", flush=True)
    finally:
        jobs.pop(key, None)


@app.post("/api/download")
def api_download():
    job, key, data, reason = auth_job()
    if not job:
        return jsonify(ok=False, error=reason), 403
    action = data.get("action")
    if action not in ("video", "audio"):
        return jsonify(ok=False, error="bad_request"), 400
    with jobs_lock:
        if job['state'] != 'new':
            return jsonify(ok=False, error="busy"), 409
        job['state'] = 'processing'
    threading.Thread(target=run_download_job, args=(key, action), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/prepare")
def api_prepare():
    job, key, _, reason = auth_job()
    if not job:
        return jsonify(ok=False, error=reason), 403
    with jobs_lock:
        should_start = job['state'] == 'new'
        if should_start:
            job['state'] = 'downloading'
    if should_start:
        threading.Thread(target=prepare_job, args=(key,), daemon=True).start()
    return jsonify(ok=True)


@app.get("/api/status")
def api_status():
    job = jobs.get(request.args.get("k", ""))
    if not job:
        return jsonify(state="expired")
    return jsonify(state=job['state'], duration=job.get('duration'),
                   title=job.get('title'), error=job.get('error'))


@app.get("/dl/<token>")
def direct_download(token):
    with links_lock:
        l = links.get(token)
    if not l or time.time() > l['exp'] or not os.path.exists(l['path']):
        return "انتهت صلاحية الرابط، أرسل الرابط للبوت مرة أخرى.", 410
    return send_file(l['path'], as_attachment=True, download_name=l['name'], conditional=True)


@app.get("/media/<key>")
def media(key):
    job = jobs.get(key)
    if (not job or job['state'] != 'ready' or not job.get('path')
            or not os.path.exists(job['path'])):
        abort(404)
    return send_file(job['path'], conditional=True)  # يدعم Range للتمرير داخل الفيديو


@app.post("/api/trim")
def api_trim():
    job, key, data, reason = auth_job()
    if not job:
        return jsonify(ok=False, error=reason), 403
    try:
        start = float(data["start"])
        end = float(data["end"])
    except Exception:
        return jsonify(ok=False, error="bad_request"), 400

    total = job.get('duration')
    start = max(0.0, start)
    if total:
        end = min(end, float(total))
    if end - start < 0.5:
        return jsonify(ok=False, error="bad_range"), 400

    with jobs_lock:
        if job['state'] != 'ready':
            return jsonify(ok=False, error="not_ready"), 409
        job['state'] = 'processing'

    threading.Thread(target=run_trim_job, args=(key, start, end), daemon=True).start()
    return jsonify(ok=True)


def janitor():
    """ينضف الطلبات القديمة والملفات اللي محدش استخدمها."""
    while True:
        time.sleep(300)
        now = time.time()
        with links_lock:
            for t, l in list(links.items()):
                if now > l['exp']:
                    safe_remove(l['path'])
                    links.pop(t, None)
        with jobs_lock:
            for k, j in list(jobs.items()):
                if now - j['ts'] > JOB_TTL and j['state'] in ('new', 'ready', 'error'):
                    safe_remove(j.get('path'))
                    jobs.pop(k, None)


# ------------------------------------------------------------------
# أوامر البوت
# ------------------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 **أهلاً بك في بوت Vortex downloader!** 📥\n\n"
        "أرسل لي رابط أي فيديو أو صوت، وسأتيح لك تحميله بجودة عالية، "
        "أو قص مقطع معين منه ✂️\n"
        "ولو الحجم كبير هضغطه تلقائياً.",
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda m: m.text and ("http://" in m.text or "https://" in m.text))
def ask_quality(message):
    m = URL_RE.search(message.text)
    url = m.group(0) if m else ""
    uid = message.from_user.id if message.from_user else 0
    if not url or not is_safe_url(url):
        bot.reply_to(message, "❌ الرابط غير صالح.")
        return
    if sum(1 for j in list(jobs.values()) if j['user_id'] == uid) >= MAX_JOBS_PER_USER:
        bot.reply_to(message, "⏳ عندك طلبات كثيرة قيد المعالجة، انتظر لحد ما تخلص.")
        return
    key = uuid.uuid4().hex[:16]
    jobs[key] = {
        'url': url,
        'user_id': uid,
        'chat_id': message.chat.id,
        'state': 'new',
        'ts': time.time(),
    }

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 تحميل فيديو (HD - 720p)", callback_data=f"video|{key}"),
        InlineKeyboardButton("🎵 تحميل صوت فقط (MP3)", callback_data=f"audio|{key}")
    )
    if WEBAPP_URL and message.chat.type == "private":
        markup.row(InlineKeyboardButton(
            "✂️ قص مقطع (بالسلايدر)",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}/app?k={key}")
        ))
    else:
        markup.row(InlineKeyboardButton("✂️ قص مقطع من الفيديو", callback_data=f"trim|{key}"))

    bot.reply_to(
        message,
        "🎯 **تم استلام الرابط بنجاح!**\n\nاختر الصيغة التي ترغب بها من الأزرار أدناه:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


def handle_trim_range(message, key):
    """بديل نصي للقص (يشتغل لو الميني اب مش مفعّل أو في المجموعات)."""
    text = (message.text or "").strip()

    if "http://" in text or "https://" in text:
        ask_quality(message)
        return
    if text.startswith("/"):
        bot.reply_to(message, "تم إلغاء القص.")
        return

    job = jobs.get(key)
    if not job:
        bot.reply_to(message, "⚠️ انتهت صلاحية الطلب، أرسل الرابط مرة أخرى.")
        return

    rng = parse_range(text)
    if not rng or rng[0] >= rng[1]:
        msg = bot.reply_to(
            message,
            "❌ **صيغة غير صحيحة.**\nأرسل وقت البداية والنهاية هكذا:\n"
            "`00:30 01:45`\n(ويجب أن تكون النهاية بعد البداية). أو أرسل /cancel للإلغاء.",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, handle_trim_range, key)
        return

    status = bot.reply_to(message, "⏳ **جاري البدء...**", parse_mode="Markdown")
    try:
        process_request(message.chat.id, status.message_id, job['url'], "video", trim=rng)
    finally:
        jobs.pop(key, None)


@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    try:
        action, key = call.data.split("|", 1)
    except ValueError:
        bot.answer_callback_query(call.id, "طلب غير صالح")
        return

    job = jobs.get(key)
    if not job:
        bot.answer_callback_query(call.id, "انتهت صلاحية الطلب، أرسل الرابط مرة أخرى")
        return

    if action == "trim":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "✂️ **أرسل وقت البداية والنهاية للمقطع الذي تريده:**\n\n"
            "مثال: `00:30 01:45`\n"
            "أو: `1:05:00 1:10:30` (ساعة:دقيقة:ثانية)\n"
            "أو بالثواني: `30 105`\n\n"
            "لإلغاء القص أرسل /cancel",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, handle_trim_range, key)
        return

    if job['state'] != 'new':
        bot.answer_callback_query(call.id, "هذا الطلب قيد المعالجة حالياً")
        return

    bot.answer_callback_query(call.id, "⏳ جاري المعالجة والفحص...")
    job['state'] = 'processing'
    try:
        process_request(call.message.chat.id, call.message.message_id, job['url'], action)
    finally:
        jobs.pop(key, None)  # الطلب خلص، نحرر مكانه من حد المستخدم


# ------------------------------------------------------------------
# التشغيل
# ------------------------------------------------------------------
def main():
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    if COOKIES_B64:
        try:
            import base64
            with open(COOKIES_PATH, "wb") as f:
                f.write(base64.b64decode(COOKIES_B64))
            print("🍪 تم تحميل ملف الكوكيز", flush=True)
        except Exception as e:
            print(f"[ERROR] فشل قراءة YT_COOKIES_B64: {e}", flush=True)

    if not shutil.which("ffmpeg"):
        print("[WARN] ffmpeg غير مثبت: لن يعمل القص ولا الضغط حتى تثبّته (RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg)", flush=True)

    if not shutil.which("deno"):
        print("[WARN] deno غير مثبت: يوتيوب يحتاج JavaScript runtime (RAILPACK_PACKAGES=deno)", flush=True)

    if not os.path.exists(INDEX_PATH):
        print("[WARN] ملف index.html غير موجود بجانب bot.py، الميني اب لن يعمل!", flush=True)

    threading.Thread(
        target=lambda: serve(app, host="0.0.0.0", port=PORT, threads=8),
        daemon=True
    ).start()
    threading.Thread(target=janitor, daemon=True).start()

    if WEBAPP_URL:
        print(f"🌐 الميني اب مفعّل: {WEBAPP_URL}/app", flush=True)
    else:
        print("[WARN] لا يوجد دومين عام، سيُستخدم القص النصي بدل الميني اب.", flush=True)

    print("🤖 Vortex downloader يعمل الآن...", flush=True)
    bot.infinity_polling()


if __name__ == "__main__":
    main()
