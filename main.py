
import os
import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099 
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')
CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ ---
DB_FILE = os.getenv("DB_PATH", "/data/bot_data.db")
db_dir = os.path.dirname(DB_FILE)
if db_dir and not os.path.exists(db_dir):
    try: os.makedirs(db_dir, exist_ok=True)
    except: DB_FILE = "bot_data.db"

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time REAL DEFAULT 0, join_date TEXT)''')
    try: cursor.execute("ALTER TABLE users ADD COLUMN join_date TEXT")
    except: pass
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- НАСТРОЙКА КНОПКИ "МЕНЮ" ---

async def set_main_menu(bot: Bot):
    # Команды для всех пользователей
    user_commands = [
        BotCommand(command="start", description="Получить файл"),
        BotCommand(command="grade", description="Оставить отзыв"),
        BotCommand(command="review", description="Посмотреть отзывы"),
        BotCommand(command="help", description="Помощь")
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())

    # Команды только для АДМИНА
    admin_commands = user_commands + [
        BotCommand(command="admin", description="Панель управления"),
        BotCommand(command="full_stats", description="Подробная статистика"),
        BotCommand(command="set_file", description="Загрузить файл"),
        BotCommand(command="sms", description="Рассылка"),
        BotCommand(command="clear_reviews", description="Очистить отзывы"),
        BotCommand(command="clear_stata", description="Очистить статистику")
    ]
    await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# --- ФУНКЦИИ ---

def get_unique_downloads_count():
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file = 1")
    return cursor.fetchone()[0]

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else ""

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    res = cursor.fetchone()
    if not res or res[0] is None: return 0, 0
    return round(res[0], 1), res[1]

async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return False

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    is_admin = message.from_user.id == ADMIN_ID
    text = "📖 <b>Доступные команды:</b>\n\n/start — Получить файл\n/grade — Оставить отзыв\n/review — Отзывы\n"
    if is_admin:
        text += "\n👑 <b>Админ:</b>\n/admin, /full_stats, /set_file, /sms, /clear_reviews, /clear_stata"
    await message.answer(text)

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен.")
        return
    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, join_date) VALUES (?, ?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name, now_date))
    conn.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать файл", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Подпишись на канал!", show_alert=True)
        return
    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл не загружен.", show_alert=True)
        return
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    current_time = time.time()
    if u_data and u_data[0] and current_time - float(u_data[0]) < 300:
        left = int(300 - (current_time - float(u_data[0])))
        await callback.answer(f"⏳ Ждите {left // 60}м {left % 60}с.", show_alert=True)
        return
    wait_msg = await callback.message.edit_text("⏳ Вы в очереди... Отправка через 4 секунды.")
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (current_time, date_str, user_id))
        conn.commit()
        avg, count = get_average_rating()
        caption = f"🥁 <b>Готово!</b>\n📈 Скачало: {get_unique_downloads_count()}\n⭐ Рейтинг: {avg}/5\n\n/grade — отзыв"
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await wait_msg.delete()
        except: await callback.message.answer("❌ Ошибка отправки.")
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени файл (1-5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = callback.data.split("_")[1]
    await state.update_data(rating=int(rating))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_comment_text")]])
    await callback.message.edit_text(f"Оценка: {rating}/5! Напиши отзыв или пропусти:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, datetime.now(MSK).strftime("%d.%m.%Y %H:%M")))
    conn.commit(); await message.answer("✅ Отзыв сохранен!"); await state.clear()

@dp.callback_query(F.data == "skip_comment_text", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (callback.from_user.id, callback.from_user.username or "User", data['rating'], "Без описания", datetime.now(MSK).strftime("%d.%m.%Y %H:%M")))
    conn.commit(); await callback.message.edit_text("✅ Оценка сохранена!"); await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    res = f"⭐ <b>Рейтинг: {avg}/5</b> (Всего: {count})\n\n"
    for r in cursor.fetchall():
        res += f"👤 @{r[0]} | {r[1]}/5\n📝 {r[2]}\n📅 {r[3]}\n\n"
    await message.answer(res if count > 0 else "Отзывов нет.")

# --- АДМИНКА ---

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats_cmd(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users"); total = cursor.fetchone()[0]
    month = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (month,)); m_count = cursor.fetchone()[0]
    year = (datetime.now(MSK) - timedelta(days=365)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (year,)); y_count = cursor.fetchone()[0]
    
    res = f"📊 <b>Статистика:</b>\nВсего: {total}\nЗа год: {y_count}\nЗа месяц: {m_count}\nСкачиваний: {get_unique_downloads_count()}\n\n<b>Последние скачавшие:</b>\n"
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 10")
    for r in cursor.fetchall():
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"• @{r[1]} | {r[2]} | Саб: {sub}\n"
    await message.answer(res)

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer(f"🛠 <b>Админ-панель</b>\nФайл: {'✅' if len(get_setting('file_id')) > 5 else '❌'}\nБот: {get_setting('bot_status')}")

@dp.message(Command("clear_reviews"), F.from_user.id == ADMIN_ID)
async def clear_revs(message: types.Message):
    cursor.execute("DELETE FROM reviews"); conn.commit(); await message.answer("✅ Отзывы удалены.")

@dp.message(Command("clear_stata"), F.from_user.id == ADMIN_ID)
async def clear_st(message: types.Message):
    cursor.execute("UPDATE users SET received_file=0, last_download_time=0, date_received=NULL"); conn.commit(); await message.answer("✅ Статистика сброшена.")

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_file(message: types.Message, state: FSMContext):
    await message.answer("Отправь файл."); await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def file_up(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit(); await message.answer("✅ Файл сохранен!"); await state.clear()

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); c = 0
    for u in cursor.fetchall():
        try: await bot.send_message(u[0], txt); c += 1; await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Отправлено {c} чел.")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit(); await message.answer("✅ ВКЛ.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit(); await message.answer("❌ ВЫКЛ.")

async def main():
    await set_main_menu(bot) # Устанавливаем кнопку Меню при запуске
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
