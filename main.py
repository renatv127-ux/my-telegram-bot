
import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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

logging.basicConfig(level=logging.INFO)

# --- ПУТЬ К БАЗЕ (ВАЖНО ДЛЯ RAILWAY) ---
if os.path.exists("/data"):
    DB_PATH = "/data/bot_data.db" # Путь к постоянному диску Railway
else:
    DB_PATH = "bot_data.db" # Локальный путь

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ ---
class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- ФУНКЦИИ БАЗЫ ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        if commit: conn.commit()
        if fetchone: return cursor.fetchone()
        if fetchall: return cursor.fetchall()

def db_init():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                           received_file INTEGER DEFAULT 0, date_received TEXT,
                           last_download_time TEXT, last_msg_id INTEGER)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                          (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                          (key TEXT PRIMARY KEY, value TEXT)''')
        for k, v in [('downloads', '0'), ('bot_status', 'on'), ('file_id', '')]:
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        
        # Проверка структуры (добавление колонки если нет)
        cursor.execute("PRAGMA table_info(users)")
        if "last_msg_id" not in [c[1] for c in cursor.fetchall()]:
            cursor.execute("ALTER TABLE users ADD COLUMN last_msg_id INTEGER")
        conn.commit()

async def is_subscribed(user_id):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status in ["member", "administrator", "creator"]
    except: return False

def get_setting(key):
    res = db_query("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return res[0] if res else ""

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    u_id = message.from_user.id
    if get_setting("bot_status") == "off" and u_id != ADMIN_ID:
        return await message.answer("❌ Бот временно отключен.")
    
    db_query("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
             (u_id, message.from_user.username, message.from_user.full_name), commit=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    if not await is_subscribed(callback.from_user.id):
        return await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
    
    f_id = get_setting("file_id")
    if not f_id or len(f_id) < 5:
        return await callback.answer("❌ Файл еще не загружен админом!", show_alert=True)

    db_query("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'", commit=True)
    
    # Пытаемся отправить как документ
    try:
        await callback.message.answer_document(f_id, caption=f"Ваш драм кит!{CONTACT_INFO}")
        await callback.answer()
    except Exception as e:
        await callback.answer("Ошибка при отправке файла. Админ, перелей файл!", show_alert=True)

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени драм кит (1-5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = callback.data.split("_")[1]
    await state.update_data(rating=rating)
    await callback.message.edit_text(f"Оценка: {rating}/5. Напиши отзыв:")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    db_query("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, message.from_user.username, data['rating'], message.text, dt), commit=True)
    await message.answer("✅ Отзыв сохранен!"); await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    rows = db_query("SELECT username, rating, comment FROM reviews ORDER BY date DESC LIMIT 5", fetchall=True)
    if not rows: return await message.answer("Отзывов пока нет.")
    res = "Последние отзывы:\n\n"
    for r in rows: res += f"👤 @{r[0]} | ⭐ {r[1]}/5\n📝 {r[2]}\n\n"
    await message.answer(res)

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    f_id = get_setting("file_id")
    f_st = "✅ ОК" if len(f_id) > 5 else "❌ НЕТ"
    b_st = get_setting("bot_status")
    
    text = (f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\nФайл: {f_st}\nБот: {b_st}\n\n"
            f"/FileDK — Загрузить файл\n/on | /off — Состояние\n"
            f"/Stata — Статистика\n/sms Текст — Рассылка")
    await message.answer(text)

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.answer("📁 Отправь ЛЮБОЙ файл (ЗИП, Аудио, Документ).")
        await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    # Ищем file_id в любом типе контента
    file_obj = message.document or message.audio or message.video or (message.photo[-1] if message.photo else None)
    
    if file_obj:
        db_query("UPDATE settings SET value=? WHERE key='file_id'", (file_obj.file_id,), commit=True)
        await message.answer(f"✅ Файл сохранен!\nID: <code>{file_obj.file_id}</code>")
        await state.clear()
    else:
        await message.answer("❌ Это не файл. Пришли файл.")

@dp.message(Command("on"))
async def bot_on(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        db_query("UPDATE settings SET value='on' WHERE key='bot_status'", commit=True)
        await message.answer("✅ Бот включен.")

@dp.message(Command("off"))
async def bot_off(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        db_query("UPDATE settings SET value='off' WHERE key='bot_status'", commit=True)
        await message.answer("❌ Бот выключен.")

@dp.message(Command("Stata"))
async def admin_stata(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    dl = get_setting("downloads")
    await message.answer(f"📊 Всего юзеров: {users}\n📈 Скачиваний: {dl}")

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/sms", "").strip()
    if not text: return await message.answer("Пример: /sms Всем привет")
    users = db_query("SELECT user_id FROM users", fetchall=True)
    count = 0
    for u in users:
        try:
            await bot.send_message(u[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Рассылка завершена: {count} чел.")

async def main():
    db_init()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
