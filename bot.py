import os
import sys
import logging
import asyncio
import pytz
import secrets
import string
from datetime import datetime, timedelta
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

# YouTube download libraries
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC
import requests

# Flask for health checks
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

# Silence logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)
# Enable yt-dlp logging for debugging
logging.getLogger('yt_dlp').setLevel(logging.INFO)

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://Veggo:zero8907@cluster0.o8sxezg.mongodb.net/?appName=Cluster0")
ADMIN_USER_IDS = [int(id.strip()) for id in os.environ.get("ADMIN_USER_IDS", "").split(",") if id.strip()]

# URL Shortener
SHORTENER_API = "9a4803974a9dc9c639002d42c5a67f7c18961c0e"
SHORTENER_DOMAIN = "https://adfly.site/api"

# Channels
FORCE_SUB_CHANNELS = [
    {"username": "zerodev2", "url": "https://t.me/zerodev2"},
    {"username": "mvxyoffcail", "url": "https://t.me/mvxyoffcail"}
]

# Images
WELCOME_IMAGE = "https://api.aniwallpaper.workers.dev/random?type=music"
FORCE_SUB_IMAGE = "https://i.ibb.co/pr2H8cwT/img-8312532076.jpg"

# Temp directory
TEMP_DIR = "/tmp/music_bot_temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# MongoDB
try:
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client['music_bot']
    users_collection = db['users']
    downloads_collection = db['downloads']
    verification_collection = db['verifications']
    verification_tokens_collection = db['verification_tokens']
    logger.info("✅ MongoDB connected")
except ConnectionFailure as e:
    logger.error(f"❌ MongoDB failed: {e}")
    db = None

# Flask
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return {"status": "ok", "bot": "running"}, 200

def run_flask():
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)


def generate_random_token(length=32):
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def get_greeting():
    hour = datetime.now(pytz.timezone('Asia/Kolkata')).hour
    if 5 <= hour < 12:
        return "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞"
    elif 12 <= hour < 17:
        return "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ ☀️"
    elif 17 <= hour < 21:
        return "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌆"
    else:
        return "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌙"


def generate_verification_link(user_id, context):
    try:
        if db is None:
            return None
        
        token = generate_random_token(32)
        
        verification_tokens_collection.insert_one({
            'token': token,
            'user_id': user_id,
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(hours=24),
            'used': False
        })
        
        bot_username = context.bot.username
        callback_url = f"https://t.me/{bot_username}?start=verify_{token}"
        
        try:
            api_url = f"{SHORTENER_DOMAIN}?api={SHORTENER_API}&url={callback_url}&format=text"
            response = requests.get(api_url, timeout=15)
            
            if response.status_code == 200:
                short_url = response.text.strip()
                if short_url and short_url.startswith('http'):
                    return short_url
            verification_tokens_collection.delete_one({'token': token})
            return None
        except:
            verification_tokens_collection.delete_one({'token': token})
            return None
    except Exception as e:
        logger.error(f"Verification link error: {e}")
        return None


async def get_verification_credits(user_id):
    if db is None:
        return 0
    
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        total_verifications = verification_collection.count_documents({
            'user_id': user_id,
            'verified_at': {'$gte': today}
        })
        
        downloads_today = downloads_collection.count_documents({
            'user_id': user_id,
            'downloaded_at': {'$gte': today}
        })
        
        total_credits = 5 + (total_verifications * 5)
        return max(0, total_credits - downloads_today)
    except:
        return 0


async def mark_user_verified(user_id):
    if db is None:
        return False
    
    try:
        verification_collection.insert_one({
            'user_id': user_id,
            'verified_at': datetime.now()
        })
        return True
    except:
        return False


async def get_seconds(time_str):
    try:
        parts = time_str.split()
        if len(parts) != 2:
            return 0
        
        value = int(parts[0])
        unit = parts[1].lower()
        
        time_map = {
            'minute': 60, 'minutes': 60, 'min': 60, 'mins': 60,
            'hour': 3600, 'hours': 3600, 'hr': 3600, 'hrs': 3600,
            'day': 86400, 'days': 86400,
            'week': 604800, 'weeks': 604800,
            'month': 2592000, 'months': 2592000,
            'year': 31536000, 'years': 31536000
        }
        
        return value * time_map.get(unit, 0)
    except:
        return 0


