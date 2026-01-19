import logging
import io
import requests
import base64
import aiosqlite
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Загрузка переменных окружения
load_dotenv()

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
API_URL = "https://Lukpan-Deep-Fake-Finder.hf.space"
DB_NAME = "bot_data.db"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
            (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
             checks_count INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS global_stats 
            (name TEXT PRIMARY KEY, value INTEGER DEFAULT 0)''')
        
        await db.execute("INSERT OR IGNORE INTO global_stats (name, value) VALUES ('total_checks', 0)")
        await db.execute("INSERT OR IGNORE INTO global_stats (name, value) VALUES ('total_users', 0)")
        
        await db.commit()
        logger.info("✅ Database initialized")

async def update_user_stats(user_id: int, username: str = None, first_name: str = None):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            user_exists = await cursor.fetchone()
            
            if not user_exists:
                await db.execute(
                    "INSERT INTO users (id, username, first_name, checks_count) VALUES (?, ?, ?, 1)",
                    (user_id, username, first_name)
                )
                await db.execute(
                    "UPDATE global_stats SET value = value + 1 WHERE name = 'total_users'"
                )
            else:
                await db.execute(
                    "UPDATE users SET checks_count = checks_count + 1 WHERE id = ?",
                    (user_id,)
                )
            
            await db.execute(
                "UPDATE global_stats SET value = value + 1 WHERE name = 'total_checks'"
            )
            
            await db.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")

async def get_stats(user_id: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT checks_count FROM users WHERE id = ?", (user_id,))
            user_row = await cursor.fetchone()
            user_checks = user_row[0] if user_row else 0
            
            cursor = await db.execute("SELECT value FROM global_stats WHERE name = 'total_checks'")
            global_row = await cursor.fetchone()
            total_checks = global_row[0] if global_row else 0
            
            cursor = await db.execute("SELECT value FROM global_stats WHERE name = 'total_users'")
            users_row = await cursor.fetchone()
            total_users = users_row[0] if users_row else 0
            
            return user_checks, total_checks, total_users
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return 0, 0, 0

# --- КОМАНДЫ БОТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 Проверить текст"), KeyboardButton("👤 Мой профиль")],
        [KeyboardButton("📊 Глобальная стата")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    user = update.effective_user
    await update_user_stats(user.id, user.username, user.first_name)
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я AI Detector Bot\n\n"
        "📸 Что я умею:\n"
        "• Анализировать фото на ИИ\n"
        "• Проверять текст на авторство ИИ\n\n"
        "⚡ Просто отправь мне:\n"
        "• Фото для анализа\n"
        "• Текст для проверки",
        reply_markup=reply_markup
    )

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_checks, total_checks, total_users = await get_stats(user.id)
    
    await update.message.reply_text(
        f"👤 Ваш профиль\n\n"
        f"🆔 ID: {user.id}\n"
        f"📛 Имя: {user.first_name}\n"
        f"✅ Ваших проверок: {user_checks}\n"
        f"👥 Всего пользователей: {total_users}"
    )

async def show_global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_checks, total_checks, total_users = await get_stats(user.id)
    
    await update.message.reply_text(
        f"📊 Глобальная статистика\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Всего проверок: {total_checks}\n"
        f"🎯 Ваш вклад: {user_checks} проверок"
    )

# --- ОБРАБОТКА ФОТО ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return
    
    user = update.effective_user
    status_msg = await update.message.reply_text("📸 Анализирую фото...")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        files = {'file': ('image.jpg', io.BytesIO(photo_bytes), 'image/jpeg')}
        response = requests.post(f"{API_URL}/upload", files=files, timeout=60)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get("success"):
                await update_user_stats(user.id, user.username, user.first_name)
                
                ai_prob = data.get("ai_probability", 0)
                if ai_prob <= 1.0:
                    ai_prob *= 100
                
                if ai_prob > 50:
                    verdict = "🤖 СКОРЕЕ ВСЕГО ИИ"
                else:
                    verdict = "👤 СКОРЕЕ ВСЕГО НАСТОЯЩЕЕ"
                
                result_text = (
                    f"📊 Результат анализа:\n"
                    f"🤖 Вероятность ИИ: {ai_prob:.1f}%\n"
                    f"🏷 Вердикт: {verdict}"
                )
                
                if data.get("image_base64"):
                    try:
                        final_img = io.BytesIO(base64.b64decode(data["image_base64"]))
                        await update.message.reply_photo(
                            photo=final_img,
                            caption=result_text
                        )
                        await status_msg.delete()
                    except:
                        await status_msg.edit_text(result_text)
                else:
                    await status_msg.edit_text(result_text)
            else:
                await status_msg.edit_text("❌ Ошибка анализа")
        else:
            await status_msg.edit_text(f"❌ Ошибка сервера: {response.status_code}")
            
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

# --- ОБРАБОТКА ТЕКСТА ---
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user = update.effective_user
    
    if user_text in ["👤 Мой профиль", "📊 Глобальная стата", "📝 Проверить текст"]:
        if user_text == "📝 Проверить текст":
            await update.message.reply_text("📝 Отправьте текст для проверки (мин. 10 символов)")
        return
    
    if len(user_text) < 10:
        await update.message.reply_text("❌ Нужно минимум 10 символов")
        return
    
    status_msg = await update.message.reply_text("📝 Анализирую текст...")
    
    try:
        payload = {"text": user_text}
        response = requests.post(f"{API_URL}/detect-text", json=payload, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get("success"):
                await update_user_stats(user.id, user.username, user.first_name)
                
                score = data.get("ai_score", 0)
                label = data.get("label", "Неизвестно")
                
                await status_msg.edit_text(
                    f"📝 Результат анализа:\n\n"
                    f"🏷 Вердикт: {label}\n"
                    f"🤖 Вероятность ИИ: {score}%"
                )
            else:
                await status_msg.edit_text("❌ Ошибка анализа")
        else:
            await status_msg.edit_text(f"❌ Ошибка сервера: {response.status_code}")
            
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

# --- ЗАПУСК БОТА ---
async def post_init(application):
    await init_db()
    logger.info("✅ Database initialized")

def main():
    # Исправленная строка: Application.builder() вместо ApplicationBuilder()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", show_profile))
    app.add_handler(CommandHandler("stats", show_global_stats))
    
    app.add_handler(MessageHandler(filters.Text(["👤 Мой профиль"]), show_profile))
    app.add_handler(MessageHandler(filters.Text(["📊 Глобальная стата"]), show_global_stats))
    app.add_handler(MessageHandler(filters.Text(["📝 Проверить текст"]), 
        lambda u, c: u.message.reply_text("📝 Отправьте текст для проверки!")))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    logger.info("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
