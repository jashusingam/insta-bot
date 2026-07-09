from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, MessageHandler, ContextTypes, filters, 
    CallbackQueryHandler, CommandHandler
)
from download_handler import MediaDownloader
import os
import logging

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION (Railway Environment Variables)
# ============================================

# Reads the token uploaded as a Railway variable named BOT_TOKEN
TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# Reads a comma-separated string of user IDs from Railway (e.g., "123456,789012")
ALLOWED_USERS_RAW = os.environ.get('ALLOWED_USERS', '')
ALLOWED_USERS = {1362997526, 7509064576}

if ALLOWED_USERS_RAW:
    try:
        ALLOWED_USERS = {int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(',') if uid.strip()}
        logger.info(f"🔒 Bot locked to specific users: {ALLOWED_USERS}")
    except ValueError:
        logger.error("❌ Failed to parse ALLOWED_USERS variable. Ensure it's a comma-separated list of numbers.")

os.makedirs("downloads", exist_ok=True)

# Initialize downloader
downloader = MediaDownloader()

# Store URLs in memory (user_id_message_id -> url)
user_urls = {}

# ============================================
# ACCESS CONTROL HELPER
# ============================================

def is_user_allowed(user_id: int) -> bool:
    """Check if the user is authorized to use the bot"""
    if not ALLOWED_USERS:
        return True  # If the variable isn't set, anyone can use it
    return user_id in ALLOWED_USERS

