
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

# Очередь скачивания (не более 2-х человек одновременно)
download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ (С ПОДДЕРЖКОЙ RAILWAY VOLUMES) ---
if os.getenv("DB_PATH"):
    DB_FILE = os.getenv("DB_PATH")
elif os.path.exists("/data"):
    DB_FILE = "/data/bot_data.db"
else:
    DB_FILE = "bot_data.db"

print(f"--- DATABASE INITIALIZED AT: {DB_FILE} ---")

db_dir = os.path.dirname(DB_FILE)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time TEXT, last_msg_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    conn.commit()

db_init()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
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

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    if get_setting("bot_status") == "off" and user_id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return

    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (user_id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл еще не загружен администратором.", show_alert=True)
        return

    # Проверка Кулдауна 5 минут
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    now_dt = datetime.now(MSK)
    if u_data and u_data[0]:
        try:
            last_dt = datetime.strptime(u_data[0], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now_dt - last_dt < timedelta(minutes=5):
                await callback.answer("⏳ Подождите 5 минут перед повторным скачиванием.", show_alert=True)
                return
        except: pass

    await callback.message.edit_text("⏳ Вы в очереди... Файл будет отправлен через 4 секунды.")

    async with download_queue:
        await asyncio.sleep(4) # Защита от перегрузки
        
        now_str = now_dt.strftime("%d.%m.%Y %H:%M")
        cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")

        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (now_str, now_str, user_id))
        conn.commit()

        avg, count = get_average_rating()
        caption = (f"🥁 <b>Файл готов!</b>\n📈 Скачиваний: {get_setting('downloads')}\n⭐ Рейтинг: {avg}/5\n\n/grade — оставить отзыв\n/review — все отзывы{CONTACT_INFO}")
        
        await callback.message.answer_document(file_id, caption=caption)
        try: await callback.message.delete()
        except: pass

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени драм кит (1-5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(rating=int(callback.data.split("_")[1]))
    await callback.message.edit_text("Напиши краткий отзыв или нажми /skip чтобы пропустить:")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or message.from_user.first_name, data['rating'], message.text, dt))
    conn.commit()
    await message.answer(f"✅ Отзыв сохранен!{CONTACT_INFO}"); await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Всего отзывов: {count})\n\n"
    rows = cursor.fetchall()
    for r in rows:
        admin_info = f"🆔 ID: <code>{r[4]}</code>\n" if message.from_user.id == ADMIN_ID else ""
        res += f"👤 @{r[0]} | {r[1]}/5\n{admin_info}📝 {r[2]}\n📅 {r[3]}\n\n"
    await message.answer(res if rows else "Отзывов пока нет.")

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    f_status = "✅ Загружен" if get_setting("file_id") else "❌ Нет файла"
    await message.answer(f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\nФайл: {f_status}\nБот: {get_setting('bot_status')}\n\n"
                         f"Команды:\n/FileDK — Загрузить файл\n/Stata — Статистика\n/sms — Рассылка\n"
                         f"/on | /off — Состояние бота\n/delete_review [ID] — Удалить отзыв")

@dp.message(Command("FileDK"), F.from_user.id == ADMIN_ID)
async def admin_file_req(message: types.Message, state: FSMContext):
    await message.answer("📁 Отправь файл (как ДОКУМЕНТ):")
    await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def admin_file_save(message: types.Message, state: FSMContext):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
    conn.commit(); await message.answer("✅ Файл сохранен!"); await state.clear()

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def admin_stata(message: types.Message):
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    res = f"📊 <b>Статистика скачавших ({len(rows)}):</b>\n\n"
    for r in rows:
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"ID: <code>{r[0]}</code> | @{r[1]} | {r[2]} | Подписка: {sub}\n"
    if len(res) > 4000:
        for x in range(0, len(res), 4096): await message.answer(res[x:x+4096])
    else: await message.answer(res if rows else "Никто еще не скачал.")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall(); c = 0
    for u in users:
        try:
            await bot.send_message(u[0], txt); c += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Рассылка завершена для {c} человек.")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
    conn.commit(); await message.answer("✅ Бот включен.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
    conn.commit(); await message.answer("❌ Бот выключен.")

@dp.message(Command("delete_review"), F.from_user.id == ADMIN_ID)
async def admin_del_review(message: types.Message):
    try:
        rid = int(message.text.split()[1])
        cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
        conn.commit(); await message.answer(f"✅ Отзыв пользователя {rid} удален.")
    except: await message.answer("Пример: /delete_review 1234567")

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
