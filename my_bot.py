"""
Advanced Instagram Reel Downloader Telegram Bot
my_bot.py - Main entry point
"""

import os
import logging
import asyncio
from telegram import Update, BotCommand
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

# ─── Logging Setup ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ALLOWED_USERS    = set()                     # Empty = allow all; add int user IDs to restrict
MAX_DAILY_MB     = 1024                      # Daily traffic cap per user (MB)
DOWNLOADS_DIR    = "downloads"
COOKIES_FILE     = os.path.join(DOWNLOADS_DIR, "cookies.txt")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# ─── Handlers ────────────────────────────────────────────────────────────────
download_handler = DownloadHandler(
    downloads_dir=DOWNLOADS_DIR,
    cookies_file=COOKIES_FILE,
    max_daily_mb=MAX_DAILY_MB,
)
metadata_handler = MetadataHandler()


# ── /start ───────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("User %s (%d) started the bot.", user.first_name, user.id)
    await update.message.reply_html(
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        "📥 <b>Reel & Video Downloader Bot</b>\n\n"
        "Send me any <b>Instagram Reel/Post</b> or <b>Twitter/X video</b> URL "
        "and I'll fetch it for you in multiple qualities.\n\n"
        "📋 <b>Commands:</b>\n"
        "/start   — Show this message\n"
        "/help    — Detailed usage guide\n"
        "/status  — Your daily usage stats\n"
        "/cancel  — Cancel current download\n"
        "/about   — Bot info\n\n"
        "⚡ Powered by yt-dlp"
    )


# ── /help ────────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "📖 <b>How to use:</b>\n\n"
        "1️⃣  Copy an Instagram Reel/Post or Twitter/X video URL\n"
        "2️⃣  Paste it here and send\n"
        "3️⃣  Choose your preferred quality\n"
        "4️⃣  Receive the video or audio file\n\n"
        "🎬 <b>Available qualities:</b>\n"
        "• 1088×720 HD  (~1.4 MB)\n"
        "• 544×360 SD   (~0.75 MB)\n"
        "• 480×854 Story (~0.48 MB)\n"
        "• 🎵 Audio only (~0.24 MB)\n\n"
        "⚠️ <b>Limits:</b>\n"
        f"• Daily bandwidth: {MAX_DAILY_MB} MB per user\n"
        "• Private accounts require cookies\n\n"
        "🍪 <b>Private content?</b>\n"
        "Ask the admin to add your Instagram cookies."
    )


# ── /status ──────────────────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    used = download_handler.get_daily_usage(uid)
    pct  = (used / MAX_DAILY_MB) * 100
    bar  = _progress_bar(pct)
    await update.message.reply_html(
        f"📊 <b>Your Daily Usage</b>\n\n"
        f"<code>{bar}</code>  {pct:.1f}%\n"
        f"Used : <b>{used:.2f} MB</b>\n"
        f"Limit: <b>{MAX_DAILY_MB} MB</b>\n"
        f"Left : <b>{max(0, MAX_DAILY_MB - used):.2f} MB</b>\n\n"
        "Resets at midnight UTC 🌙"
    )


# ── /cancel ──────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    cancelled = download_handler.cancel_download(uid)
    if cancelled:
        await update.message.reply_text("❌ Download cancelled.")
    else:
        await update.message.reply_text("ℹ️ No active download to cancel.")


# ── /about ───────────────────────────────────────────────────────────────────
async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "🤖 <b>Instagram Reel Downloader</b>\n\n"
        "Version : <code>2.0.0</code>\n"
        "Engine  : <code>yt-dlp</code>\n"
        "Framework: <code>python-telegram-bot 20.x</code>\n\n"
        "📦 Files:\n"
        "<code>my_bot.py</code>          — Main bot\n"
        "<code>download_handler.py</code> — Download engine\n"
        "<code>metadata_handler.py</code> — Metadata parser\n"
        "<code>downloads/</code>          — Temp storage\n"
        "<code>downloads/cookies.txt</code> — Auth cookies\n"
    )


