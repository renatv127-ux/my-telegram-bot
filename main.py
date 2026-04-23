
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

# --- ПУТЬ К БАЗЕ (ДЛЯ RAILWAY VOLUME) ---
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

# --- ФУНКЦИИ БАЗЫ ДАННЫХ ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        if commit: conn.commit()
        if fetchone: return cursor.fetchone()
        if fetchall: return cursor.fetchall()
    return None

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
        
        # Начальные настройки
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

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    
    f_id = get_setting("file_id")
    f_status = "✅ ОК" if (f_id and len(f_id) > 5) else "❌ НЕТ ФАЙЛА"
    
    b_status_raw = get_setting("bot_status")
    b_status = "Вкл" if b_status_raw == "on" else "Выкл"
    
    text = (f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
            f"Файл: {f_status}\n"
            f"Бот: {b_status}\n\n"
            f"<b>Команды:</b>\n"
            f"/FileDK — Загрузить файл\n"
            f"/on | /off — Состояние бота\n"
            f"/Stata — Список юзеров\n"
            f"/sms [текст] — Рассылка\n"
            f"/delete_review [ID] — Удалить отзыв\n\n"
            f"Юзер команды: /start, /grade, /review")
    await message.answer(text)

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("📁 Отправь файл (как ДОКУМЕНТ):")
    await state.set_state(AdminStates.waiting_for_file)

# Улучшенный приемник файла (ловит ZIP, документы, аудио и т.д.)
@dp.message(AdminStates.waiting_for_file)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    file_id = None
    if message.document: file_id = message.document.file_id
    elif message.audio: file_id = message.audio.file_id
    elif message.video: file_id = message.video.file_id
    elif message.photo: file_id = message.photo[-1].file_id

    if file_id:
        db_query("UPDATE settings SET value=? WHERE key='file_id'", (file_id,), commit=True)
        await message.answer(f"✅ <b>Файл успешно сохранен в базу!</b>\nID: <code>{file_id}</code>")
        await state.clear()
    else:
        await message.answer("❌ Я не увидел файла. Отправь ZIP-архив или документ.")

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
    rows = db_query("SELECT user_id, username, received_file FROM users", fetchall=True)
    if not rows: return await message.answer("База пользователей пуста.")
    
    text = "📊 <b>Список пользователей:</b>\n\n"
    for r in rows:
        dl_status = "✅ Скачал" if r[2] == 1 else "⏳ Не скачал"
        text += f"ID: <code>{r[0]}</code> | @{r[1]} | {dl_status}\n"
    
    await message.answer(text[:4000])

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/sms", "").strip()
    if not text: return await message.answer("Введите текст после команды. Пример: /sms Привет!")
    
    users = db_query("SELECT user_id FROM users", fetchall=True)
    count = 0
    for u in users:
        try:
            await bot.send_message(u[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Рассылка завершена. Получили {count} чел.")

@dp.message(Command("delete_review"))
async def admin_del_review(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text.split()[1])
        db_query("DELETE FROM reviews WHERE user_id=?", (target_id,), commit=True)
        await message.answer(f"✅ Отзыв пользователя {target_id} удален.")
    except:
        await message.answer("Пример: /delete_review 12345678")

# --- ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    u_id = message.from_user.id
    if get_setting("bot_status") == "off" and u_id != ADMIN_ID:
        return await message.answer("❌ Бот временно отключен администратором.")
    
    db_query("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
             (u_id, message.from_user.username, message.from_user.full_name), commit=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    if not await is_subscribed(callback.from_user.id):
        return await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
    
    f_id = get_setting("file_id")
    if not f_id or len(f_id) < 5:
        return await callback.answer("❌ Файл еще не загружен администратором!", show_alert=True)

    db_query("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'", commit=True)
    db_query("UPDATE users SET received_file=1 WHERE user_id=?", (callback.from_user.id,), commit=True)
    
    await callback.message.answer_document(f_id, caption=f"Твой драм кит готов!{CONTACT_INFO}")
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени драм кит (от 1 до 5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = callback.data.split("_")[1]
    await state.update_data(rating=rating)
    await callback.message.edit_text(f"Твоя оценка: {rating}/5. Напиши теперь свой отзыв:")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_review(message: types.Message, state: FSMContext):
    data = await state.get_data()
    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    db_query("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, message.from_user.username, data['rating'], message.text, dt), commit=True)
    await message.answer(f"✅ Спасибо за отзыв!{CONTACT_INFO}")
    await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    rows = db_query("SELECT username, rating, comment FROM reviews ORDER BY date DESC LIMIT 5", fetchall=True)
    if not rows: return await message.answer("Отзывов пока нет.")
    
    res = "💬 <b>Последние отзывы:</b>\n\n"
    for r in rows:
        res += f"👤 @{r[0]} | ⭐ {r[1]}/5\n📝 {r[2]}\n\n"
    await message.answer(res + CONTACT_INFO)

async def main():
    db_init()
    print("--- БОТ ЗАПУЩЕН ---")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
