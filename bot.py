import os
import sys
import requests
import logging
import time
import asyncio
import pytz
from datetime import datetime, timedelta
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
from telegram.error import BadRequest, Forbidden
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Flask for health checks (prevents Render timeout)
from flask import Flask
from threading import Thread

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

# Silence Flask logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Configuration from environment variables
API_BASE_URL = os.environ.get("API_BASE_URL", "https://c0d8a915-cabf-4560-b61b-799b5757aff1-00-3jh8y5tlvnt4v.spock.replit.dev")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://Veggo:zero8907@cluster0.o8sxezg.mongodb.net/?appName=Cluster0")
ADMIN_USER_IDS = [int(id.strip()) for id in os.environ.get("ADMIN_USER_IDS", "").split(",") if id.strip()]

# Force subscription channels
FORCE_SUB_CHANNELS = [
    {"username": "zerodev2", "url": "https://t.me/zerodev2"},
    {"username": "mvxyoffcail", "url": "https://t.me/mvxyoffcail"}
]

# Images
WELCOME_IMAGE = "https://api.aniwallpaper.workers.dev/random?type=music"
FORCE_SUB_IMAGE = "https://i.ibb.co/pr2H8cwT/img-8312532076.jpg"
SUBSCRIPTION_IMAGE = "https://i.ibb.co/gMrpRQWP/photo-2025-07-09-05-21-32-7524948058832896004.jpg"

# Use /tmp for temp files (Render compatible)
TEMP_DIR = "/tmp/music_bot_temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# Download/Upload speed configuration - 100Mbps = 12.5MB/s
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for faster transfer
MAX_WORKERS = 8  # Parallel processing for faster speeds

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

# Flask app for health checks (prevents Render timeout)
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    """Health check endpoint for Render."""
    return {
        "status": "ok",
        "bot": "running",
        "timestamp": datetime.now().isoformat()
    }, 200

def run_flask():
    """Run Flask server in background."""
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)


def create_session():
    """Create a requests session with retry logic and optimized for speed."""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
    )
    
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=100,
        pool_maxsize=100,
        pool_block=False
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Connection': 'keep-alive',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br'
    })
    
    return session


http_session = create_session()


def get_greeting():
    """Get greeting based on time of day in Asia/Kolkata timezone (India/Sri Lanka)."""
    # Get current time in Asia/Kolkata timezone
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(kolkata_tz)
    hour = current_time.hour
    
    if 5 <= hour < 12:
        return "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞"
    elif 12 <= hour < 17:
        return "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ ☀️"
    elif 17 <= hour < 21:
        return "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌆"
    else:
        return "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌙"


async def get_seconds(time_str):
    """Convert time string to seconds."""
    try:
        parts = time_str.split()
        if len(parts) != 2:
            return 0
        
        value = int(parts[0])
        unit = parts[1].lower()
        
        if unit in ['minute', 'minutes', 'min', 'mins']:
            return value * 60
        elif unit in ['hour', 'hours', 'hr', 'hrs']:
            return value * 3600
        elif unit in ['day', 'days']:
            return value * 86400
        elif unit in ['week', 'weeks']:
            return value * 604800
        elif unit in ['month', 'months']:
            return value * 2592000  # 30 days
        elif unit in ['year', 'years']:
            return value * 31536000  # 365 days
        else:
            return 0
    except:
        return 0


async def check_user_subscription(user_id, context):
    """Check if user is subscribed to all required channels."""
    for channel in FORCE_SUB_CHANNELS:
        try:
            member = await context.bot.get_chat_member(
                chat_id=f"@{channel['username']}", 
                user_id=user_id
            )
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logger.error(f"Error checking subscription for @{channel['username']}: {e}")
            return False
    return True


async def is_premium_user(user_id):
    """Check if user has active premium subscription."""
    if db is None:
        return False
    
    try:
        user = users_collection.find_one({'user_id': user_id})
        if user and user.get('expiry_time'):
            expiry_time = user['expiry_time']
            if isinstance(expiry_time, str):
                expiry_time = datetime.fromisoformat(expiry_time)
            
            if expiry_time > datetime.now():
                return True
            else:
                # Remove expired premium
                users_collection.update_one(
                    {'user_id': user_id},
                    {'$unset': {'expiry_time': ""}}
                )
                return False
        return False
    except Exception as e:
        logger.error(f"Error checking premium: {e}")
        return False


