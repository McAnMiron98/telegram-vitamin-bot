import os
import json
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackQueryHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

moscow_tz = ZoneInfo("Europe/Moscow")

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

SAVE_FILE = "reminders.json"

reminders = {}  # {(chat_id, name): {'time_str': 'HH:MM', 'accepted': False}}
user_states = {}

scheduler = BackgroundScheduler()
scheduler.start()

bot_instance = None
event_loop = None


def save_reminders():
    """Сохраняем reminders в JSON файл."""
    to_save = []
    for (chat_id, name), data in reminders.items():
        to_save.append({
            "chat_id": chat_id,
            "name": name,
            "time_str": data["time_str"],
            "accepted": data["accepted"]
        })
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)


def load_reminders():
    """Загружаем reminders из JSON и восстанавливаем задачи планировщика."""
    if not os.path.exists(SAVE_FILE):
        return
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            chat_id = item["chat_id"]
            name = item["name"]
            time_str = item["time_str"]
            accepted = item.get("accepted", False)
            reminders[(chat_id, name)] = {"time_str": time_str, "accepted": accepted}
            # Восстанавливаем задачу в планировщике
            hour, minute = map(int, time_str.split(":"))
            now = datetime.now(moscow_tz)
            first_time = datetime.combine(now.date(), time(hour, minute), tzinfo=moscow_tz)
            if first_time < now:
                first_time += timedelta(days=1)
            job_id = f"{chat_id}_{name}"
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
            scheduler.add_job(
                send_reminder_sync,
                trigger=IntervalTrigger(hours=1, start_date=first_time, timezone=moscow_tz),
                id=job_id,
                replace_existing=True,
                args=[chat_id, name]
            )
        logging.info("Напоминания успешно загружены и задачи восстановлены.")
    except Exception as e:
        logging.error(f"Ошибка загрузки напоминаний: {e}")


def main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить", callback_data="add")],
        [InlineKeyboardButton("📋 Список", callback_data="list")],
        [InlineKeyboardButton("🗑 Удалить", callback_data="delete")]
    ]
    return InlineKeyboardMarkup(keyboard)


