
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

# Очередь скачивания (Semaphore): не более 2-х человек одновременно
download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ (С ПОДДЕРЖКОЙ RAILWAY VOLUMES) ---
DB_PATH = os.getenv("DATABASE_URL", "bot_data.db")

# Создаем папку для базы, если её нет (нужно для Volumes)
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time TEXT, last_msg_id INTEGER)''')
    
    # Проверка на наличие колонки last_msg_id для существующих баз
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'last_msg_id' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_msg_id INTEGER")

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
    
    # Удаление старого сообщения бота (если есть)
    cursor.execute("SELECT last_msg_id FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        try: await bot.delete_message(chat_id=message.chat.id, message_id=row[0])
        except: pass

    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (user_id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    sent_msg = await message.answer(f"Привет! Подпишись на канал и нажми кнопку, чтобы получить файл!{CONTACT_INFO}", reply_markup=kb)
    cursor.execute("UPDATE users SET last_msg_id=? WHERE user_id=?", (sent_msg.message_id, user_id))
    conn.commit()

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл не был прикреплен к боту", show_alert=True)
        return

    cursor.execute("SELECT last_download_time, received_file, date_received FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    now_dt = datetime.now(MSK)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")

    # Анти-спам: ограничение на повторное скачивание (раз в 5 минут)
    if u_data and u_data[0]:
        try:
            last_dt = datetime.strptime(u_data[0], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now_dt - last_dt < timedelta(minutes=5):
                diff = timedelta(minutes=5) - (now_dt - last_dt)
                await callback.answer(f"❌ Повторная загрузка будет доступна через {int(diff.total_seconds() // 60)} мин.", show_alert=True)
                return
        except: pass

    # --- ЛОГИКА ОЧЕРЕДИ И ЗАДЕРЖКИ ---
    await callback.message.edit_text("⏳ Вы в очереди... Файл будет отправлен через 3-5 секунд.")

    async with download_queue:
        await asyncio.sleep(4) # Искусственная задержка для защиты сервера
        
        if not u_data or u_data[1] == 0:
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")

        date_rec = (u_data[2] if u_data and u_data[2] else now_str)
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=? WHERE user_id=?", (now_str, date_rec, user_id))
        conn.commit()

        try: await callback.message.delete()
        except: pass

        avg, count = get_average_rating()
        total_dl = get_setting("downloads")
        caption = (f"🥁 <b>Файл готов!</b>\n📈 Скачиваний: {total_dl}\n⭐ Рейтинг: {avg}/5\n\n/grade — оставить отзыв\n/review — отзывы{CONTACT_INFO}")
        
        await callback.message.answer_document(file_id, caption=caption)
        await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT rating, comment, date FROM reviews WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        try:
            r_dt = datetime.strptime(existing[2], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if datetime.now(MSK) - r_dt > timedelta(hours=2):
                await message.answer(f"❌ Изменить отзыв можно только в течение 2-х часов!{CONTACT_INFO}")
                return
        except: pass
        text = f"🔄 Твой отзыв ({existing[0]}/5). Выбери новую оценку:"
    else: 
        text = "⭐ Оцени драм кит (1-5):"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer(text + CONTACT_INFO, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(rating=int(callback.data.split("_")[1]))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_text")]])
    await callback.message.edit_text(f"Оценка {callback.data.split('_')[1]}/5!\nНапиши комментарий или нажми пропустить:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cursor.execute("SELECT date FROM reviews WHERE user_id=?", (message.from_user.id,))
    row = cursor.fetchone()
    dt = row[0] if row else datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or message.from_user.first_name, data['rating'], message.text, dt))
    conn.commit()
    await message.answer(f"✅ Отзыв сохранен!{CONTACT_INFO}"); await state.clear()

@dp.callback_query(F.data == "skip_text", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cursor.execute("SELECT date FROM reviews WHERE user_id=?", (callback.from_user.id,))
    row = cursor.fetchone()
    dt = row[0] if row else datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (callback.from_user.id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без описания", dt))
    conn.commit()
    await callback.message.edit_text(f"✅ Оценка сохранена!{CONTACT_INFO}"); await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Отзывов: {count})\n\n"
    for r in cursor.fetchall():
        id_info = f"🆔 ID: <code>{r[4]}</code>\n" if message.from_user.id == ADMIN_ID else ""
        res += f"👤 @{r[0]} | {r[1]}/5\n{id_info}📝 {r[2]}\n📅 {r[3]}\n\n"
    await message.answer((res if count > 0 else "Отзывов пока нет.") + CONTACT_INFO)

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    f_status = "✅ ОК" if get_setting("file_id") else "❌ НЕТ ФАЙЛА"
    b_status = "Вкл" if get_setting("bot_status") == "on" else "Выкл"
    text = (f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\nФайл: {f_status}\nБот: {b_status}\n\n"
            f"<b>Команды:</b>\n/FileDK — Загрузить файл\n/on | /off — Состояние бота\n"
            f"/Stata — Список юзеров\n/sms [текст] — Рассылка\n"
            f"/delete_review [ID] — Удалить отзыв\n\n"
            f"<b>Юзер команды:</b> /start, /grade, /review")
    await message.answer(text)

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.answer("📁 Отправь файл (как ДОКУМЕНТ):")
        await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    if message.document:
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
        conn.commit()
        await message.answer("✅ Файл сохранен!"); await state.clear()
    else: await message.answer("❌ Это не файл. Отправь архив или документ.")

@dp.message(Command("on"))
async def bot_on(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
        conn.commit(); await message.answer("✅ Бот включен.")

@dp.message(Command("off"))
async def bot_off(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
        conn.commit(); await message.answer("❌ Бот выключен.")

@dp.message(Command("Stata"))
async def admin_stata(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    if not rows: await message.answer("Никто еще не скачал."); return
    await message.answer("📊 Собираю статистику и проверяю подписки...")
    res = "📊 <b>Статистика скачавших:</b>\n\n"
    for r in rows:
        sub_status = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"ID: <code>{r[0]}</code> | @{r[1]} | {r[2]} | Подписка: {sub_status}\n"
    if len(res) > 4096:
        for x in range(0, len(res), 4096): await message.answer(res[x:x+4096])
    else: await message.answer(res)

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); users = cursor.fetchall(); c = 0
    for u in users:
        try:
            await bot.send_message(u[0], txt); c += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Рассылка: {c} чел.")

@dp.message(Command("delete_review"))
async def admin_del_review(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Пример: /delete_review 1234567")
            return
        rid = int(parts[1])
        cursor.execute("SELECT user_id FROM reviews WHERE user_id=?", (rid,))
        if cursor.fetchone():
            cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
            conn.commit()
            await message.answer(f"✅ Отзыв пользователя {rid} успешно удален.")
        else:
            await message.answer(f"❌ Отзыва от пользователя с ID {rid} не найдено.")
    except ValueError:
        await message.answer("❌ ID должен быть числом. Пример: /delete_review 1234567")
    except Exception as e:
        await message.answer(f"Произошла ошибка при удалении: {e}")

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
