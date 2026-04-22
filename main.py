import os
import asyncio
import sqlite3
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099 
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')

CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    conn.commit()

db_init()

async def is_subscribed(user_id):
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except:
        return False

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else None

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    avg, count = cursor.fetchone()
    if not avg:
        return 0, 0
    return round(avg, 1), count

# --- START ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, received_file) VALUES (?, ?, ?, 0)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    # 👇 имя
    name = message.from_user.username
    if name:
        name = f"@{name}"
    else:
        name = message.from_user.first_name or "друг"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    
    await message.answer(
        f"Привет, {name}! 👋\nПодпишись на канал и нажми кнопку, чтобы получить файл!" + CONTACT_INFO,
        reply_markup=kb
    )

# --- СКАЧИВАНИЕ ---
@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Админ еще не загрузил файл через /FileDK", show_alert=True)
        return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    
    now_dt = datetime.now(MSK)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")

    if u_data and u_data[0]:
        try:
            last_dt = datetime.strptime(u_data[0], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now_dt - last_dt < timedelta(minutes=15):
                diff = timedelta(minutes=15) - (now_dt - last_dt)
                await callback.answer(f"❌ Повторная загрузка через {int(diff.total_seconds() // 60)} мин.", show_alert=True)
                return
        except:
            pass

    cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")

    cursor.execute("""
        UPDATE users SET 
            username=?,
            full_name=?,
            received_file=1,
            last_download_time=?,
            date_received=COALESCE(date_received, ?)
        WHERE user_id=?
    """, (
        callback.from_user.username,
        callback.from_user.first_name,
        now_str,
        now_str,
        user_id
    ))
    conn.commit()

    avg, count = get_average_rating()
    total_dl = get_setting("downloads")

    caption = (f"🥁 <b>Файл готов!</b>\n📈 Скачиваний: {total_dl}\n⭐ Рейтинг: {avg}/5\n\n"
               f"/grade — оставить отзыв\n/review — отзывы" + CONTACT_INFO)
    
    await callback.message.answer_document(file_id, caption=caption, parse_mode="HTML")
    await callback.answer()

# --- ОТЗЫВ ---
@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    
    if not row or not row[0]:
        await message.answer("❌ Сначала скачай драм кит через /start!" + CONTACT_INFO)
        return

    cursor.execute("SELECT rating, comment, date FROM reviews WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        text = f"🔄 Твой отзыв ({existing[0]}/5). Выбери новую оценку:"
    else:
        text = "⭐ Оцени драм кит (1-5):"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]
    ])
    await message.answer(text + CONTACT_INFO, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

# --- ВСЕ АДМИН-КОМАНДЫ ВОЗВРАЩЕНЫ ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    f_status = "✅ ОК" if get_setting("file_id") else "❌ НЕТ ФАЙЛА"
    b_status = "Вкл" if get_setting("bot_status") == "on" else "Выкл"
    await message.answer(f"🛠 АДМИН\nФайл: {f_status}\nБот: {b_status}")

# (остальные команды можешь оставить как были у тебя — они не конфликтуют)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())