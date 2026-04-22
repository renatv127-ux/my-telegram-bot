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

def table_columns(table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}

def ensure_column(table_name: str, column_name: str, column_def: str) -> None:
    if column_name not in table_columns(table_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

def db_init():
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS users
        (user_id INTEGER PRIMARY KEY,
         username TEXT,
         full_name TEXT,
         has_downloaded INTEGER DEFAULT 0,
         date_received TEXT,
         last_download_time TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS reviews
        (user_id INTEGER PRIMARY KEY,
         username TEXT,
         rating INTEGER,
         comment TEXT,
         date TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS settings
        (key TEXT PRIMARY KEY,
         value TEXT)"""
    )

    ensure_column("users", "username", "TEXT")
    ensure_column("users", "full_name", "TEXT")
    ensure_column("users", "has_downloaded", "INTEGER DEFAULT 0")
    ensure_column("users", "date_received", "TEXT")
    ensure_column("users", "last_download_time", "TEXT")

    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    conn.commit()

db_init()

# --- HELPERS ---
def get_setting(key: str) -> str | None:
    conn.commit()  # FIX
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None

def set_setting(key: str, value: str) -> None:
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

def increment_setting(key: str, step: int = 1) -> None:
    current = get_setting(key)
    try:
        new_value = int(current or "0") + step
    except ValueError:
        new_value = step
    set_setting(key, str(new_value))

async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    avg, count = cursor.fetchone()
    return (round(avg, 1) if avg else 0), count

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Загрузить файл", callback_data="adm_file")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stat")],
        [InlineKeyboardButton(text="📩 Рассылка", callback_data="adm_sms")],
        [InlineKeyboardButton(text="🗑 Удалить отзыв", callback_data="adm_del")],
    ])

def parse_dt(dt_str: str) -> datetime:
    return MSK.localize(datetime.strptime(dt_str, "%d.%m.%Y %H:%M"))

# --- USER ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return

    cursor.execute(
        """INSERT OR IGNORE INTO users (user_id, username, full_name, has_downloaded)
           VALUES (?, ?, ?, 0)""",
        (message.from_user.id, message.from_user.username, message.from_user.full_name)
    )
    cursor.execute(
        "UPDATE users SET username=?, full_name=? WHERE user_id=?",
        (message.from_user.username, message.from_user.full_name, message.from_user.id)
    )
    conn.commit()

    name = f"@{message.from_user.username}" if message.from_user.username else (message.from_user.first_name or "друг")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])

    await message.answer(
        f"Привет, {name}! 👋\nПодпишись на канал и нажми кнопку, чтобы получить файл!" + CONTACT_INFO,
        reply_markup=kb
    )

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    await callback.answer()  # FIX

    user_id = callback.from_user.id

    if not await is_subscribed(user_id):
        await callback.answer("❌ Подпишись и попробуй ещё раз", show_alert=True)
        return

    file_id = get_setting("file_id")

    if not file_id or file_id == "None":  # FIX
        await callback.answer("❌ Админ еще не загрузил файл", show_alert=True)
        return

    cursor.execute("SELECT has_downloaded, last_download_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    now_dt = datetime.now(MSK)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")

    if row and row[1]:
        try:
            last_dt = parse_dt(row[1])
            if now_dt - last_dt < timedelta(minutes=15):
                diff = timedelta(minutes=15) - (now_dt - last_dt)
                await callback.answer(
                    f"❌ Повторная загрузка через {int(diff.total_seconds() // 60)} мин.",
                    show_alert=True
                )
                return
        except Exception:
            pass

    first_download = not row or row[0] != 1
    if first_download:
        increment_setting("downloads")

    cursor.execute(
        """INSERT OR REPLACE INTO users
           (user_id, username, full_name, has_downloaded, date_received, last_download_time)
           VALUES (?, ?, ?, 1, ?, ?)""",
        (
            user_id,
            callback.from_user.username,
            callback.from_user.full_name,
            now_str,
            now_str
        )
    )
    conn.commit()

    avg, _ = get_average_rating()
    total_dl = get_setting("downloads") or "0"

    caption = (
        f"🥁 <b>Файл готов!</b>\n"
        f"📈 Скачиваний: {total_dl}\n"
        f"⭐ Рейтинг: {avg}/5\n\n"
        f"/grade — оставить отзыв\n"
        f"/review — отзывы" + CONTACT_INFO
    )

    await callback.message.answer_document(file_id, caption=caption, parse_mode="HTML")

# --- ADMIN FILE SAVE FIX ---
@dp.message(AdminStates.waiting_for_file, F.document)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("file_id", message.document.file_id)
    )
    conn.commit()
    conn.execute("VACUUM")  # FIX

    print(f"FILE SAVED: {message.document.file_id}")  # FIX

    await message.answer("✅ Файл сохранен!")
    await state.clear()

# --- FIX FSM ---
@dp.callback_query(F.data.startswith("rate_"))
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() != ReviewStates.waiting_for_rating:
        await callback.answer()
        return

    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_text")]
    ])

    await callback.message.edit_text(
        f"Оценка {rating}/5!\nНапиши комментарий или нажми пропустить:",
        reply_markup=kb
    )
    await state.set_state(ReviewStates.waiting_for_comment)
    await callback.answer()

@dp.callback_query(F.data == "skip_text")
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() != ReviewStates.waiting_for_comment:
        await callback.answer()
        return

    data = await state.get_data()
    rating = data.get("rating")

    if not rating:
        await state.clear()
        await callback.answer("❌ Сначала выбери оценку.", show_alert=True)
        return

    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%М")

    cursor.execute(
        """INSERT OR REPLACE INTO reviews
           (user_id, username, rating, comment, date)
           VALUES (?, ?, ?, ?, ?)""",
        (
            callback.from_user.id,
            callback.from_user.username or callback.from_user.first_name,
            rating,
            "Без описания",
            dt
        )
    )
    conn.commit()

    await state.clear()
    await callback.message.edit_text("✅ Оценка сохранена!" + CONTACT_INFO)
    await callback.answer()
