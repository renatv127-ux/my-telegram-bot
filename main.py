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

# ================== CONFIG ==================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone("Europe/Moscow")

CONTACT_INFO = "\n\nесли будут вопросы — пиши @TwixerArtist"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================== STATES ==================
class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# ================== DB ==================
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        has_downloaded INTEGER DEFAULT 0,
        date_received TEXT,
        last_download_time TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        rating INTEGER,
        comment TEXT,
        date TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    cursor.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('downloads','0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('bot_status','on')")
    conn.commit()

db_init()

# ================== HELPERS ==================
def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    r = cursor.fetchone()
    return r[0] if r else None

def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
    conn.commit()

async def is_subscribed(user_id):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status in ["member", "creator", "administrator"]
    except:
        return False

def avg_rating():
    cursor.execute("SELECT AVG(rating), COUNT(*) FROM reviews")
    a, c = cursor.fetchone()
    return (round(a,1) if a else 0), c

# ================== ADMIN KB ==================
def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Файл", callback_data="adm_file")],
        [InlineKeyboardButton(text="📊 Стата", callback_data="adm_stat")],
        [InlineKeyboardButton(text="📩 Рассылка", callback_data="adm_sms")],
        [InlineKeyboardButton(text="🗑 Отзыв", callback_data="adm_del")]
    ])

# ================== START ==================
@dp.message(Command("start"))
async def start(m: types.Message):
    if get_setting("bot_status") == "off" and m.from_user.id != ADMIN_ID:
        return await m.answer("❌ бот выключен")

    cursor.execute("""
    INSERT OR IGNORE INTO users (user_id,username,full_name,has_downloaded)
    VALUES (?,?,?,0)
    """, (m.from_user.id, m.from_user.username, m.from_user.full_name))
    conn.commit()

    name = f"@{m.from_user.username}" if m.from_user.username else m.from_user.first_name

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📢 Подписка", url=CHANNEL_URL)],
        [InlineKeyboardButton("⬇️ Скачать", callback_data="download")]
    ])

    await m.answer(f"Привет {name}", reply_markup=kb)

# ================== DOWNLOAD ==================
@dp.callback_query(F.data == "download")
async def download(c: types.CallbackQuery):
    uid = c.from_user.id

    if not await is_subscribed(uid):
        return await c.answer("❌ подпишись", show_alert=True)

    file_id = get_setting("file_id")
    if not file_id:
        return await c.answer("❌ нет файла", show_alert=True)

    cursor.execute("SELECT has_downloaded,last_download_time FROM users WHERE user_id=?", (uid,))
    row = cursor.fetchone()

    now = datetime.now(MSK)
    now_s = now.strftime("%d.%m.%Y %H:%M")

    if row and row[1]:
        try:
            last = datetime.strptime(row[1], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now - last < timedelta(minutes=15):
                return await c.answer("⏳ подожди 15 мин", show_alert=True)
        except:
            pass

    cursor.execute("""
    INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?)
    """, (
        uid,
        c.from_user.username,
        c.from_user.full_name,
        1,
        now_s,
        now_s
    ))
    conn.commit()

    a, cnt = avg_rating()
    dl = get_setting("downloads")

    await c.message.answer_document(
        file_id,
        caption=f"⬇️ готово\n📊 {dl}\n⭐ {a}/5\n/grade /review"
    )
    await c.answer()

# ================== GRADE ==================
@dp.message(Command("grade"))
async def grade(m: types.Message):
    cursor.execute("SELECT has_downloaded FROM users WHERE user_id=?", (m.from_user.id,))
    r = cursor.fetchone()

    if not r or r[0] != 1:
        return await m.answer("❌ сначала скачай")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(str(i), callback_data=f"rate_{i}") for i in range(1,6)]
    ])

    await m.answer("⭐ оценка:", reply_markup=kb)

@dp.callback_query(F.data.startswith("rate_"))
async def rate(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(rating=int(c.data.split("_")[1]))
    await c.message.edit_text("Напиши комментарий или пропусти")

@dp.message()
async def comment(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data:
        return

    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")

    cursor.execute("""
    INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?)
    """, (
        m.from_user.id,
        m.from_user.username or m.from_user.first_name,
        data["rating"],
        m.text,
        dt
    ))
    conn.commit()

    await state.clear()
    await m.answer("✅ сохранено")

# ================== REVIEW ==================
@dp.message(Command("review"))
async def review(m: types.Message):
    a, c = avg_rating()

    cursor.execute("SELECT * FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()

    text = f"⭐ {a}/5 ({c})\n\n"

    for r in rows:
        text += f"@{r[1]} {r[2]}/5\n{r[3]}\n\n"

    await m.answer(text or "нет отзывов")

# ================== ADMIN ==================
@dp.message(Command("admin"))
async def admin(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return

    file_status = "❌ нет" if not get_setting("file_id") else "✅ есть"
    bot_status = get_setting("bot_status")

    text = f"""🛠 АДМИН-ПАНЕЛЬ

Статус файла: {file_status}
Статус бота: {bot_status}

⚙️ /FileDK — файл
/on /off — бот
/Stata — скачивания
/sms — рассылка
/delete_review — удалить

👤 /start /grade /review
"""

    await m.answer(text, reply_markup=admin_kb())

@dp.message(Command("FileDK"))
async def file(m: types.Message, state: FSMContext):
    if m.from_user.id == ADMIN_ID:
        await state.set_state(AdminStates.waiting_for_file)
        await m.answer("📁 файл?")

@dp.message(AdminStates.waiting_for_file, F.document)
async def save_file(m: types.Message, state: FSMContext):
    if m.from_user.id == ADMIN_ID:
        set_setting("file_id", m.document.file_id)
        await state.clear()
        await m.answer("✅ сохранено")

@dp.message(Command("on"))
async def on(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        set_setting("bot_status","on")
        await m.answer("ON")

@dp.message(Command("off"))
async def off(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        set_setting("bot_status","off")
        await m.answer("OFF")

@dp.message(Command("Stata"))
async def stat(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return

    cursor.execute("SELECT COUNT(*) FROM users WHERE has_downloaded=1")
    c = cursor.fetchone()[0]

    await m.answer(f"📊 скачали: {c}")

@dp.message(Command("sms"))
async def sms(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return

    txt = m.text.replace("/sms","").strip()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    for u in users:
        try:
            await bot.send_message(u[0], txt)
            await asyncio.sleep(0.1)
        except:
            pass

@dp.message(Command("delete_review"))
async def delr(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return

    try:
        uid = int(m.text.split()[1])
        cursor.execute("DELETE FROM reviews WHERE user_id=?", (uid,))
        conn.commit()
        await m.answer("deleted")
    except:
        await m.answer("use /delete_review id")

# ================== RUN ==================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())