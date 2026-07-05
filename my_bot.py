"""
Advanced Instagram & Twitter/X Downloader Telegram Bot
my_bot.py — Main entry point
Supports: videos (HD/SD/Audio), single photos, carousels (multi-image posts)
"""

import os
import logging
import asyncio
from telegram import Update, BotCommand, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from download_handler import DownloadHandler
from metadata_handler import MetadataHandler

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ALLOWED_USERS = {1362997526, 7509064576} 
MAX_DAILY_MB  = 1024
DOWNLOADS_DIR = "downloads"
COOKIES_FILE  = os.path.join(DOWNLOADS_DIR, "cookies.txt")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)

_cookies_env = os.getenv("COOKIES_TXT")
if _cookies_env:
    with open(COOKIES_FILE, "w", encoding="utf-8") as _f:
        _f.write(_cookies_env)
    logger.info("Wrote cookies.txt from COOKIES_TXT environment variable.")

download_handler = DownloadHandler(downloads_dir=DOWNLOADS_DIR,
                                   cookies_file=COOKIES_FILE,
                                   max_daily_mb=MAX_DAILY_MB)
metadata_handler = MetadataHandler()

# ── Commands ────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        "📥 <b>Media Downloader Bot</b>\n\n"
        "Send me any Instagram or Twitter/X link and I'll download it for you.\n\n"
        "🎬 Supports:\n• Instagram Reels, Posts, Photos, Carousels\n• Twitter/X Videos and Photos\n\n"
        "📋 Commands:\n/start — Welcome\n/help — Usage\n/status — Daily usage\n/cancel — Cancel download\n/about — Bot info\n\n"
        "⚡ Powered by yt-dlp"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "📖 <b>How to use:</b>\n\n"
        "1️⃣ Copy an Instagram or Twitter/X link\n"
        "2️⃣ Paste it here\n"
        "3️⃣ Videos → choose quality\nPhotos → sent automatically\n\n"
        "🎬 Video qualities:\n• HD up to 720p\n• SD up to 360p\n• 🎵 Audio only\n\n"
        "🖼 Photo support:\n• Single photos\n• Carousels (up to 10)\n\n"
        f"⚠️ Daily limit: {MAX_DAILY_MB} MB per user"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    used = download_handler.get_daily_usage(uid)
    pct  = (used / MAX_DAILY_MB) * 100
    bar  = _progress_bar(pct)
    await update.message.reply_html(
        f"📊 <b>Your Daily Usage</b>\n<code>{bar}</code> {pct:.1f}%\n"
        f"Used: {used:.2f} MB\nLimit: {MAX_DAILY_MB} MB\nLeft: {max(0, MAX_DAILY_MB - used):.2f} MB"
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if download_handler.cancel_download(uid):
        await update.message.reply_text("❌ Download cancelled.")
    else:
        await update.message.reply_text("ℹ️ No active download.")

async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "🤖 <b>Media Downloader Bot</b>\nVersion 3.0.0\nEngine yt-dlp\nFramework python-telegram-bot\n\n"
        "Supports:\n• Instagram — Reels, Posts, Photos, Carousels\n• Twitter/X — Videos and Photos"
    )

# ── URL handler ──────────────────────────────────────────────────────────────
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        await update.message.reply_text("🚫 Not authorised.")
        return

    if not metadata_handler.is_supported_url(text):
        await update.message.reply_text("⚠️ Unsupported link.")
        return

    used = download_handler.get_daily_usage(uid)
    if used >= MAX_DAILY_MB:
        await update.message.reply_text("🚫 Daily limit reached.")
        return

    status_msg = await update.message.reply_text("🔍 Fetching info…")
    try:
        meta = await metadata_handler.fetch_metadata(text, COOKIES_FILE)
    except Exception as exc:
        await status_msg.edit_text(f"❌ Metadata error: {exc}")
        return

    if meta.get("is_photo"):
        await _handle_photo(update, context, status_msg, text, meta, uid)
        return

    context.user_data["pending_url"]  = text
    context.user_data["pending_meta"] = meta
    keyboard = download_handler.build_quality_keyboard(meta, uid)
    await status_msg.edit_text(metadata_handler.format_meta_message(meta, used, MAX_DAILY_MB),
                               parse_mode="HTML", reply_markup=keyboard)

async def _handle_photo(update, context, status_msg, url, meta, uid):
    used = download_handler.get_daily_usage(uid)
    await status_msg.edit_text(metadata_handler.format_photo_message(meta, used, MAX_DAILY_MB),
                               parse_mode="HTML")
    try:
        results = await download_handler.download_image(url, uid, COOKIES_FILE)
    except Exception as exc:
        await status_msg.edit_text(f"❌ Image download failed: {exc}")
        return

    await status_msg.edit_text("📤 Sending…")
    caption = f"🖼 <b>{meta.get('title','Photo')}</b>\n👤 @{meta.get('uploader','unknown')}\n🔗 <a href='{url}'>Original</a>"
    if len(results) == 1:
        await update.message.reply_photo(photo=open(results[0]["filepath"], "rb"),
                                         caption=caption, parse_mode="HTML")
    else:
        media_group = [InputMediaPhoto(open(r["filepath"], "rb"),
                                       caption=caption if i==0 else None,
                                       parse_mode="HTML" if i==0 else None)
                       for i,r in enumerate(results[:10])]
        await update.message.reply_media_group(media_group)
    await status_msg.delete()
    download_handler.cleanup_list(results)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    if not data.startswith("dl:"): return
    quality = data[3:]
    url  = context.user_data.get("pending_url")
    meta = context.user_data.get("pending_meta")
    if not url:
        await query.edit_message_text("⚠️ Session expired.")
        return
    if quality == "cancel":
        download_handler.cancel_download(uid)
        await query.edit_message_text("❌ Cancelled.")
        return
    await query.edit_message_text(f"⏳ Downloading {quality.upper()}…", parse_mode="HTML")
    def _on_progress(p): asyncio.create_task(query.edit_message_text(f"📥 {p:.1f}% done", parse_mode="HTML"))
    try:
        result = await download_handler.download(url, quality, uid, COOKIES_FILE, _on_progress)
    except Exception as exc:
        await query.edit_message_text(f"❌ Download failed: {exc}")
        return
    await query.edit_message_text("📤 Uploading…")
    caption = f"📹 <b>{meta.get('title','Video')}</b>\n👤 @{meta.get('uploader','unknown')}\n📦 {result['size_mb']:.2f} MB | {quality.upper()}"
    if quality=="audio":
        await query.message.reply_audio(audio=open(result["filepath"],"rb"),caption=caption,parse_mode="HTML")
    else:
        await query.message.reply_video(video=open(result["filepath"],"rb"),caption=caption,parse_mode="HTML",supports_streaming=True)
    await query.delete_message()
    download_handler.cleanup(result.get("filepath"))

def