# ── URL message handler ───────────────────────────────────────────────────────
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    text = update.message.text.strip()

    # Access control
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        await update.message.reply_text("🚫 You are not authorised to use this bot.")
        return

    # Validate URL
    if not metadata_handler.is_supported_url(text):
        await update.message.reply_text(
            "⚠️ That doesn't look like a supported link.\n\n"
            "I can download from:\n"
            "• Instagram — <code>instagram.com/reel/ABC123/</code>\n"
            "• Twitter/X — <code>x.com/user/status/12345</code>",
            parse_mode="HTML",
        )
        return

    # Daily limit check
    used = download_handler.get_daily_usage(uid)
    if used >= MAX_DAILY_MB:
        await update.message.reply_text(
            f"🚫 Daily limit reached ({MAX_DAILY_MB} MB).\n"
            "Your quota resets at midnight UTC."
        )
        return

    # Fetch metadata & show quality picker
    status_msg = await update.message.reply_text("🔍 Fetching video info…")
    try:
        meta = await metadata_handler.fetch_metadata(text, COOKIES_FILE)
    except Exception as exc:
        logger.error("Metadata error: %s", exc)
        await status_msg.edit_text(f"❌ Could not fetch video info.\n<code>{exc}</code>", parse_mode="HTML")
        return

    # Store URL in user_data for callback
    context.user_data["pending_url"]  = text
    context.user_data["pending_meta"] = meta

    keyboard = download_handler.build_quality_keyboard(meta, uid)
    await status_msg.edit_text(
        metadata_handler.format_meta_message(meta, used, MAX_DAILY_MB),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── Callback: quality chosen ──────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    uid  = query.from_user.id
    data = query.data  # e.g. "dl:1088x720" or "dl:audio"

    if not data.startswith("dl:"):
        return

    quality = data[3:]
    url     = context.user_data.get("pending_url")
    meta    = context.user_data.get("pending_meta")

    if not url:
        await query.edit_message_text("⚠️ Session expired. Please send the URL again.")
        return

    await query.edit_message_text(f"⏳ Downloading <b>{quality}</b>…", parse_mode="HTML")

    def _on_progress(p: float) -> None:
        # This now runs on the main event loop thread (scheduled via
        # call_soon_threadsafe from download_handler), so create_task is safe here.
        async def _update():
            try:
                await query.edit_message_text(
                    f"📥 Downloading <b>{quality}</b>…\n{_progress_bar(p)}  {p:.1f}%",
                    parse_mode="HTML",
                )
            except Exception:
                # Telegram throttles identical/rapid edits — ignore those failures
                pass
        asyncio.create_task(_update())

    try:
        result = await download_handler.download(
            url=url,
            quality=quality,
            user_id=uid,
            cookies_file=COOKIES_FILE,
            progress_callback=_on_progress,
        )
    except Exception as exc:
        logger.error("Download error for uid=%d: %s", uid, exc)
        await query.edit_message_text(f"❌ Download failed.\n<code>{exc}</code>", parse_mode="HTML")
        return

    await query.edit_message_text("📤 Uploading to Telegram…")

    try:
        caption = (
            f"📹 <b>{meta.get('title', 'Video')}</b>\n"
            f"👤 @{meta.get('uploader', 'unknown')}\n"
            f"⏱ {meta.get('duration_str', '')}\n"
            f"📦 {result['size_mb']:.2f} MB  |  {quality}\n\n"
            f"🔗 <a href='{url}'>Original</a>"
        )
        if quality == "audio":
            await query.message.reply_audio(
                audio=open(result["filepath"], "rb"),
                caption=caption,
                parse_mode="HTML",
                title=meta.get("title", "Audio"),
                performer=meta.get("uploader", ""),
            )
        else:
            await query.message.reply_video(
                video=open(result["filepath"], "rb"),
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
                width=result.get("width"),
                height=result.get("height"),
            )
        await query.delete_message()
    except Exception as exc:
        logger.error("Upload error: %s", exc)
        await query.edit_message_text(f"❌ Upload failed.\n<code>{exc}</code>", parse_mode="HTML")
    finally:
        download_handler.cleanup(result.get("filepath"))


# ── Helpers ───────────────────────────────────────────────────────────────────
def _progress_bar(pct: float, width: int = 10) -> str:
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


# ── Bot setup & run ───────────────────────────────────────────────────────────
async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",  "Welcome message"),
        BotCommand("help",   "Usage guide"),
        BotCommand("status", "Daily usage stats"),
        BotCommand("cancel", "Cancel current download"),
        BotCommand("about",  "Bot info"),
    ])
    logger.info("Bot commands registered.")


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("about",  cmd_about))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
