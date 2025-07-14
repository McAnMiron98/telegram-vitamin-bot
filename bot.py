import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, time, timedelta
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Формат: { (chat_id, name): {'time_str': 'HH:MM', 'accepted': False} }
reminders = {}

scheduler = BackgroundScheduler()
scheduler.start()

bot_instance = None  # Глобальный объект бота
event_loop = None    # Главный asyncio event loop

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот, который будет напоминать тебе про витамины 💊\n\n"
        "📌 Добавить напоминание: /add <название> <время в ЧЧ:ММ>\n"
        "📋 Посмотреть все: /list\n"
        "🗑 Удалить: /delete <название>\n\n"
        "✅ Когда принял, отправь сообщение в формате: <название> принял"
    )

async def send_reminder_async(chat_id: int, name: str, bot):
    key = (chat_id, name)
    data = reminders.get(key)
    if not data:
        return False
    if data['accepted']:
        return False
    try:
        logging.info(f"Отправка напоминания {name} в чат {chat_id} в {datetime.now()}")
        await bot.send_message(chat_id=chat_id, text=f"⏰ Напоминание: пора принять {name}!")
        logging.info(f"Отправлено напоминание: {name} для {chat_id}")
        return True
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания: {e}")
        return False

def send_reminder_sync(chat_id: int, name: str):
    global bot_instance, event_loop
    if bot_instance is None or event_loop is None:
        logging.error("Bot instance или event loop не инициализированы")
        return
    asyncio.run_coroutine_threadsafe(send_reminder_async(chat_id, name, bot_instance), event_loop)

async def add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("❗ Формат: /add <название> <время в ЧЧ:ММ>\nПример: /add Магний 08:30")
        return

    name, time_str = args
    chat_id = update.effective_chat.id

    try:
        hour, minute = map(int, time_str.split(":"))
        now = datetime.now()
        first_time = datetime.combine(now.date(), time(hour, minute))
        if first_time < now:
            first_time += timedelta(days=1)

        key = (chat_id, name)
        reminders[key] = {'time_str': time_str, 'accepted': False}

        job_id = f"{chat_id}_{name}"
        # Удаляем старую задачу, если есть
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

        scheduler.add_job(
            send_reminder_sync,
            trigger=IntervalTrigger(hours=1, start_date=first_time),
            id=job_id,
            replace_existing=True,
            args=[chat_id, name]
        )

        await update.message.reply_text(f"✅ Напоминание для {name} добавлено с {time_str} и будет повторяться каждый час, пока не подтвердите.")
        logging.info(f"Добавлено напоминание: {name} для {chat_id} на {time_str}")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")
        logging.error(f"Ошибка при добавлении напоминания: {e}")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_reminders = []
    for (uid, name), data in reminders.items():
        if uid == chat_id:
            status = "✅ Принято" if data['accepted'] else "⏳ Ждёт"
            user_reminders.append(f"{name} в {data['time_str']} - {status}")
    if user_reminders:
        text = "📋 Твои напоминания:\n" + "\n".join(user_reminders)
    else:
        text = "❌ У тебя пока нет напоминаний."
    await update.message.reply_text(text)

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❗ Формат: /delete <название>")
        return

    name = args[0]
    job_id = f"{chat_id}_{name}"
    key = (chat_id, name)

    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    if key in reminders:
        del reminders[key]
        await update.message.reply_text(f"🗑 Напоминание '{name}' удалено.")
    else:
        await update.message.reply_text(f"❌ Напоминание '{name}' не найдено.")

async def check_acceptance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    chat_id = update.effective_chat.id

    for (uid, name), data in list(reminders.items()):
        if uid == chat_id:
            accept_str = f"{name.lower()} принял"
            if text == accept_str:
                reminders[(chat_id, name)]['accepted'] = True
                job_id = f"{chat_id}_{name}"
                try:
                    scheduler.remove_job(job_id)
                except Exception:
                    pass
                await update.message.reply_text(f"✅ Отлично! Я запомнил, что ты принял {name}. Напоминания остановлены.")
                logging.info(f"{chat_id} подтвердил приём {name}")
                break

def main():
    global bot_instance, event_loop

    app = ApplicationBuilder().token(TOKEN).build()
    bot_instance = app.bot
    event_loop = asyncio.get_event_loop()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_reminder))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("delete", delete_reminder))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_acceptance))

    print("🤖 Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
