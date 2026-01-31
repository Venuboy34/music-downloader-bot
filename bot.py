import os
import sys
import requests
import logging
import time
import asyncio
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
API_BASE_URL = os.environ.get("API_BASE_URL", "https://c0d8a915-cabf-4560-b61b-799b5757aff1-00-3jh8y5tlvnt4v.spock.replit.dev")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://Veggo:zero8907@cluster0.o8sxezg.mongodb.net/?appName=Cluster0")
ADMIN_USER_IDS = [int(id.strip()) for id in os.environ.get("ADMIN_USER_IDS", "").split(",") if id.strip()]

# Use /tmp for temp files (Render compatible)
TEMP_DIR = "/tmp/music_bot_temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# MongoDB Connection
try:
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client['music_bot']
    users_collection = db['users']
    downloads_collection = db['downloads']
    stats_collection = db['stats']
    logger.info("✅ MongoDB connected successfully")
except ConnectionFailure as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    db = None


def create_session():
    """Create a requests session with retry logic."""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Connection': 'keep-alive',
        'Accept': '*/*'
    })
    
    return session


http_session = create_session()


def save_user(user_id, username, first_name):
    """Save or update user in database."""
    if not db:
        return
    
    try:
        users_collection.update_one(
            {'user_id': user_id},
            {
                '$set': {
                    'username': username,
                    'first_name': first_name,
                    'last_active': datetime.now()
                },
                '$setOnInsert': {
                    'joined_at': datetime.now(),
                    'total_downloads': 0
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving user: {e}")


def log_download(user_id, video_id, title):
    """Log a download to the database."""
    if not db:
        return
    
    try:
        # Save download record
        downloads_collection.insert_one({
            'user_id': user_id,
            'video_id': video_id,
            'title': title,
            'downloaded_at': datetime.now()
        })
        
        # Increment user download count
        users_collection.update_one(
            {'user_id': user_id},
            {'$inc': {'total_downloads': 1}}
        )
        
        # Update global stats
        stats_collection.update_one(
            {'_id': 'global'},
            {
                '$inc': {'total_downloads': 1},
                '$set': {'last_updated': datetime.now()}
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error logging download: {e}")


def get_stats():
    """Get bot statistics."""
    if not db:
        return None
    
    try:
        total_users = users_collection.count_documents({})
        total_downloads = downloads_collection.count_documents({})
        
        # Get top downloaders
        top_users = list(users_collection.find(
            {},
            {'first_name': 1, 'total_downloads': 1}
        ).sort('total_downloads', -1).limit(5))
        
        return {
            'total_users': total_users,
            'total_downloads': total_downloads,
            'top_users': top_users
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user = update.effective_user
    
    # Save user to database
    save_user(user.id, user.username, user.first_name)
    
    # Send sticker first
    sticker_msg = await update.message.reply_sticker(
        sticker="CAACAgIAAxkBAAEQYt1pfZPhPjP99PZfe3GQoyoKNlrStgACBT0AAiUmaUjLrgS38Ul59jgE"
    )
    
    # Wait a bit then delete the sticker
    await asyncio.sleep(2)
    try:
        await sticker_msg.delete()
    except:
        pass
    
    # Send welcome message
    await update.message.reply_text(
        f"👋 Hello {user.first_name}!\n\n"
        "🎵 *Welcome to Music Downloader Bot!*\n\n"
        "I can help you download music from YouTube.\n\n"
        "📝 *How to use:*\n"
        "Just send me any song name or artist and I'll find it for you!\n\n"
        "💡 *Example:*\n"
        "`Stardust zayn`\n"
        "`Perfect Ed Sheeran`\n\n"
        "📊 Use /stats to see download statistics\n"
        "❓ Use /help for more information",
        parse_mode='Markdown'
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics."""
    stats = get_stats()
    
    if not stats:
        await update.message.reply_text(
            "📊 *Statistics*\n\n"
            "Database not connected. Stats unavailable.",
            parse_mode='Markdown'
        )
        return
    
    # Build top users text
    top_users_text = ""
    for idx, user in enumerate(stats['top_users'], 1):
        name = user.get('first_name', 'Unknown')
        downloads = user.get('total_downloads', 0)
        
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "📊"
        top_users_text += f"{medal} {name}: {downloads} downloads\n"
    
    await update.message.reply_text(
        "📊 *Bot Statistics*\n\n"
        f"👥 Total Users: *{stats['total_users']:,}*\n"
        f"📥 Total Downloads: *{stats['total_downloads']:,}*\n\n"
        "🏆 *Top Downloaders:*\n"
        f"{top_users_text}",
        parse_mode='Markdown'
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users (admin only)."""
    user_id = update.effective_user.id
    
    # Check if user is admin
    if ADMIN_USER_IDS and user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only for administrators.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📢 *Broadcast Command*\n\n"
            "Usage: `/broadcast Your message here`\n\n"
            "This will send the message to all bot users.",
            parse_mode='Markdown'
        )
        return
    
    if not db:
        await update.message.reply_text("❌ Database not connected. Cannot broadcast.")
        return
    
    message = ' '.join(context.args)
    
    status_msg = await update.message.reply_text("📤 Broadcasting message...")
    
    try:
        users = users_collection.find({}, {'user_id': 1})
        
        success = 0
        failed = 0
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user['user_id'],
                    text=f"📢 *Broadcast Message*\n\n{message}",
                    parse_mode='Markdown'
                )
                success += 1
                
                # Small delay to avoid rate limits
                if success % 20 == 0:
                    await status_msg.edit_text(
                        f"📤 Broadcasting...\n✅ Sent: {success}\n❌ Failed: {failed}"
                    )
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Failed to send to {user['user_id']}: {e}")
                failed += 1
        
        await status_msg.edit_text(
            f"✅ *Broadcast Complete!*\n\n"
            f"📤 Sent: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        await status_msg.edit_text(f"❌ Broadcast failed: {str(e)}")


async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's personal statistics."""
    user_id = update.effective_user.id
    
    if not db:
        await update.message.reply_text("❌ Database not connected.")
        return
    
    try:
        user = users_collection.find_one({'user_id': user_id})
        
        if not user:
            await update.message.reply_text("No stats available yet. Start downloading music!")
            return
        
        downloads = user.get('total_downloads', 0)
        joined = user.get('joined_at', datetime.now())
        
        # Get user's rank
        rank = users_collection.count_documents({'total_downloads': {'$gt': downloads}}) + 1
        
        await update.message.reply_text(
            "📊 *Your Statistics*\n\n"
            f"📥 Total Downloads: *{downloads}*\n"
            f"🏆 Global Rank: *#{rank}*\n"
            f"📅 Member Since: {joined.strftime('%B %d, %Y')}\n",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
        await update.message.reply_text("❌ Error fetching your stats.")


async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for music based on user query."""
    query = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Save user activity
    save_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    if not query:
        await update.message.reply_text("❌ Please send a song name to search.")
        return
    
    await update.message.chat.send_action(ChatAction.TYPING)
    
    searching_msg = await update.message.reply_text(
        f"🔍 Searching for '*{query}*'...",
        parse_mode='Markdown'
    )
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            logger.info(f"Searching for: {query} (Attempt {attempt + 1}/{max_retries})")
            
            response = http_session.get(
                f"{API_BASE_URL}/search",
                params={"query": query, "limit": 10},
                timeout=30
            )
            
            logger.info(f"Search response status: {response.status_code}")
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    await searching_msg.edit_text(
                        f"⚠️ Search failed (attempt {attempt + 1}/{max_retries}). Retrying..."
                    )
                    time.sleep(2)
                    continue
                else:
                    await searching_msg.edit_text("❌ Search failed. Please try again.")
                    return
            
            data = response.json()
            results = data.get("results", [])
            
            if not results:
                await searching_msg.edit_text(
                    f"❌ No results found for '*{query}*'.\n\nTry different keywords!",
                    parse_mode='Markdown'
                )
                return
            
            logger.info(f"Found {len(results)} results for: {query}")
            
            keyboard = []
            for idx, result in enumerate(results[:10], 1):
                video_id = result.get("video_id")
                title = result.get("title", "Unknown Title")
                duration = result.get("duration", "N/A")
                
                display_title = title[:45] + "..." if len(title) > 45 else title
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"🎵 {idx}. {display_title} ({duration})",
                        callback_data=f"download_{video_id}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("❌ Cancel", callback_data="cancel")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await searching_msg.edit_text(
                f"🎵 Found *{len(results)}* results for '*{query}*':\n\n"
                "👇 Click on a song to download:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
            
        except Exception as e:
            logger.error(f"Search error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await searching_msg.edit_text(f"⚠️ Error. Retrying... ({attempt + 1}/{max_retries})")
                time.sleep(2)
            else:
                await searching_msg.edit_text("❌ An error occurred. Please try again.")


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download button clicks."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.message.edit_text("❌ Search cancelled.")
        return
    
    video_id = query.data.replace("download_", "")
    user_id = query.from_user.id
    
    download_msg = await query.message.reply_text("⬇️ Preparing download...")
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            await download_msg.chat.send_action(ChatAction.UPLOAD_AUDIO)
            
            logger.info(f"Getting download info for video_id: {video_id} (Attempt {attempt + 1})")
            
            response = http_session.get(
                f"{API_BASE_URL}/download_song",
                params={"video_id": video_id},
                timeout=30
            )
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    await download_msg.edit_text(f"⚠️ Retrying... ({attempt + 1}/{max_retries})")
                    time.sleep(2)
                    continue
                else:
                    await download_msg.edit_text("❌ Failed to get download link.")
                    return
            
            data = response.json()
            title = data.get("title", "Unknown")
            download_path = data.get("download_link", "")
            
            if not download_path:
                await download_msg.edit_text("❌ Download link not available.")
                return
            
            await download_msg.edit_text(f"📥 Downloading: *{title}*...", parse_mode='Markdown')
            
            file_url = f"{API_BASE_URL}{download_path}"
            logger.info(f"Downloading from: {file_url}")
            
            file_response = http_session.get(
                file_url,
                timeout=(30, 180),
                stream=True
            )
            
            if file_response.status_code != 200:
                if attempt < max_retries - 1:
                    await download_msg.edit_text(f"⚠️ Download failed. Retrying... ({attempt + 1}/{max_retries})")
                    time.sleep(2)
                    continue
                else:
                    await download_msg.edit_text("❌ Failed to download the file.")
                    return
            
            file_size = int(file_response.headers.get('content-length', 0))
            file_size_mb = file_size / (1024 * 1024)
            
            if file_size_mb > 50:
                await download_msg.edit_text(
                    f"❌ File is too large ({file_size_mb:.1f}MB).\n"
                    "Telegram limit is 50MB."
                )
                return
            
            # Save to temp directory
            filename = f"{video_id}.mp3"
            filepath = os.path.join(TEMP_DIR, filename)
            
            logger.info(f"Saving file to: {filepath}")
            with open(filepath, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            await download_msg.edit_text(f"📤 Uploading: *{title}*...", parse_mode='Markdown')
            
            # Download thumbnail
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            thumb_path = os.path.join(TEMP_DIR, f"{video_id}_thumb.jpg")
            
            try:
                thumb_response = http_session.get(thumbnail_url, timeout=10)
                if thumb_response.status_code == 200:
                    with open(thumb_path, 'wb') as f:
                        f.write(thumb_response.content)
                else:
                    thumb_path = None
            except Exception as e:
                logger.warning(f"Failed to download thumbnail: {e}")
                thumb_path = None
            
            # Send audio file
            logger.info("Sending audio file to user")
            with open(filepath, 'rb') as audio_file:
                if thumb_path and os.path.exists(thumb_path):
                    with open(thumb_path, 'rb') as thumb_file:
                        await query.message.reply_audio(
                            audio=audio_file,
                            thumbnail=thumb_file,
                            title=title,
                            performer="YouTube",
                            filename=filename,
                            caption=f"🎵 {title}"
                        )
                else:
                    await query.message.reply_audio(
                        audio=audio_file,
                        title=title,
                        performer="YouTube",
                        filename=filename,
                        caption=f"🎵 {title}"
                    )
            
            logger.info("Audio file sent successfully")
            
            # Log download to database
            log_download(user_id, video_id, title)
            
            # Cleanup temp files
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp files: {e}")
            
            # Delete status messages
            try:
                await download_msg.delete()
                await query.message.delete()
            except:
                pass
            
            return
            
        except Exception as e:
            logger.error(f"Download error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await download_msg.edit_text(f"⚠️ Error. Retrying... ({attempt + 1}/{max_retries})")
                time.sleep(2)
            else:
                await download_msg.edit_text(f"❌ An error occurred: {str(e)[:200]}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message."""
    await update.message.reply_text(
        "🎵 *Music Downloader Bot - Help*\n\n"
        "📖 *How to Use:*\n"
        "1️⃣ Send me a song name or artist\n"
        "2️⃣ I'll search YouTube for matches\n"
        "3️⃣ Click on the song you want\n"
        "4️⃣ I'll download and send it to you!\n\n"
        "📝 *Examples:*\n"
        "`Stardust zayn`\n"
        "`Perfect Ed Sheeran`\n\n"
        "⚙️ *Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this help\n"
        "/stats - Global statistics\n"
        "/mystats - Your statistics\n"
        "/broadcast - Send message to all (admin only)\n\n"
        "⚠️ *Limits:*\n"
        "• Max file size: 50MB\n"
        "• Timeout: 3 minutes",
        parse_mode='Markdown'
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("Bot token not set!")
        print("\n❌ ERROR: Bot token not set!")
        print("Please set the BOT_TOKEN environment variable\n")
        sys.exit(1)
    
    logger.info("Creating bot application...")
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("mystats", my_stats))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    application.add_handler(CallbackQueryHandler(download_callback))
    
    application.add_error_handler(error_handler)
    
    logger.info("Bot started successfully! Press Ctrl+C to stop.")
    print("\n✅ Bot is running!")
    print(f"📁 Temp directory: {TEMP_DIR}")
    print(f"🗄️ MongoDB: {'Connected' if db is not None else 'Not connected'}")
    print("Press Ctrl+C to stop\n")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\n👋 Bot stopped!")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