async def check_user_subscription(user_id, context):
    for channel in FORCE_SUB_CHANNELS:
        try:
            member = await context.bot.get_chat_member(
                chat_id=f"@{channel['username']}", 
                user_id=user_id
            )
            if member.status in ['left', 'kicked']:
                return False
        except:
            return False
    return True


async def is_premium_user(user_id):
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
                users_collection.update_one(
                    {'user_id': user_id},
                    {'$unset': {'expiry_time': ""}}
                )
        return False
    except:
        return False


async def check_download_limit(user_id):
    if db is None:
        return True
    
    if await is_premium_user(user_id):
        return True
    
    credits = await get_verification_credits(user_id)
    return credits > 0


async def get_remaining_downloads(user_id):
    if db is None:
        return "N/A"
    
    if await is_premium_user(user_id):
        return "Unlimited ⭐"
    
    try:
        credits = await get_verification_credits(user_id)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        total_verifications = verification_collection.count_documents({
            'user_id': user_id,
            'verified_at': {'$gte': today}
        })
        
        total_credits = 5 + (total_verifications * 5)
        return f"{credits}/{total_credits}"
    except:
        return "5/5"


def save_user(user_id, username, first_name):
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
    except:
        pass


def log_download(user_id, video_id, title):
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
    except:
        pass


