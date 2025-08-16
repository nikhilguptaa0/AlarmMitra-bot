import logging
import sqlite3
from datetime import datetime, timedelta
import openai
import dateparser
import json
import re
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==== CONFIG ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")
DB_NAME = "ai_reminders.db"

# ==== LOGGING ====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==== DATABASE SETUP ====
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            message TEXT NOT NULL,
            remind_time TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def add_reminder(chat_id, message, remind_time):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO reminders (chat_id, message, remind_time) VALUES (?, ?, ?)", (chat_id, message, remind_time))
    conn.commit()
    conn.close()

def get_due_reminders():
    now = datetime.now()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, chat_id, message FROM reminders WHERE remind_time <= ?", (now.strftime("%Y-%m-%d %H:%M:%S"),))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_reminder(reminder_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

# ==== SMART PARSING ====
def parse_reminder(text):
    """ Parse reminder using GPT (for smart extraction). Fallback to regex or dateparser if GPT fails. """
    try:
        prompt = f"Extract the reminder time and message from this text in JSON:\n{text}\nFormat: {{'time': 'datetime string', 'message': 'text'}}"
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response['choices'][0]['message']['content']
        result = json.loads(content)
        dt = dateparser.parse(result["time"])
        if dt is None:
            raise ValueError("GPT could not parse date")
        return dt, result["message"]
    except Exception:
        # Regex fallback for "in X seconds/minutes/hours"
        match = re.search(r'in (\d+) (second|seconds|minute|minutes|hour|hours)', text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            dt = datetime.now()
            if "second" in unit:
                dt += timedelta(seconds=value)
            elif "minute" in unit:
                dt += timedelta(minutes=value)
            elif "hour" in unit:
                dt += timedelta(hours=value)
            message = text.split("to", 1)[-1].strip() if "to" in text else text
            return dt, message

        # General date fallback
        dt = dateparser.parse(text)
        if dt:
            return dt, text

        return None, None

# ==== BOT HANDLERS ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm your *AI Reminder Bot* 🤖\n\n"
        "You can remind me about anything:\n"
        "➡️ /remind me in 10 seconds to drink water\n"
        "➡️ Remind me tomorrow at 5 PM to call mom\n\n"
        "I’ll make sure you don’t forget!",
        parse_mode="Markdown",
    )

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    await handle_reminder_request(update, context, text)

async def handle_text_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    # Try parsing only if it looks like a reminder
    if "remind" in text.lower():
        await handle_reminder_request(update, context, text)

async def handle_reminder_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    dt, message = parse_reminder(text)
    if dt is None or message is None:
        await update.message.reply_text(
            "⚠️ Sorry, I couldn't understand the time.\n"
            "Try phrases like:\n"
            "in 10 seconds, tomorrow 5 PM, next Monday 9 AM",
            parse_mode="Markdown",
        )
        return

    add_reminder(str(update.effective_chat.id), message, dt.strftime("%Y-%m-%d %H:%M:%S"))
    await update.message.reply_text(
        f"✅ Reminder set for *{dt.strftime('%Y-%m-%d %I:%M %p')}* - {message}",
        parse_mode="Markdown",
    )

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    reminders = get_due_reminders()
    for r_id, chat_id, message in reminders:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔔 *Reminder:*\n👉 {message}",
            parse_mode="Markdown",
        )
        delete_reminder(r_id)

# ==== MAIN ====
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_reminder))

    # Job Queue to check reminders every 5 seconds
    app.job_queue.run_repeating(check_reminders, interval=5, first=0)

    logger.info("🚀 AI Reminder Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