def reminder_buttons(name):
    keyboard = [
        [
            InlineKeyboardButton("✅ Принял", callback_data=f"accepted|{name}")
        ],
        [
            InlineKeyboardButton("⏰ Отложить 15м", callback_data=f"repeat|{name}|15"),
            InlineKeyboardButton("⏰ Отложить 30м", callback_data=f"repeat|{name}|30"),
            InlineKeyboardButton("⏰ Отложить 1ч", callback_data=f"repeat|{name}|60"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот, который будет напоминать тебе про витамины 💊\n\n"
        "Выбери действие ниже:",
        reply_markup=main_menu()
    )


async def send_reminder_async(chat_id: int, name: str, bot):
    key = (chat_id, name)
    data = reminders.get(key)
    if not data or data['accepted']:
        return False
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Напоминание: пора принять {name}!",
            reply_markup=reminder_buttons(name)
        )
        return True
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания: {e}")
        return False


def send_reminder_sync(chat_id: int, name: str):
    global bot_instance, event_loop
    if bot_instance is None or event_loop is None:
        logging.error("Bot или event loop не инициализированы")
        return
    asyncio.run_coroutine_threadsafe(send_reminder_async(chat_id, name, bot_instance), event_loop)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    data = query.data

    if data == "add":
        user_states[chat_id] = {"action": "add", "step": "waiting_for_name"}
        await query.message.reply_text("Введите название витамина:")
    elif data == "list":
        await list_reminders(update, context, from_callback=True)
    elif data == "delete":
        user_states[chat_id] = {"action": "delete", "step": "waiting_for_name"}
        user_vitamins = [(name, info['time_str']) for (uid, name), info in reminders.items() if uid == chat_id]
        if not user_vitamins:
            await query.message.reply_text("У тебя пока нет напоминаний для удаления.", reply_markup=main_menu())
            user_states.pop(chat_id, None)
            return
        keyboard = []
        for name, time_str in user_vitamins:
            keyboard.append([InlineKeyboardButton(f"{name} в {time_str}", callback_data=f"delvitamin|{name}")])
        keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
        await query.message.reply_text("Выбери витамин для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "cancel":
        user_states.pop(chat_id, None)
        await query.message.reply_text("Отмена.", reply_markup=main_menu())
    elif data.startswith("delvitamin|"):
        name = data.split("|", 1)[1]
        key = (chat_id, name)
        job_id = f"{chat_id}_{name}"
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        if key in reminders:
            del reminders[key]
            save_reminders()  # Сохраняем изменения
            await query.message.reply_text(f"🗑 Напоминание '{name}' удалено.", reply_markup=main_menu())
        else:
            await query.message.reply_text(f"❌ Напоминание '{name}' не найдено.", reply_markup=main_menu())
        user_states.pop(chat_id, None)
    elif data.startswith("accepted|"):
        name = data.split("|", 1)[1]
        key = (chat_id, name)
        if key in reminders:
            time_str = reminders[key]['time_str']
            hour, minute = map(int, time_str.split(":"))
            now = datetime.now(moscow_tz)
            next_time = datetime.combine(now.date(), time(hour, minute), tzinfo=moscow_tz) + timedelta(days=1)
            reminders[key]['accepted'] = False

            job_id = f"{chat_id}_{name}"
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
            scheduler.add_job(
                send_reminder_sync,
                trigger=IntervalTrigger(hours=1, start_date=next_time, timezone=moscow_tz),
                id=job_id,
                replace_existing=True,
                args=[chat_id, name]
            )
            save_reminders()

            await query.message.reply_text(f"✅ Отлично! Я запомнил, что ты принял {name}. Напоминание перенесено на следующий день.", reply_markup=main_menu())
        else:
            await query.message.reply_text(f"❌ Напоминание '{name}' не найдено.", reply_markup=main_menu())
    elif data.startswith("repeat|"):
        parts = data.split("|")
        if len(parts) != 3:
            await query.message.reply_text("❗ Некорректная команда.", reply_markup=main_menu())
            return
        name = parts[1]
        try:
            delay_min = int(parts[2])
        except ValueError:
            await query.message.reply_text("❗ Некорректное время отложить.", reply_markup=main_menu())
            return

        key = (chat_id, name)
        if key not in reminders:
            await query.message.reply_text(f"❌ Напоминание '{name}' не найдено.", reply_markup=main_menu())
            return

        job_id = f"{chat_id}_{name}"
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

        run_time = datetime.now(moscow_tz) + timedelta(minutes=delay_min)
        scheduler.add_job(
            send_reminder_sync,
            trigger=DateTrigger(run_date=run_time),
            id=f"{job_id}_repeat_{run_time.timestamp()}",
            replace_existing=False,
            args=[chat_id, name]
        )

        await query.message.reply_text(f"⏰ Напоминание для {name} отложено на {delay_min} минут.", reply_markup=main_menu())


async def handle_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    state = user_states.get(chat_id)

    # Обработка текста "витамин принял"
    for (uid, name), data in reminders.items():
        if uid == chat_id and text.lower() == f"{name.lower()} принял":
            time_str = data['time_str']
            hour, minute = map(int, time_str.split(":"))
            now = datetime.now(moscow_tz)
            next_time = datetime.combine(now.date(), time(hour, minute), tzinfo=moscow_tz) + timedelta(days=1)
            data['accepted'] = False

            job_id = f"{chat_id}_{name}"
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
            scheduler.add_job(
                send_reminder_sync,
                trigger=IntervalTrigger(hours=1, start_date=next_time, timezone=moscow_tz),
                id=job_id,
                replace_existing=True,
                args=[chat_id, name]
            )
            save_reminders()

            await update.message.reply_text(f"✅ Отлично! Я запомнил, что ты принял {name}. Напоминание перенесено на следующий день.", reply_markup=main_menu())
            return

    if not state:
        return

    if state["action"] == "add":
        if state["step"] == "waiting_for_name":
            state["name"] = text
            state["step"] = "waiting_for_time"
            await update.message.reply_text("Введите время в формате ЧЧ:ММ (по московскому времени):")
        elif state["step"] == "waiting_for_time":
            try:
                name = state["name"]
                time_str = text
                hour, minute = map(int, time_str.split(":"))
                now = datetime.now(moscow_tz)
                first_time = datetime.combine(now.date(), time(hour, minute), tzinfo=moscow_tz)
                if first_time < now:
                    first_time += timedelta(days=1)

                key = (chat_id, name)
                reminders[key] = {'time_str': time_str, 'accepted': False}

                job_id = f"{chat_id}_{name}"
                try:
                    scheduler.remove_job(job_id)
                except Exception:
                    pass

                scheduler.add_job(
                    send_reminder_sync,
                    trigger=IntervalTrigger(hours=1, start_date=first_time, timezone=moscow_tz),
                    id=job_id,
                    replace_existing=True,
                    args=[chat_id, name]
                )

                save_reminders()

                await update.message.reply_text(f"✅ Напоминание для {name} добавлено на {time_str} (МСК).", reply_markup=main_menu())
                del user_states[chat_id]
            except Exception as e:
                await update.message.reply_text(f"❗ Ошибка: {e}")

    elif state["action"] == "delete":
        name = text
        key = (chat_id, name)
        job_id = f"{chat_id}_{name}"

        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

        if key in reminders:
            del reminders[key]
            save_reminders()
            await update.message.reply_text(f"🗑 Напоминание '{name}' удалено.", reply_markup=main_menu())
        else:
            await update.message.reply_text(f"❌ Напоминание '{name}' не найдено.", reply_markup=main_menu())
        del user_states[chat_id]


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    chat_id = update.effective_chat.id
    user_reminders = []
    for (uid, name), data in reminders.items():
        if uid == chat_id:
            status = "✅ Принято" if data['accepted'] else "⏳ Ждёт"
            user_reminders.append(f"{name} в {data['time_str']} - {status}")
    text = "📋 Твои напоминания:\n" + "\n".join(user_reminders) if user_reminders else "❌ У тебя пока нет напоминаний."

    if from_callback:
        await update.callback_query.message.reply_text(text, reply_markup=main_menu())
    else:
        await update.message.reply_text(text, reply_markup=main_menu())


def main():
    global bot_instance, event_loop
    load_reminders()  # Загружаем напоминания перед стартом

    app = ApplicationBuilder().token(TOKEN).build()
    bot_instance = app.bot
    event_loop = asyncio.get_event_loop()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_user_input))

    print("🤖 Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