def search_youtube(query, limit=10):
    """Search YouTube for music videos."""
    try:
        logger.info(f"Searching YouTube for: {query}")
        
        ydl_opts = {
            'quiet': False,  # Enable output for debugging
            'no_warnings': False,
            'extract_flat': True,
            'skip_download': True,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'default_search': 'ytsearch',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Try with ytsearch prefix
                search_url = f"ytsearch{limit}:{query}"
                logger.info(f"Search URL: {search_url}")
                
                search_results = ydl.extract_info(search_url, download=False)
                
                logger.info(f"Search results type: {type(search_results)}")
                if search_results:
                    logger.info(f"Search results keys: {search_results.keys() if isinstance(search_results, dict) else 'Not a dict'}")
                
                results = []
                if search_results and 'entries' in search_results:
                    logger.info(f"Found {len(search_results['entries'])} entries")
                    
                    for idx, entry in enumerate(search_results['entries']):
                        if entry:
                            duration_seconds = entry.get('duration', 0)
                            minutes = duration_seconds // 60 if duration_seconds else 0
                            seconds = duration_seconds % 60 if duration_seconds else 0
                            duration = f"{minutes}:{seconds:02d}" if duration_seconds else "N/A"
                            
                            video_id = entry.get('id')
                            title = entry.get('title', 'Unknown')
                            
                            logger.info(f"Entry {idx}: {video_id} - {title}")
                            
                            results.append({
                                'video_id': video_id,
                                'title': title,
                                'duration': duration,
                                'channel': entry.get('channel', entry.get('uploader', 'Unknown'))
                            })
                else:
                    logger.warning(f"No entries found in search results")
                
                logger.info(f"Returning {len(results)} results")
                return results
                
            except Exception as inner_e:
                logger.error(f"Inner search error: {inner_e}", exc_info=True)
                return []
                
    except Exception as e:
        logger.error(f"YouTube search error: {e}", exc_info=True)
        return []


def download_youtube_audio(video_id, output_path):
    """Download YouTube audio as MP3 with metadata."""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"Downloading audio from: {url}")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': False,
            'no_warnings': False,
            'writethumbnail': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("Extracting video info...")
            info = ydl.extract_info(url, download=True)
            
            title = info.get('title', 'Unknown')
            artist = info.get('artist') or info.get('uploader', 'Unknown')
            album = info.get('album', 'YouTube')
            
            logger.info(f"Downloaded: {title}")
            
            mp3_path = output_path + '.mp3'
            thumb_path = output_path + '.jpg'
            
            # Add metadata
            if os.path.exists(mp3_path):
                logger.info("Adding metadata to MP3...")
                try:
                    audio = MP3(mp3_path, ID3=ID3)
                    
                    try:
                        audio.add_tags()
                    except:
                        pass
                    
                    audio.tags['TIT2'] = TIT2(encoding=3, text=title)
                    audio.tags['TPE1'] = TPE1(encoding=3, text=artist)
                    audio.tags['TALB'] = TALB(encoding=3, text=album)
                    
                    # Add cover art
                    if os.path.exists(thumb_path):
                        logger.info("Adding album art...")
                        with open(thumb_path, 'rb') as img_file:
                            audio.tags['APIC'] = APIC(
                                encoding=3,
                                mime='image/jpeg',
                                type=3,
                                desc='Cover',
                                data=img_file.read()
                            )
                    
                    audio.save()
                    logger.info("Metadata saved successfully")
                except Exception as e:
                    logger.warning(f"Metadata error: {e}")
            else:
                logger.error(f"MP3 file not found at: {mp3_path}")
                return None
            
            return {
                'mp3_path': mp3_path if os.path.exists(mp3_path) else None,
                'thumb_path': thumb_path if os.path.exists(thumb_path) else None,
                'title': title,
                'artist': artist
            }
    
    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler."""
    user = update.effective_user
    
    # Verification callback
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith('verify_'):
            token = arg.replace('verify_', '')
            
            if db is None:
                await update.message.reply_text("❌ Database error.")
                return
            
            token_data = verification_tokens_collection.find_one({'token': token, 'used': False})
            
            if not token_data:
                await update.message.reply_text(
                    "❌ *Invalid or Expired Link!*\n\nUse /verify for new link.",
                    parse_mode='Markdown'
                )
                return
            
            if token_data['expires_at'] < datetime.now():
                await update.message.reply_text(
                    "❌ *Link Expired!*\n\nUse /verify for new link.",
                    parse_mode='Markdown'
                )
                return
            
            if token_data['user_id'] != user.id:
                await update.message.reply_text(
                    "❌ *Wrong User!*\n\nUse /verify for your link.",
                    parse_mode='Markdown'
                )
                return
            
            verification_tokens_collection.update_one(
                {'token': token},
                {'$set': {'used': True, 'used_at': datetime.now()}}
            )
            
            success = await mark_user_verified(user.id)
            
            if success:
                credits = await get_verification_credits(user.id)
                await update.message.reply_text(
                    "✅ *Verified!*\n\n"
                    f"🎉 +5 downloads earned!\n"
                    f"📊 Remaining: {credits}\n\n"
                    "Send a song name to download! 🎵",
                    parse_mode='Markdown'
                )
            return
    
    save_user(user.id, user.username, user.first_name)
    has_premium = await is_premium_user(user.id)
    
    if not has_premium:
        is_subscribed = await check_user_subscription(user.id, context)
        
        if not is_subscribed:
            keyboard = []
            for channel in FORCE_SUB_CHANNELS:
                keyboard.append([
                    InlineKeyboardButton(f"📢 Join {channel['username']}", url=channel['url'])
                ])
            keyboard.append([
                InlineKeyboardButton("✅ I Joined", callback_data="check_subscription")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await update.message.reply_photo(
                    photo=FORCE_SUB_IMAGE,
                    caption=f"👋 Hello {user.first_name}!\n\n⚠️ Join all channels first:",
                    reply_markup=reply_markup
                )
            except:
                await update.message.reply_text(
                    f"👋 Hello {user.first_name}!\n\n⚠️ Join all channels first:",
                    reply_markup=reply_markup
                )
            return
    
    # Send sticker
    try:
        sticker_msg = await update.message.reply_sticker(
            sticker="CAACAgIAAxkBAAEQYt1pfZPhPjP99PZfe3GQoyoKNlrStgACBT0AAiUmaUjLrgS38Ul59jgE"
        )
        await asyncio.sleep(2)
        await sticker_msg.delete()
    except:
        pass
    
    greeting = get_greeting()
    remaining = await get_remaining_downloads(user.id)
    
    welcome_text = (
        f"🎵 ʜᴇʏ {user.first_name}, {greeting}\n\n"
        "I ᴄᴀɴ ᴅᴏᴡɴʟᴏᴀᴅ ᴍᴜsɪᴄ ғʀᴏᴍ YᴏᴜTᴜʙᴇ! 🎧\n\n"
        "Just send a song name 🎶\n\n"
    )
    
    if has_premium:
        welcome_text += "⭐ *Premium* - Unlimited downloads!\n\n"
    else:
        welcome_text += f"🆓 *Free* - {remaining} downloads remaining\n\n"
    
    welcome_text += (
        "💡 *Examples:*\n"
        "`Stardust zayn`\n"
        "`Perfect Ed Sheeran`\n\n"
        "/help for commands"
    )
    
    try:
        await update.message.reply_photo(
            photo=WELCOME_IMAGE,
            caption=welcome_text,
            parse_mode='Markdown'
        )
    except:
        await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify command to earn downloads."""
    user_id = update.effective_user.id
    
    if await is_premium_user(user_id):
        await update.message.reply_text("⭐ Premium users don't need to verify!")
        return
    
    credits = await get_verification_credits(user_id)
    status_msg = await update.message.reply_text("🔗 Generating link...")
    
    verify_link = generate_verification_link(user_id, context)
    
    if not verify_link:
        await status_msg.edit_text("❌ Error generating link. Try later.")
        return
    
    keyboard = [[InlineKeyboardButton("🔗 Verify & Earn +5", url=verify_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await status_msg.edit_text(
        "💎 *Earn Downloads!*\n\n"
        f"📊 Current: {credits}\n\n"
        "Click to verify and get +5 downloads!\n\n"
        "⏰ Expires in 24 hours",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check subscription callback."""
    query = update.callback_query
    await query.answer()
    
    is_subscribed = await check_user_subscription(query.from_user.id, context)
    
    if not is_subscribed:
        await query.answer("❌ Join all channels first!", show_alert=True)
        return
    
    greeting = get_greeting()
    remaining = await get_remaining_downloads(query.from_user.id)
    
    welcome_text = (
        f"🎵 ʜᴇʏ {query.from_user.first_name}, {greeting}\n\n"
        "Send a song name to download! 🎶\n\n"
        f"🆓 {remaining} downloads remaining\n\n"
        "/help for commands"
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
    except:
        await query.message.reply_text(welcome_text, parse_mode='Markdown')


async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search music on YouTube."""
    user_id = update.effective_user.id
    
    has_premium = await is_premium_user(user_id)
    if not has_premium:
        if not await check_user_subscription(user_id, context):
            await update.message.reply_text("⚠️ Join channels first! /start")
            return
    
    query = update.message.text.strip()
    save_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    if not query:
        return
    
    await update.message.chat.send_action(ChatAction.TYPING)
    searching_msg = await update.message.reply_text(f"🔍 Searching '*{query}*'...", parse_mode='Markdown')
    
    try:
        results = search_youtube(query, limit=10)
        
        if not results:
            await searching_msg.edit_text(f"❌ No results for '*{query}*'", parse_mode='Markdown')
            return
        
        # 2-column layout
        keyboard = []
        row = []
        for idx, result in enumerate(results[:10], 1):
            video_id = result.get("video_id")
            title = result.get("title", "Unknown")
            
            display_title = title[:22] + "..." if len(title) > 22 else title
            
            button = InlineKeyboardButton(
                f"🎵 {display_title}",
                callback_data=f"dl_{video_id}"
            )
            
            row.append(button)
            
            if len(row) == 2 or idx == len(results):
                keyboard.append(row)
                row = []
        
        keyboard.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await searching_msg.edit_text(
            f"🎵 *Found {len(results)} results*\n\n👇 Click to download:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        await searching_msg.edit_text("❌ Search failed. Try again.")


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download callback handler."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        try:
            await query.message.delete()
        except:
            pass
        return
    
    video_id = query.data.replace("dl_", "")
    user_id = query.from_user.id
    
    # Check limit
    can_download = await check_download_limit(user_id)
    if not can_download:
        keyboard = [
            [InlineKeyboardButton("💎 Verify", callback_data="verify_now")],
            [InlineKeyboardButton("💬 Premium", url="https://t.me/Venuboyy")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "⚠️ *Limit Reached!*\n\n"
            "💎 Verify to earn +5 downloads\n"
            "⭐ Or get Premium",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    download_msg = await query.message.reply_text("⬇️ Downloading...")
    
    try:
        output_path = os.path.join(TEMP_DIR, f"{video_id}")
        result = download_youtube_audio(video_id, output_path)
        
        if not result or not result['mp3_path']:
            await download_msg.edit_text("❌ Download failed.")
            return
        
        mp3_path = result['mp3_path']
        thumb_path = result['thumb_path']
        title = result['title']
        
        # Check size
        file_size = os.path.getsize(mp3_path) / (1024 * 1024)
        if file_size > 50:
            await download_msg.edit_text(f"❌ Too large ({file_size:.1f}MB)")
            os.remove(mp3_path)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
            return
        
        await download_msg.edit_text(f"📤 Uploading *{title}*...", parse_mode='Markdown')
        
        # Send audio
        with open(mp3_path, 'rb') as audio_file:
            if thumb_path and os.path.exists(thumb_path):
                with open(thumb_path, 'rb') as thumb_file:
                    await query.message.reply_audio(
                        audio=audio_file,
                        thumbnail=thumb_file,
                        title=title,
                        performer=result.get('artist', 'YouTube'),
                        caption=f"🎵 {title}",
                        write_timeout=300,
                        read_timeout=300
                    )
            else:
                await query.message.reply_audio(
                    audio=audio_file,
                    title=title,
                    performer=result.get('artist', 'YouTube'),
                    caption=f"🎵 {title}",
                    write_timeout=300,
                    read_timeout=300
                )
        
        log_download(user_id, video_id, title)
        
        # Cleanup
        try:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
        except:
            pass
        
        try:
            await download_msg.delete()
            await query.message.delete()
        except:
            pass
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        await download_msg.edit_text(f"❌ Error: {str(e)[:100]}")


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify button callback."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    status_msg = await query.message.reply_text("🔗 Generating...")
    
    verify_link = generate_verification_link(user_id, context)
    
    if not verify_link:
        await status_msg.edit_text("❌ Error.")
        return
    
    keyboard = [[InlineKeyboardButton("🔗 Verify", url=verify_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await status_msg.edit_text(
        "💎 *Earn +5 Downloads!*\n\nClick to verify!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    await update.message.reply_text(
        "🎵 *Music Bot Help*\n\n"
        "📖 *Usage:*\n"
        "1. Send song name\n"
        "2. I search YouTube\n"
        "3. Click to download\n\n"
        "⚙️ *Commands:*\n"
        "/start - Start\n"
        "/help - Help\n"
        "/verify - Earn downloads\n\n"
        "⚠️ *Limits:*\n"
        "🆓 5/day + verifications\n"
        "⭐ Premium: Unlimited\n"
        "📦 Max: 50MB",
        parse_mode='Markdown'
    )


async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add premium (Admin)."""
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /add_premium user_id time\nExample: /add_premium 123456 1 month")
        return
    
    try:
        target_user_id = int(context.args[0])
        time_str = " ".join(context.args[1:])
        seconds = await get_seconds(time_str)
        
        if seconds <= 0:
            await update.message.reply_text("Invalid time!")
            return
        
        expiry_time = datetime.now() + timedelta(seconds=seconds)
        
        users_collection.update_one(
            {'user_id': target_user_id},
            {'$set': {'expiry_time': expiry_time}},
            upsert=True
        )
        
        await update.message.reply_text(f"✅ Premium added!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Error handler."""
    logger.error(f"Error: {context.error}")


async def post_init(application: Application):
    """Post init."""
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Ready")
    except Exception as e:
        logger.warning(f"Cleanup: {e}")


def main():
    """Main function."""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set!")
        sys.exit(1)
    
    logger.info("Starting health check...")
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logger.info("Creating bot...")
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .build()
    )
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("verify", verify_command))
    application.add_handler(CommandHandler("add_premium", add_premium_command))
    application.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify_now$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    application.add_handler(CallbackQueryHandler(download_callback))
    application.add_error_handler(error_handler)
    
    print("\n✅ Music Bot Running!")
    print(f"🗄️ MongoDB: {'✅' if db is not None else '❌'}")
    print("Press Ctrl+C to stop\n")
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Stopped!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