async def check_download_limit(user_id):
    """Check if user has reached daily download limit (for free users)."""
    if db is None:
        return True  # Allow if DB not connected
    
    # Check if premium user
    if await is_premium_user(user_id):
        return True  # Unlimited for premium
    
    try:
        # Get today's date
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Count downloads today - NO FREE TRIAL, just 5 downloads
        downloads_today = downloads_collection.count_documents({
            'user_id': user_id,
            'downloaded_at': {'$gte': today}
        })
        
        # Free users get ONLY 5 downloads per day (no trial)
        if downloads_today >= 5:
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error checking download limit: {e}")
        return True  # Allow on error


async def get_remaining_downloads(user_id):
    """Get remaining downloads for today."""
    if db is None:
        return "N/A"
    
    # Check if premium user
    if await is_premium_user(user_id):
        return "Unlimited ⭐"
    
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        downloads_today = downloads_collection.count_documents({
            'user_id': user_id,
            'downloaded_at': {'$gte': today}
        })
        
        remaining = 5 - downloads_today
        return f"{remaining}/5"
    except:
        return "5/5"


def save_user(user_id, username, first_name):
    """Save or update user in database."""
    if db is None:
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
    if db is None:
        return
    
    try:
        downloads_collection.insert_one({
            'user_id': user_id,
            'video_id': video_id,
            'title': title,
            'downloaded_at': datetime.now()
        })
        
        users_collection.update_one(
            {'user_id': user_id},
            {'$inc': {'total_downloads': 1}}
        )
        
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
    if db is None:
        return None
    
    try:
        total_users = users_collection.count_documents({})
        total_downloads = downloads_collection.count_documents({})
        premium_users = users_collection.count_documents({'expiry_time': {'$exists': True}})
        
        top_users = list(users_collection.find(
            {},
            {'first_name': 1, 'total_downloads': 1}
        ).sort('total_downloads', -1).limit(5))
        
        return {
            'total_users': total_users,
            'total_downloads': total_downloads,
            'premium_users': premium_users,
            'top_users': top_users
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user = update.effective_user
    
    save_user(user.id, user.username, user.first_name)
    
    # Check if user has premium
    has_premium = await is_premium_user(user.id)
    
    # If not premium, check subscription
    if not has_premium:
        is_subscribed = await check_user_subscription(user.id, context)
        
        if not is_subscribed:
            keyboard = []
            for channel in FORCE_SUB_CHANNELS:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📢 Join {channel['username']}", 
                        url=channel['url']
                    )
                ])
            keyboard.append([
                InlineKeyboardButton("✅ I Joined", callback_data="check_subscription")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await update.message.reply_photo(
                    photo=FORCE_SUB_IMAGE,
                    caption=(
                        f"👋 Hello {user.first_name}!\n\n"
                        "⚠️ You must join our channels to use this bot.\n\n"
                        "Please join all channels below and click 'I Joined' button:"
                    ),
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Error sending force sub photo: {e}")
                await update.message.reply_text(
                    f"👋 Hello {user.first_name}!\n\n"
                    "⚠️ You must join our channels to use this bot.\n\n"
                    "Please join all channels below and click 'I Joined' button:",
                    reply_markup=reply_markup
                )
            return
    
    # Send sticker
    try:
        sticker_msg = await update.message.reply_sticker(
            sticker="CAACAgIAAxkBAAEQYt1pfZPhPjP99PZfe3GQoyoKNlrStgACBT0AAiUmaUjLrgS38Ul59jgE"
        )
        await asyncio.sleep(2)
        try:
            await sticker_msg.delete()
        except:
            pass
    except Exception as e:
        logger.warning(f"Error with sticker: {e}")
    
    # Send welcome message
    greeting = get_greeting()
    
    welcome_text = (
        f"🎵 ʜᴇʏ {user.first_name}, {greeting}\n\n"
        "I ᴀᴍ ᴛʜᴇ ᴍᴏsᴛ ᴘᴏᴡᴇʀғᴜʟ ᴍᴜsɪᴄ ᴅᴏᴡɴʟᴏᴀᴅ ʙᴏᴛ with premium features 🎧\n\n"
        "I ᴄᴀɴ ᴘʀᴏᴠɪᴅᴇ any song you want instantly!\n\n"
        "Just send me the song name 🎶 and enjoy your music anytime!\n\n"
    )
    
    if has_premium:
        welcome_text += "⭐ *Premium User* - Enjoy unlimited downloads!\n\n"
    else:
        welcome_text += "🆓 *Free User* - 5 downloads per day\n\n"
    
    welcome_text += (
        "💡 *Example:*\n"
        "`Stardust zayn`\n"
        "`Perfect Ed Sheeran`\n\n"
        "Use /help for more information"
    )
    
    try:
        await update.message.reply_photo(
            photo=WELCOME_IMAGE,
            caption=welcome_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error sending welcome photo: {e}")
        await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription check callback."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    is_subscribed = await check_user_subscription(user_id, context)
    
    if not is_subscribed:
        await query.answer(
            "❌ You haven't joined all channels yet! Please join and try again.",
            show_alert=True
        )
        return
    
    greeting = get_greeting()
    welcome_text = (
        f"🎵 ʜᴇʏ {query.from_user.first_name}, {greeting}\n\n"
        "I ᴀᴍ ᴛʜᴇ ᴍᴏsᴛ ᴘᴏᴡᴇʀғᴜʟ ᴍᴜsɪᴄ ᴅᴏᴡɴʟᴏᴀᴅ ʙᴏᴛ with premium features 🎧\n\n"
        "I ᴄᴀɴ ᴘʀᴏᴠɪᴅᴇ any song you want instantly!\n\n"
        "Just send me the song name 🎶 and enjoy your music anytime!\n\n"
        "🆓 *Free User* - 5 downloads per day\n\n"
        "💡 *Example:*\n"
        "`Stardust zayn`\n"
        "`Perfect Ed Sheeran`\n\n"
        "Use /help for more information"
    )
    
    try:
        await query.message.delete()
    except:
        pass
    
    try:
        await query.message.reply_photo(
            photo=WELCOME_IMAGE,
            caption=welcome_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error sending welcome photo: {e}")
        await query.message.reply_text(welcome_text, parse_mode='Markdown')


async def myplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's premium plan."""
    user = update.message.from_user
    user_id = user.id
    
    if db is None:
        await update.message.reply_text("❌ Database not connected.")
        return
    
    try:
        data = users_collection.find_one({'user_id': user_id})
        
        if data and data.get('expiry_time'):
            expiry = data['expiry_time']
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            
            expiry_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata"))
            expiry_str = expiry_ist.strftime("%d-%m-%Y\n⏱️ ᴇxᴘɪʀʏ ᴛɪᴍᴇ : %I:%M:%S %p")
            
            current_time = datetime.now(pytz.timezone("Asia/Kolkata"))
            time_left = expiry_ist - current_time
            
            if time_left.total_seconds() <= 0:
                # Expired
                users_collection.update_one(
                    {'user_id': user_id},
                    {'$unset': {'expiry_time': ""}}
                )
                raise Exception("Premium expired")
            
            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            time_left_str = f"{days} ᴅᴀʏꜱ, {hours} ʜᴏᴜʀꜱ, {minutes} ᴍɪɴᴜᴛᴇꜱ"
            
            caption = (
                f"⚜️ <b>ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀ ᴅᴀᴛᴀ :</b>\n\n"
                f"👤 <b>ᴜꜱᴇʀ :</b> {user.mention_html()}\n"
                f"⚡ <b>ᴜꜱᴇʀ ɪᴅ :</b> <code>{user_id}</code>\n"
                f"⏰ <b>ᴛɪᴍᴇ ʟᴇꜰᴛ :</b> {time_left_str}\n"
                f"⌛️ <b>ᴇxᴘɪʀʏ ᴅᴀᴛᴇ :</b> {expiry_str}"
            )
            
            keyboard = [[
                InlineKeyboardButton("💬 Contact Admin", url="https://t.me/Venuboyy")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_photo(
                photo=SUBSCRIPTION_IMAGE,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        else:
            raise Exception("No premium")
            
    except:
        # Free user - show comparison
        caption = (
            f"ʜᴇʏ {user.first_name},\n\n"
            f"ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴ ᴀᴄᴛɪᴠᴇ ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴ.\n\n"
            f"🆓 <b>Free</b>\n\n"
            f"Daily 5 downloads\n\n"
            f"⭐ <b>Premium</b>\n\n"
            f"Unlimited downloading\n\n"
            f"ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ ᴛᴏ ɢᴇᴛ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇꜱꜱ."
        )
        
        keyboard = [[
            InlineKeyboardButton("💬 Contact Admin @Venuboyy", url="https://t.me/Venuboyy")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_photo(
            photo=SUBSCRIPTION_IMAGE,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )


async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add premium to a user (Admin only)."""
    user_id = update.effective_user.id
    
    if not ADMIN_USER_IDS or user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only for administrators.")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text(
            "📌 ᴜsᴀɢᴇ: <code>/add_premium user_id time</code>\n"
            "📅 ᴇxᴀᴍᴘʟᴇ: <code>/add_premium 123456 1 month</code>\n"
            "🧭 ᴀᴄᴄᴇᴘᴛᴇᴅ ꜰᴏʀᴍᴀᴛs: <code>1 day</code>, <code>1 hour</code>, <code>1 min</code>, <code>1 month</code>, <code>1 year</code>",
            parse_mode='HTML'
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        time_str = " ".join(context.args[1:])
        
        seconds = await get_seconds(time_str)
        if seconds <= 0:
            await update.message.reply_text(
                "❌ ɪɴᴠᴀʟɪᴅ ᴛɪᴍᴇ ꜰᴏʀᴍᴀᴛ ❗\n"
                "🕒 ᴘʟᴇᴀsᴇ ᴜsᴇ: <code>1 day</code>, <code>1 hour</code>, <code>1 min</code>, <code>1 month</code>, or <code>1 year</code>",
                parse_mode='HTML'
            )
            return
        
        target_user = await context.bot.get_chat(target_user_id)
        
        time_zone = datetime.now(pytz.timezone("Asia/Kolkata"))
        current_time = time_zone.strftime("%d-%m-%Y | %I:%M:%S %p")
        
        expiry_time = datetime.now() + timedelta(seconds=seconds)
        
        users_collection.update_one(
            {'user_id': target_user_id},
            {
                '$set': {
                    'expiry_time': expiry_time,
                    'premium_since': datetime.now()
                }
            },
            upsert=True
        )
        
        expiry_ist = expiry_time.astimezone(pytz.timezone("Asia/Kolkata"))
        expiry_str = expiry_ist.strftime("%d-%m-%Y | %I:%M:%S %p")
        
        await update.message.reply_text(
            f"ᴘʀᴇᴍɪᴜᴍ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ✅\n\n"
            f"👤 ᴜꜱᴇʀ : {target_user.first_name}\n"
            f"⚡ ᴜꜱᴇʀ ɪᴅ : <code>{target_user_id}</code>\n"
            f"⏰ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇꜱꜱ : <code>{time_str}</code>\n"
            f"⏳ ᴊᴏɪɴɪɴɢ ᴅᴀᴛᴇ : {current_time}\n"
            f"⌛️ ᴇxᴘɪʀʏ ᴅᴀᴛᴇ : {expiry_str}",
            parse_mode='HTML'
        )
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"👋 ʜᴇʏ {target_user.first_name},\n"
                    f"ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴘᴜʀᴄʜᴀꜱɪɴɢ ᴘʀᴇᴍɪᴜᴍ.\n"
                    f"ᴇɴᴊᴏʏ !! ✨🎉\n\n"
                    f"⏰ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇꜱꜱ : <code>{time_str}</code>\n"
                    f"⏳ ᴊᴏɪɴɪɴɢ ᴅᴀᴛᴇ : {current_time}\n"
                    f"⌛️ ᴇxᴘɪʀʏ ᴅᴀᴛᴇ : {expiry_str}"
                ),
                parse_mode='HTML'
            )
        except:
            pass
            
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID!")
    except Exception as e:
        logger.error(f"Error adding premium: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove premium from a user (Admin only)."""
    user_id = update.effective_user.id
    
    if not ADMIN_USER_IDS or user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only for administrators.")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("ᴜꜱᴀɢᴇ : /remove_premium user_id")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        result = users_collection.update_one(
            {'user_id': target_user_id},
            {'$unset': {'expiry_time': "", 'premium_since': ""}}
        )
        
        if result.modified_count > 0:
            await update.message.reply_text("ᴜꜱᴇʀ ʀᴇᴍᴏᴠᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ !")
            
            try:
                target_user = await context.bot.get_chat(target_user_id)
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"👋 ʜᴇʏ {target_user.first_name},\n\nʏᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ʜᴀs ᴇɴᴅᴇᴅ."
                )
            except:
                pass
        else:
            await update.message.reply_text("ᴜɴᴀʙʟᴇ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴜꜱᴇʀ !\nᴀʀᴇ ʏᴏᴜ ꜱᴜʀᴇ, ɪᴛ ᴡᴀꜱ ᴀ ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀ ɪᴅ ?")
            
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID!")
    except Exception as e:
        logger.error(f"Error removing premium: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def premium_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all premium users (Admin only)."""
    user_id = update.effective_user.id
    
    if not ADMIN_USER_IDS or user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only for administrators.")
        return
    
    if db is None:
        await update.message.reply_text("❌ Database not connected.")
        return
    
    try:
        msg = await update.message.reply_text("<i>ꜰᴇᴛᴄʜɪɴɢ...</i>", parse_mode='HTML')
        
        premium_users = users_collection.find({'expiry_time': {'$exists': True}})
        
        text = "⭐ ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀꜱ ʟɪꜱᴛ :\n\n"
        count = 1
        
        for user_data in premium_users:
            try:
                expiry = user_data['expiry_time']
                if isinstance(expiry, str):
                    expiry = datetime.fromisoformat(expiry)
                
                expiry_ist = expiry.astimezone(pytz.timezone("Asia/Kolkata"))
                current_time = datetime.now(pytz.timezone("Asia/Kolkata"))
                
                if expiry_ist > current_time:
                    time_left = expiry_ist - current_time
                    days = time_left.days
                    hours, remainder = divmod(time_left.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    
                    expiry_str = expiry_ist.strftime("%d-%m-%Y | %I:%M:%S %p")
                    
                    try:
                        user = await context.bot.get_chat(user_data['user_id'])
                        name = user.first_name
                    except:
                        name = user_data.get('first_name', 'Unknown')
                    
                    text += (
                        f"{count}. {name}\n"
                        f"👤 ᴜꜱᴇʀ ɪᴅ : {user_data['user_id']}\n"
                        f"⏳ ᴇxᴘɪʀʏ : {expiry_str}\n"
                        f"⏰ ᴛɪᴍᴇ ʟᴇꜰᴛ : {days}d {hours}h {minutes}m\n\n"
                    )
                    count += 1
            except Exception as e:
                logger.error(f"Error processing premium user: {e}")
                continue
        
        if count == 1:
            text += "No premium users found."
        
        await msg.edit_text(text)
        
    except Exception as e:
        logger.error(f"Error listing premium users: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics (Admin only)."""
    user_id = update.effective_user.id
    
    if not ADMIN_USER_IDS or user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ This command is only for administrators.")
        return
    
    stats = get_stats()
    
    if not stats:
        await update.message.reply_text(
            "📊 *Statistics*\n\nDatabase not connected. Stats unavailable.",
            parse_mode='Markdown'
        )
        return
    
    top_users_text = ""
    for idx, user in enumerate(stats['top_users'], 1):
        name = user.get('first_name', 'Unknown')
        downloads = user.get('total_downloads', 0)
        
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "📊"
        top_users_text += f"{medal} {name}: {downloads} downloads\n"
    
    await update.message.reply_text(
        "📊 *Bot Statistics (Admin Only)*\n\n"
        f"👥 Total Users: *{stats['total_users']:,}*\n"
        f"⭐ Premium Users: *{stats['premium_users']:,}*\n"
        f"📥 Total Downloads: *{stats['total_downloads']:,}*\n\n"
        "🏆 *Top Downloaders:*\n"
        f"{top_users_text}",
        parse_mode='Markdown'
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users (admin only)."""
    user_id = update.effective_user.id
    
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
    
    if db is None:
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
                
                if success % 20 == 0:
                    await status_msg.edit_text(
                        f"📤 Broadcasting...\n✅ Sent: {success}\n❌ Failed: {failed}"
                    )
                    await asyncio.sleep(1)
                    
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
    
    # Check subscription for non-premium users
    has_premium = await is_premium_user(user_id)
    if not has_premium:
        is_subscribed = await check_user_subscription(user_id, context)
        if not is_subscribed:
            await update.message.reply_text(
                "⚠️ Please join our channels first! Use /start to see the channels."
            )
            return
    
    if db is None:
        await update.message.reply_text("❌ Database not connected.")
        return
    
    try:
        user = users_collection.find_one({'user_id': user_id})
        
        if not user:
            await update.message.reply_text("No stats available yet. Start downloading music!")
            return
        
        downloads = user.get('total_downloads', 0)
        joined = user.get('joined_at', datetime.now())
        
        rank = users_collection.count_documents({'total_downloads': {'$gt': downloads}}) + 1
        
        status = "⭐ Premium User" if has_premium else "🆓 Free User"
        remaining = await get_remaining_downloads(user_id)
        
        stats_text = (
            "📊 *Your Statistics*\n\n"
            f"{status}\n"
            f"📥 Total Downloads: *{downloads}*\n"
            f"🏆 Global Rank: *#{rank}*\n"
            f"📅 Member Since: {joined.strftime('%B %d, %Y')}\n"
        )
        
        if not has_premium:
            stats_text += f"\n📊 Today's Downloads: *{remaining}*"
        
        keyboard = [[
            InlineKeyboardButton("💬 Get Premium", url="https://t.me/Venuboyy")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
        await update.message.reply_text("❌ Error fetching your stats.")


async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for music based on user query."""
    user_id = update.effective_user.id
    
    # Check premium or subscription
    has_premium = await is_premium_user(user_id)
    if not has_premium:
        is_subscribed = await check_user_subscription(user_id, context)
        if not is_subscribed:
            await update.message.reply_text(
                "⚠️ Please join our channels first! Use /start to see the channels."
            )
            return
    
    query = update.message.text.strip()
    
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
                    await asyncio.sleep(2)
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
                await asyncio.sleep(2)
            else:
                await searching_msg.edit_text("❌ An error occurred. Please try again.")


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download button clicks with optimized 100Mbps speed."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.message.edit_text("❌ Search cancelled.")
        return
    
    video_id = query.data.replace("download_", "")
    user_id = query.from_user.id
    
    # Check download limit for free users (STRICT 5 DOWNLOADS)
    can_download = await check_download_limit(user_id)
    if not can_download:
        remaining = await get_remaining_downloads(user_id)
        
        keyboard = [[
            InlineKeyboardButton("💬 Get Premium", url="https://t.me/Venuboyy")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "⚠️ <b>Daily Download Limit Reached!</b>\n\n"
            "🆓 Free users: 5 downloads per day\n"
            "⭐ Premium users: Unlimited downloads\n\n"
            f"📊 Your today's downloads: {remaining}\n\n"
            "💬 Contact admin to get premium access!",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        return
    
    download_msg = await query.message.reply_text("⬇️ Preparing download at high speed...")
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            logger.info(f"Getting download info for video_id: {video_id} (Attempt {attempt + 1})")
            
            response = http_session.get(
                f"{API_BASE_URL}/download_song",
                params={"video_id": video_id},
                timeout=30
            )
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    await download_msg.edit_text(f"⚠️ Retrying... ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(2)
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
            
            await download_msg.edit_text(f"📥 Downloading: *{title}*\n⚡ Speed: 100Mbps", parse_mode='Markdown')
            
            file_url = f"{API_BASE_URL}{download_path}"
            logger.info(f"Downloading from: {file_url}")
            
            # Optimized download with larger chunks for 100Mbps speed
            file_response = http_session.get(
                file_url,
                timeout=(30, 300),  # Increased timeout for large files
                stream=True
            )
            
            if file_response.status_code != 200:
                if attempt < max_retries - 1:
                    await download_msg.edit_text(f"⚠️ Download failed. Retrying... ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(2)
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
            
            # Save to temp directory with optimized chunk size (1MB for 100Mbps)
            filename = f"{video_id}.mp3"
            filepath = os.path.join(TEMP_DIR, filename)
            
            logger.info(f"Saving file to: {filepath} with 1MB chunks")
            downloaded = 0
            with open(filepath, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            logger.info(f"Downloaded {downloaded / (1024*1024):.2f}MB")
            
            await download_msg.edit_text(f"📤 Uploading: *{title}*\n⚡ Speed: 100Mbps", parse_mode='Markdown')
            
            # Download thumbnail with optimized settings
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
            
            # Send audio file with optimized upload
            logger.info("Uploading audio file to user at high speed")
            with open(filepath, 'rb') as audio_file:
                if thumb_path and os.path.exists(thumb_path):
                    with open(thumb_path, 'rb') as thumb_file:
                        await query.message.reply_audio(
                            audio=audio_file,
                            thumbnail=thumb_file,
                            title=title,
                            performer="YouTube",
                            filename=filename,
                            caption=f"🎵 {title}\n⚡ Downloaded at 100Mbps",
                            write_timeout=300,
                            read_timeout=300
                        )
                else:
                    await query.message.reply_audio(
                        audio=audio_file,
                        title=title,
                        performer="YouTube",
                        filename=filename,
                        caption=f"🎵 {title}\n⚡ Downloaded at 100Mbps",
                        write_timeout=300,
                        read_timeout=300
                    )
            
            logger.info("Audio file sent successfully at high speed")
            
            # Log download
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
                await asyncio.sleep(2)
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
        "/mystats - Your statistics\n"
        "/myplan - Check premium status\n\n"
        "⚠️ *Limits:*\n"
        "🆓 Free: 5 downloads/day\n"
        "⭐ Premium: Unlimited\n"
        "📦 Max file size: 50MB\n"
        "⚡ Speed: 100Mbps",
        parse_mode='Markdown'
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


async def post_init(application: Application) -> None:
    """Clean up any existing webhooks/polling sessions after app initialization."""
    try:
        logger.info("Cleaning up existing connections...")
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook deleted, ready for polling")
    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")


def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("Bot token not set!")
        print("\n❌ ERROR: Bot token not set!")
        print("Please set the BOT_TOKEN environment variable\n")
        sys.exit(1)
    
    # Start Flask health check server in background thread
    logger.info("Starting Flask health check server...")
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask health check server started on port 10000")
    
    logger.info("Creating bot application...")
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .read_timeout(300)  # Increased for large file uploads
        .write_timeout(300)  # Increased for large file uploads
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("mystats", my_stats))
    application.add_handler(CommandHandler("myplan", myplan_command))
    application.add_handler(CommandHandler("add_premium", add_premium_command))
    application.add_handler(CommandHandler("remove_premium", remove_premium_command))
    application.add_handler(CommandHandler("premium_users", premium_users_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    application.add_handler(CallbackQueryHandler(download_callback))
    
    application.add_error_handler(error_handler)
    
    logger.info("Bot started successfully! Press Ctrl+C to stop.")
    print("\n✅ Bot is running!")
    print(f"🌐 Health check: http://0.0.0.0:10000/health")
    print(f"📁 Temp directory: {TEMP_DIR}")
    print(f"🗄️ MongoDB: {'Connected' if db is not None else 'Not connected'}")
    print(f"👥 Admin IDs: {ADMIN_USER_IDS}")
    print(f"⚡ Download Speed: 100Mbps (1MB chunks)")
    print(f"🆓 Free Users: 5 downloads/day (NO trial)")
    print(f"🌍 Timezone: Asia/Kolkata (India/Sri Lanka)")
    print("Press Ctrl+C to stop\n")
    
    # Use drop_pending_updates to ignore any old updates
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


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
