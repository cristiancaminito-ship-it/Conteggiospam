from __future__ import annotations
import logging
import os
import sqlite3
import re
from datetime import time, date
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_limits (
            chat_id INTEGER PRIMARY KEY,
            limit_count INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_counts (
            chat_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            date TEXT,
            PRIMARY KEY (chat_id, user_id, date)
        )
    """)
    conn.commit()
    conn.close()

def get_limit(chat_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT limit_count FROM group_limits WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 1

def set_limit(chat_id: int, limit: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO group_limits (chat_id, limit_count) VALUES (?, ?)", (chat_id, limit))
    conn.commit()
    conn.close()

def check_and_increment(chat_id: int, user_id: int, date_str: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT count FROM user_counts WHERE chat_id = ? AND user_id = ? AND date = ?
    """, (chat_id, user_id, date_str))
    result = cursor.fetchone()

    limit = get_limit(chat_id)

    if result is None:
        if 1 <= limit:
            cursor.execute("""
                INSERT INTO user_counts (chat_id, user_id, count, date) VALUES (?, ?, 1, ?)
            """, (chat_id, user_id, date_str))
            conn.commit()
            conn.close()
            return True
        else:
            conn.close()
            return False
    else:
        current_count = result[0]
        if current_count < limit:
            cursor.execute("""
                UPDATE user_counts SET count = count + 1 
                WHERE chat_id = ? AND user_id = ? AND date = ?
            """, (chat_id, user_id, date_str))
            conn.commit()
            conn.close()
            return True
        else:
            conn.close()
            return False

def reset_daily_counts(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_counts")
    conn.commit()
    conn.close()
    logger.info("Daily counts reset.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Bot avviato! Sono qui per contare spam e link.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Usa i comandi disponibili per gestire il bot.')

async def limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Specifica un nuovo limite. Esempio: /limit 2')
        return

    try:
        new_limit = int(context.args[0])
        chat_id = update.message.chat_id
        set_limit(chat_id, new_limit)
        await update.message.reply_text(f'Limite per questo gruppo impostato su: {new_limit}')
    except ValueError:
        await update.message.reply_text('Per favore, inserisci un numero valido.')

async def check_link_entity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    has_link = False
    if message.entities:
        for entity in message.entities:
            if entity.type in ['url', 'text_link']:
                has_link = True
                break
    if message.caption_entities:
        for entity in message.caption_entities:
            if entity.type in ['url', 'text_link']:
                has_link = True
                break

    if has_link or message.photo:
        chat_id = message.chat_id
        user_id = message.from_user.id
        date_str = str(date.today())

        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status in ['creator', 'administrator']:
            return

        if not check_and_increment(chat_id, user_id, date_str):
            await message.delete()
            await message.reply_text(f"Limite giornaliero superato per {message.from_user.first_name}. Messaggio eliminato.")

def main():
    init_db()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN non impostato nelle variabili d'ambiente.")

    application = Application.builder().token(token).build()

    application.job_queue.run_daily(reset_daily_counts, time(hour=0, minute=0, second=0))

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("limit", limit_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_link_entity))
    application.add_handler(MessageHandler(filters.PHOTO, check_link_entity))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
