
import os
import asyncio
import sqlite3
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
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

# ИМЯ ФАЙЛА, КОТОРЫЙ ДОЛЖЕН ЛЕЖАТЬ В ПАПКЕ С БОТОМ
KIT_FILENAME = "Ambient_Drum_Kit.zip" 

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

# --- БАЗА ДАННЫХ ---
if os.path.exists("/data"): 
    DB_FILE = "/data/bot_data.db"
else: 
    DB_FILE = "bot_data.db"

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
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
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('cached_file_id', '')")
    conn.commit()

db_init()

async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return False

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else None

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    res = cursor.fetchone()
    if not res or res[0] is None: return 0, 0
    return round(res[0], 1), res[1]

# --- КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (user_id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать Ambient Drum Kit", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить <b>Ambient Drum Kit by TWIXER</b>!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    # Проверяем, есть ли физический файл в папке
    if not os.path.exists(KIT_FILENAME):
        await callback.answer("❌ Ошибка: Файл не найден на сервере. Обратитесь к админу.", show_alert=True)
        return

    # Проверка Кулдауна 5 мин
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    now_dt = datetime.now(MSK)
    if u_data and u_data[0]:
        try:
            last_dt = datetime.strptime(u_data[0], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now_dt - last_dt < timedelta(minutes=5):
                await callback.answer("⏳ Подождите 5 минут.", show_alert=True)
                return
        except: pass

    await callback.message.edit_text("⏳ Подготовка файла... Подождите пару секунд.")

    async with download_queue:
        now_str = now_dt.strftime("%d.%m.%Y %H:%M")
        
        # Обновляем статистику
        cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        if res and res[0] == 0:
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
        
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (now_str, now_str, user_id))
        conn.commit()

        avg, count = get_average_rating()
        caption = (f"🥁 <b>Ambient Drum Kit by TWIXER</b>\n\n📈 Скачиваний: {get_setting('downloads')}\n⭐ Рейтинг: {avg}/5\n\n/grade — оставить отзыв\n/review — все отзывы{CONTACT_INFO}")
        
        try:
            # Сначала пробуем отправить через кэшированный file_id (это мгновенно)
            cached_id = get_setting("cached_file_id")
            if cached_id:
                try:
                    await callback.message.answer_document(cached_id, caption=caption)
                except:
                    # Если cached_id устарел, отправляем файл заново
                    file = FSInputFile(KIT_FILENAME)
                    msg = await callback.message.answer_document(file, caption=caption)
                    cursor.execute("UPDATE settings SET value=? WHERE key='cached_file_id'", (msg.document.file_id,))
                    conn.commit()
            else:
                # Если кэша нет, загружаем файл
                file = FSInputFile(KIT_FILENAME)
                msg = await callback.message.answer_document(file, caption=caption)
                # Сохраняем ID для следующих юзеров, чтобы не грузить файл каждый раз
                cursor.execute("UPDATE settings SET value=? WHERE key='cached_file_id'", (msg.document.file_id,))
                conn.commit()
                
            await callback.message.delete()
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка при отправке: {e}")

    await callback.answer()

# --- ОТЗЫВЫ ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени драм кит от 1 до 5:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = callback.data.split("_")[1]
    await state.update_data(rating=int(rating))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_comment_text")]])
    await callback.message.edit_text(f"Твоя оценка: {rating}/5!\nНапиши отзыв:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    now_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, now_str))
    conn.commit()
    await message.answer("✅ Отзыв сохранен!")
    await state.clear()

@dp.callback_query(F.data == "skip_comment_text", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    now_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (callback.from_user.id, callback.from_user.username or "User", data['rating'], "Без описания", now_str))
    conn.commit()
    await callback.message.edit_text("✅ Оценка сохранена!")
    await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b>\n\n"
    for r in rows:
        res += f"👤 @{r[0]} | {r[1]}/5\n📝 {r[2]}\n\n"
    await message.answer(res if rows else "Отзывов пока нет.")

# --- АДМИНКА ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    f_exists = "✅ Файл найден" if os.path.exists(KIT_FILENAME) else "❌ ФАЙЛА НЕТ В ПАПКЕ"
    await message.answer(f"🛠 <b>Админка</b>\nСтатус файла на сервере: {f_exists}\n\n/Stata - Статистика\n/sms [текст] - Рассылка")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def admin_stata(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file=1")
    count = cursor.fetchone()[0]
    await message.answer(f"📊 Всего скачало: {count}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
