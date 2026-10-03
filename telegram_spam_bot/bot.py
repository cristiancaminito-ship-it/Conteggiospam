from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ( Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters, )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Bot avviato! Sono qui per contare spam e link.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Usa i comandi disponibili per gestire il bot.')

async def check_link_entity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Logica per gestire i link e foto nei messaggi
    pass

def main():
    application = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_link_entity))
    application.add_handler(CommandHandler("limit", limit_command))
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    
if __name__ == "__main__":
    main()
