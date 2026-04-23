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

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone("Europe/Moscow")

CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- DB ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            last_download_time TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()

db_init()

def get_setting(key: str):
    conn.commit()
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None

async def is_subscribed(user_id: int):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def parse_dt(dt_str: str):
    return MSK.localize(datetime.strptime(dt_str, "%d.%m.%Y %H:%M"))

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
        (message.from_user.id, message.from_user.username, message.from_user.full_name)
    )
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать", callback_data="check_sub")]
    ])

    await message.answer("Подпишись и нажми скачать", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    await callback.answer()

    user_id = callback.from_user.id

    if not await is_subscribed(user_id):
        await callback.answer("❌ Подпишись и попробуй ещё раз", show_alert=True)
        return

    file_id = get_setting("file_id")

    if not file_id or file_id == "None":
        await callback.answer("❌ Файл не загружен", show_alert=True)
        return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    now_dt = datetime.now(MSK)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")

    if row and row[0]:
        try:
            last_dt = parse_dt(row[0])
            if now_dt - last_dt < timedelta(minutes=15):
                await callback.answer("❌ Подожди 15 минут", show_alert=True)
                return
        except:
            pass

    cursor.execute(
        "UPDATE users SET last_download_time=? WHERE user_id=?",
        (now_str, user_id)
    )
    conn.commit()

    await callback.message.answer_document(file_id)

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Отправь файл")
    await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def admin_file_save(message: types.Message, state: FSMContext):
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("file_id", message.document.file_id)
    )
    conn.commit()
    conn.execute("VACUUM")

    print("FILE SAVED:", message.document.file_id)

    await message.answer("✅ Файл сохранен")
    await state.clear()

async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
