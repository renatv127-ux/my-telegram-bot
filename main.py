
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

# --- ПУТЬ К БАЗЕ (RAILWAY VOLUME) ---
if os.path.exists("/data"):
    DB_PATH = "/data/bot_data.db"
else:
    DB_PATH = "bot_data.db"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ ---
class AdminStates(StatesGroup):
    waiting_for_file = State()

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

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
                           received_file INTEGER DEFAULT 0, date_received TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                          (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                          (key TEXT PRIMARY KEY, value TEXT)''')
        for k, v in [('downloads', '0'), ('bot_status', 'on'), ('file_id', '')]:
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()

async def is_subscribed(user_id):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status in ["member", "administrator", "creator"]
    except: return False

def get_setting(key):
    res = db_query("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return res[0] if res else ""

# --- АДМИНСКИЕ КОМАНДЫ ---

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    f_id = get_setting("file_id")
    f_st = "✅ ОК" if len(f_id) > 5 else "❌ НЕТ"
    b_st = get_setting("bot_status")
    text = (f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\nФайл: {f_st}\nБот: {b_st}\n\n"
            f"/FileDK — Загрузить файл\n/Stata — Статистика\n/on | /off — Бот\n/sms Текст — Рассылка")
    await message.answer(text)

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("📁 Отправь файл (ZIP, документ или аудио).")
    await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    # Ищем file_id во всех возможных типах сообщений
    file_obj = message.document or message.audio or message.video or (message.photo[-1] if message.photo else None)
    
    if file_obj:
        db_query("UPDATE settings SET value=? WHERE key='file_id'", (file_obj.file_id,), commit=True)
        await message.answer(f"✅ <b>Файл сохранен!</b>\nID: <code>{file_obj.file_id}</code>")
        await state.clear()
    else:
        await message.answer("❌ Файл не обнаружен. Попробуй отправить еще раз.")

@dp.message(Command("Stata"))
async def admin_stata(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    u_count = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    dl_count = get_setting("downloads")
    await message.answer(f"📊 Юзеров в базе: {u_count}\n📈 Скачиваний: {dl_count}")

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    users = db_query("SELECT user_id FROM users", fetchall=True)
    c = 0
    for u in users:
        try:
            await bot.send_message(u[0], txt)
            c += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Рассылка: {c} чел.")

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

@dp.message(Command("myid"))
async def get_my_id(message: types.Message):
    await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>")

# --- ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Бот временно отключен.")
    
    db_query("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
             (message.from_user.id, message.from_user.username, message.from_user.full_name), commit=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и получи драм кит!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    if not await is_subscribed(callback.from_user.id):
        return await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
    
    f_id = get_setting("file_id")
    if not f_id or len(f_id) < 5:
        return await callback.answer("❌ Файл не загружен админом!", show_alert=True)

    db_query("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'", commit=True)
    await callback.message.answer_document(f_id, caption=f"Твой драм кит!{CONTACT_INFO}")
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени драм кит от 1 до 5:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    r = callback.data.split("_")[1]
    await state.update_data(rating=r)
    await callback.message.edit_text(f"Оценка {r}/5. Напиши короткий отзыв:")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_review(message: types.Message, state: FSMContext):
    data = await state.get_data()
    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    db_query("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, message.from_user.username, data['rating'], message.text, dt), commit=True)
    await message.answer("✅ Отзыв принят!")
    await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    rows = db_query("SELECT username, rating, comment FROM reviews ORDER BY date DESC LIMIT 5", fetchall=True)
    if not rows: return await message.answer("Отзывов пока нет.")
    res = "Последние отзывы:\n\n"
    for r in rows: res += f"👤 @{r[0]} | ⭐ {r[1]}/5\n📝 {r[2]}\n\n"
    await message.answer(res)

async def main():
    db_init()
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