# ============================================
# COMMAND HANDLERS
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Sorry, you do not have permission to use this bot.")
        return

    await update.message.reply_text(
        "👋 Welcome to Media Downloader Bot!\n\n"
        "📱 Send me a link from:\n"
        "  • Instagram (Reels, Posts, Photos, Carousels)\n"
        "  • Twitter/X (Videos, Photos)\n"
        "  • YouTube\n"
        "  • TikTok\n"
        "  • Facebook\n"
        "  • And 900+ other sites...\n\n"
        "🎬 Videos → Choose MP4 or MP3\n"
        "🖼 Photos → Auto download\n\n"
        "Type /help for more info."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Sorry, you do not have permission to use this bot.")
        return

    await update.message.reply_text(
        "📖 How to Use:\n\n"
        "1️⃣ Send me a direct URL/link\n"
        "2️⃣ For VIDEOS: Choose format (MP4 or MP3)\n"
        "3️⃣ For PHOTOS: Auto downloaded instantly\n"
        "4️⃣ Wait for download\n"
        "5️⃣ I'll send you the file!\n\n"
        "✅ Supported:\n"
        "• Instagram Videos (Reels)\n"
        "• Instagram Photos (single)\n"
        "• Instagram Carousels (multi-photo)\n"
        "• Twitter/X Videos & Photos\n"
        "• YouTube Videos\n"
        "• TikTok Videos\n\n"
        "⏱ Processing time: 30-60 seconds\n"
        "📦 Max file size: 2GB (Telegram limit)"
    )


# ============================================
# MESSAGE HANDLERS
# ============================================

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle when user sends a link
    Detect if it's video or photo and respond accordingly
    """
    user_id = update.message.from_user.id
    
    if not is_user_allowed(user_id):
        await update.message.reply_text("⛔ Sorry, you do not have permission to use this bot.")
        return

    url = update.message.text.strip()
    logger.info(f"User {user_id} sent URL: {url}")
    
    # Validate URL
    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text(
            "❌ Invalid URL\n\n"
            "Please send a valid link starting with http:// or https://\n\n"
            "Examples:\n"
            "• https://www.instagram.com/reel/...\n"
            "• https://www.instagram.com/p/...  (photo)\n"
            "• https://x.com/i/status/...\n"
            "• https://www.youtube.com/watch?v=..."
        )
        return
    
    # Show processing message
    status_msg = await update.message.reply_text("🔍 Detecting media type...")
    
    # Detect if video or photo
    media_type = downloader.detect_media_type(url)
    logger.info(f"Detected media type: {media_type}")
    
    # Store URL
    message_id = update.message.message_id
    request_key = f"{user_id}_{message_id}"
    user_urls[request_key] = url
    
    # Handle based on type
    if media_type == 'photo':
        await handle_photo_download(status_msg, url, user_id)
    else:
        # It's a video - show format selection buttons
        keyboard = [
            [
                InlineKeyboardButton("🎬 MP4 (Video)", callback_data=f"mp4_{request_key}"),
                InlineKeyboardButton("🎵 MP3 (Audio)", callback_data=f"mp3_{request_key}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_msg.edit_text(
            "📥 Choose download format:",
            reply_markup=reply_markup
        )


async def handle_photo_download(status_msg, url: str, user_id: int):
    """
    Download and send Instagram photos
    Handles single photos and carousels
    """
    await status_msg.edit_text("⏳ Downloading photo(s)...")
    
    try:
        # Download photo(s)
        files = downloader.download_photo(url)
        
        if not files:
            await status_msg.edit_text(
                "❌ Photo download failed\n\n"
                "Possible reasons:\n"
                "• Invalid URL\n"
                "• Photo is restricted/private\n"
                "• Network error\n\n"
                "Try again or send a different link."
            )
            return
        
        await status_msg.edit_text("📤 Uploading to Telegram...")
        
        # Send photos
        if len(files) == 1:
            # Single photo
            with open(files[0], 'rb') as f:
                await status_msg.edit_text("Sending photo...")
                await status_msg.message.reply_photo(
                    photo=f,
                    caption="✅ Photo downloaded successfully!"
                )
        else:
            # Multiple photos (carousel)
            media_group = []
            opened_files = []
            
            try:
                for file_path in files[:10]:  # Max 10
                    f = open(file_path, 'rb')
                    opened_files.append(f)
                    media_group.append(InputMediaPhoto(f))
                
                if media_group:
                    await status_msg.message.reply_media_group(media_group)
                    await status_msg.edit_text(f"✅ Carousel with {len(files)} photos downloaded!")
            finally:
                for f in opened_files:
                    f.close()
        
        await status_msg.delete()
        
        # Cleanup
        for file in files:
            downloader.cleanup(file)
        
    except Exception as e:
        logger.error(f"Photo download error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)[:100]}")


async def handle_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle when user clicks MP4 or MP3 button
    Download video and send to user
    """
    query = update.callback_query
    await query.answer()
    
    # Enforce access control on interactive query buttons
    if not is_user_allowed(query.from_user.id):
        await query.edit_message_text("⛔ Sorry, you do not have permission to use this bot.")
        return

    # Parse callback data
    data = query.data.split('_', 1)
    format_type = data[0]  # 'mp4' or 'mp3'
    request_key = data[1]  # 'user_id_message_id'
    
    logger.info(f"User requested {format_type} for key: {request_key}")
    
    # Get URL from storage
    url = user_urls.get(request_key)
    
    if not url:
        await query.edit_message_text(
            "❌ URL not found!\n\n"
            "Please send a new link."
        )
        return
    
    # Update message to show downloading
    await query.edit_message_text(
        f"⏳ Downloading as {format_type.upper()}...\n"
        "This may take 30-60 seconds.\n"
        "Please wait..."
    )
    
    try:
        # Download the file
        logger.info(f"Starting download: {url} as {format_type}")
        file_path = downloader.download_video(url, format_type)
        
        if not file_path:
            logger.error(f"Download failed for: {url}")
            await query.edit_message_text(
                "❌ Download Failed\n\n"
                "Possible reasons:\n"
                "• Invalid or unsupported URL\n"
                "• Video/content is restricted\n"
                "• Private content\n"
                "• Network error\n\n"
                "Try:\n"
                "• Different link\n"
                "• Different platform\n"
                "• Wait and try again"
            )
            return
        
        # Check file size
        file_size = os.path.getsize(file_path)
        size_mb = file_size / (1024 * 1024)
        
        logger.info(f"Downloaded file: {file_path} ({size_mb:.1f} MB)")
        
        # Telegram has 2GB limit
        if file_size > 2147483648:
            logger.warning(f"File too large: {size_mb:.1f} MB")
            await query.edit_message_text(
                f"❌ File Too Large ({size_mb:.1f} MB)\n\n"
                "Telegram has a 2GB upload limit.\n"
                "Try downloading a shorter video."
            )
            downloader.cleanup(file_path)
            return
        
        # Update message to show uploading
        await query.edit_message_text(
            f"📤 Uploading to Telegram...\n"
            f"File size: {size_mb:.1f} MB"
        )
        
        # Send file to user
        if format_type == 'mp4':
            logger.info(f"Sending video file: {file_path}")
            with open(file_path, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=video_file,
                    caption=f"✅ Download Complete!\n📦 {size_mb:.1f} MB | MP4",
                    supports_streaming=True
                )
        else:  # mp3
            logger.info(f"Sending audio file: {file_path}")
            with open(file_path, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=audio_file,
                    caption=f"✅ Download Complete!\n📦 {size_mb:.1f} MB | MP3"
                )
        
        # Delete the button message
        await query.message.delete()
        
        # Clean up downloaded file
        downloader.cleanup(file_path)
        
        # Remove URL from storage
        if request_key in user_urls:
            del user_urls[request_key]
        
        logger.info(f"Successfully sent {format_type} to user")
        
    except Exception as e:
        logger.error(f"Error during download/upload: {str(e)}")
        await query.edit_message_text(
            f"❌ Error Occurred\n\n"
            f"Error: {str(e)[:100]}\n\n"
            "Please try again or use a different link."
        )


# ============================================
# ERROR HANDLER
# ============================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors caused by updates"""
    logger.error(f"Exception while handling an update: {context.error}")


# ============================================
# MAIN BOT SETUP
# ============================================

def main():
    """Start the bot"""
    
    if TOKEN == 'YOUR_BOT_TOKEN_HERE' or not TOKEN:
        print("❌ ERROR: Please set your BOT_TOKEN inside Railway Variables!")
        return
    
    # Create bot application
    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # Add message handler for links
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)
    )
    
    # Add callback handler for button clicks
    application.add_handler(CallbackQueryHandler(handle_format_choice))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    logger.info("🤖 Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
