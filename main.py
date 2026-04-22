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
    user_id = callback.from_user.id

    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Админ еще не загрузил файл через /FileDK", show_alert=True)
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
                    f"❌ Повторная загрузка будет через {int(diff.total_seconds() // 60)} мин.",
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
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    cursor.execute("SELECT has_downloaded FROM users WHERE user_id=?", (message.from_user.id,))
    row = cursor.fetchone()

    if not row or row[0] != 1:
        await message.answer("❌ Сначала скачай драм кит через /start!" + CONTACT_INFO)
        return

    cursor.execute("SELECT rating, comment, date FROM reviews WHERE user_id=?", (message.from_user.id,))
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

@dp.callback_query(F.data.startswith("rate_"))
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != ReviewStates.waiting_for_rating.state:
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
    current_state = await state.get_state()
    if current_state != ReviewStates.waiting_for_comment.state:
        await callback.answer()
        return

    data = await state.get_data()
    rating = data.get("rating")
    if not rating:
        await state.clear()
        await callback.answer("❌ Сначала выбери оценку.", show_alert=True)
        return

    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
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

@dp.message(ReviewStates.waiting_for_comment, F.text)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id

    rating = data.get("rating")
    if not rating:
        await state.clear()
        await message.answer("❌ Сначала выбери оценку через /grade." + CONTACT_INFO)
        return

    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute(
        """INSERT OR REPLACE INTO reviews
           (user_id, username, rating, comment, date)
           VALUES (?, ?, ?, ?, ?)""",
        (
            user_id,
            message.from_user.username or message.from_user.first_name,
            rating,
            message.text,
            dt
        )
    )
    conn.commit()
    await message.answer("✅ Отзыв сохранен!" + CONTACT_INFO)
    await state.clear()

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment_non_text(message: types.Message):
    await message.answer("Отправь текстовый комментарий или нажми «Пропустить»." + CONTACT_INFO)

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY rowid DESC LIMIT 10")
    rows = cursor.fetchall()

    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Отзывов: {count})\n\n"
    if not rows:
        res = "Отзывов пока нет."

    for r in rows:
        res += (
            f"👤 @{r[0]} | {r[1]}/5\n"
            f"🆔 ID: <code>{r[4]}</code>\n"
            f"📝 {r[2]}\n"
            f"📅 {r[3]}\n\n"
        )

    await message.answer(res + CONTACT_INFO, parse_mode="HTML")

# --- ADMIN ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    file_status = "❌ Не загружен" if not get_setting("file_id") else "✅ Загружен"
    bot_status = "Вкл" if get_setting("bot_status") == "on" else "Выкл"

    text = f"""🛠 <b>АДМИН-ПАНЕЛЬ</b>

Статус файла: {file_status}
Статус бота: {bot_status}

⚙️ <b>КОМАНДЫ АДМИНА:</b>
/FileDK — Загрузить/Обновить файл
/on | /off — Включить/Выключить бота
/Stata — Посмотреть всех скачавших
/sms [текст] — Рассылка сообщения всем
/delete_review [ID] — Удалить отзыв (ID брать в /review)

👤 <b>КОМАНДЫ ПОЛЬЗОВАТЕЛЯ:</b>
/start — Главное меню / Регистрация
/grade — Поставить оценку
/review — Посмотреть последние отзывы"""

    await message.answer(text, parse_mode="HTML", reply_markup=admin_kb())

@dp.callback_query(F.data == "adm_file")
async def adm_file(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.message.answer("📁 Отправь файл командой /FileDK, затем пришли сам документ.")
    await callback.answer()

@dp.callback_query(F.data == "adm_stat")
async def adm_stat(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    cursor.execute("SELECT COUNT(*) FROM users WHERE has_downloaded=1")
    downloaded_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM reviews")
    reviews_count = cursor.fetchone()[0]

    avg, _ = get_average_rating()
    downloads = get_setting("downloads") or "0"

    text = (
        f"📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 Пользователей скачавших: {downloaded_users}\n"
        f"⬇️ Всего скачиваний: {downloads}\n"
        f"⭐ Средний рейтинг: {avg}\n"
        f"📝 Отзывов: {reviews_count}"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "adm_sms")
async def adm_sms(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.message.answer("📩 Используй команду: /sms [текст]")
    await callback.answer()

@dp.callback_query(F.data == "adm_del")
async def adm_del(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.message.answer("🗑 Используй команду: /delete_review [ID]")
    await callback.answer()

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("📁 Отправь файл драм-кита одним сообщением.")
    await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)",
        (message.document.file_id,)
    )
    conn.commit()
    await message.answer("✅ Файл сохранен!")
    await state.clear()

@dp.message(AdminStates.waiting_for_file)
async def admin_file_wait(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Пришли именно файл-документ, а не текст.")

@dp.message(Command("on"))
async def bot_on(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    set_setting("bot_status", "on")
    await message.answer("✅ Бот включен.")

@dp.message(Command("off"))
async def bot_off(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    set_setting("bot_status", "off")
    await message.answer("❌ Бот выключен.")

@dp.message(Command("Stata"))
async def admin_stata(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    cursor.execute(
        "SELECT user_id, username, date_received FROM users WHERE has_downloaded=1"
    )
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Никто еще не скачал.")
        return

    res = "<b>📊 Статистика:</b>\n\n"
    for user_id, username, date_received in rows:
        uname = f"@{username}" if username else "без username"
        res += f"ID: <code>{user_id}</code> | {uname} | {date_received or '-'}\n"

    await message.answer(res[:4000], parse_mode="HTML")

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    txt = message.text.replace("/sms", "", 1).strip()
    if not txt:
        await message.answer("Пример: /sms Привет всем!")
        return

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    sent = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, txt)
            sent += 1
            await asyncio.sleep(0.08)
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена. Отправлено: {sent}")

@dp.message(Command("delete_review"))
async def admin_del_review(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        rid = int(message.text.split()[1])
    except Exception:
        await message.answer("Пример: /delete_review 1234567")
        return

    cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
    conn.commit()
    await message.answer(f"✅ Отзыв {rid} удален.")

# --- RUN ---
async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
