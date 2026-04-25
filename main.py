
import os
import asyncio
import sqlite3
import time
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

download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ ---
DB_FILE = os.getenv("DB_PATH", "/data/bot_data.db")
db_dir = os.path.dirname(DB_FILE)
if db_dir and not os.path.exists(db_dir):
    try: os.makedirs(db_dir, exist_ok=True)
    except: DB_FILE = "bot_data.db"

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time REAL DEFAULT 0, join_date TEXT)''')
    # Добавляем колонку join_date если её нет в старой базе
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN join_date TEXT")
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- ФУНКЦИИ ---

def get_unique_downloads_count():
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file = 1")
    return cursor.fetchone()[0]

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else ""

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    res = cursor.fetchone()
    if not res or res[0] is None: return 0, 0
    return round(res[0], 1), res[1]

async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return False

# --- УМНОЕ МЕНЮ ПОМОЩИ ---

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    is_admin = message.from_user.id == ADMIN_ID
    
    help_text = "📖 <b>Меню команд бота</b>\n\n"
    help_text += "<b>Для пользователей:</b>\n"
    help_text += "/start — Главная страница и получение файла\n"
    help_text += "/grade — Оценить файл и оставить отзыв\n"
    help_text += "/review — Посмотреть отзывы других людей\n"
    help_text += "/help — Список всех команд\n"

    if is_admin:
        help_text += "\n👑 <b>ДЛЯ АДМИНИСТРАТОРА:</b>\n"
        help_text += "— <i>Управление:</i>\n"
        help_text += "/admin — Статус файла и кнопки включения\n"
        help_text += "/set_file — Загрузить новый файл в бота\n"
        help_text += "/on | /off — Включить или выключить бота\n"
        help_text += "— <i>Статистика:</i>\n"
        help_text += "/full_stats — Глобальная стата (Месяц/Год/Подписки)\n"
        help_text += "/Stata — Краткий список ID всех скачавших\n"
        help_text += "— <i>Работа с данными:</i>\n"
        help_text += "/sms [текст] — Рассылка сообщения всем юзерам\n"
        help_text += "/clear_reviews — Удалить абсолютно все отзывы\n"
        help_text += "/clear_stata — Сбросить историю скачиваний\n"
        help_text += "/delete_review [ID] — Удалить отзыв по ID\n"
    
    await message.answer(help_text)

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return

    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, join_date) VALUES (?, ?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name, now_date))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать файл", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!\n\n/help — список всех команд{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл еще не загружен.", show_alert=True)
        return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    current_time = time.time()

    if u_data and u_data[0]:
        try:
            if current_time - float(u_data[0]) < 300:
                left = int(300 - (current_time - float(u_data[0])))
                await callback.answer(f"⏳ Подождите {left // 60}м {left % 60}с.", show_alert=True)
                return
        except: pass

    wait_msg = await callback.message.edit_text("⏳ Вы в очереди... Файл будет отправлен через 4 секунды.")

    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (current_time, date_str, user_id))
        conn.commit()

        avg, count = get_average_rating()
        unique_dl = get_unique_downloads_count()
        caption = (f"🥁 <b>Ваш файл готов!</b>\n📈 Скачало человек: {unique_dl}\n⭐ Рейтинг: {avg}/5\n\n"
                   f"/grade — оставить отзыв\n/review — все отзывы{CONTACT_INFO}")
        
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await wait_msg.delete()
        except:
            await callback.message.answer("❌ Ошибка отправки. Свяжитесь с @TwixerArtist")
    await callback.answer()

# --- ОТЗЫВЫ ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени файл от 1 до 5:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = callback.data.split("_")[1]
    await state.update_data(rating=int(rating))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_comment_text")]])
    await callback.message.edit_text(f"Твоя оценка: {rating}/5!\nНапиши комментарий или пропусти:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    now_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or message.from_user.first_name, data['rating'], message.text, now_str))
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
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Всего: {count})\n\n"
    rows = cursor.fetchall()
    for r in rows:
        admin_info = f"🆔 ID: <code>{r[4]}</code>\n" if message.from_user.id == ADMIN_ID else ""
        res += f"👤 @{r[0]} | {r[1]}/5\n{admin_info}📝 {r[2]}\n📅 {r[3]}\n\n"
    await message.answer(res if rows else "Отзывов пока нет.")

# --- АДМИНКА ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer(f"🛠 <b>Админ-панель</b>\n\nФайл: {'✅' if len(get_setting('file_id')) > 5 else '❌'}\nБот: {get_setting('bot_status')}\n\n"
                         f"Используй /help для всех команд.")

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats_cmd(message: types.Message):
    # 1. За все время
    cursor.execute("SELECT COUNT(*) FROM users")
    total_ever = cursor.fetchone()[0]
    
    # 2. За год (365 дней)
    year_ago = (datetime.now(MSK) - timedelta(days=365)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (year_ago,))
    total_year = cursor.fetchone()[0]

    # 3. За месяц (30 дней)
    month_ago = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (month_ago,))
    total_month = cursor.fetchone()[0]
    
    # 4. Список скачавших
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 15")
    rows = cursor.fetchall()
    
    res = f"📊 <b>ПОЛНАЯ СТАТИСТИКА БОТА</b>\n\n"
    res += f"🌎 <b>Пользователей в базе:</b>\n"
    res += f"└ За все время: <code>{total_ever}</code>\n"
    res += f"└ За год: <code>{total_year}</code>\n"
    res += f"└ За месяц: <code>{total_month}</code>\n\n"
    res += f"📥 <b>Уникальных скачиваний:</b> {get_unique_downloads_count()}\n\n"
    res += f"📋 <b>Последние 15 скачавших:</b>\n"
    
    if not rows:
        res += "└ Пока никто не скачивал."
    else:
        for r in rows:
            sub = "✅" if await is_subscribed(r[0]) else "❌"
            res += f"• @{r[1]} | {r[2]} | Саб: {sub}\n"
    
    await message.answer(res)

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def admin_stata(message: types.Message):
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Пока никто не скачал."); return
    res = f"📊 <b>Список ID скачавших ({len(rows)}):</b>\n\n"
    for r in rows:
        res += f"🆔 <code>{r[0]}</code> | @{r[1]} | {r[2]}\n"
    await message.answer(res)

@dp.message(Command("clear_reviews"), F.from_user.id == ADMIN_ID)
async def clear_all_reviews(message: types.Message):
    cursor.execute("DELETE FROM reviews"); conn.commit()
    await message.answer("✅ Все отзывы удалены.")

@dp.message(Command("clear_stata"), F.from_user.id == ADMIN_ID)
async def clear_all_stata(message: types.Message):
    cursor.execute("UPDATE users SET received_file=0, last_download_time=0, date_received=NULL"); conn.commit()
    await message.answer("✅ Статистика скачиваний обнулена.")

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_file_cmd(message: types.Message, state: FSMContext):
    await message.answer("Отправьте файл."); await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def process_file_upload(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit(); await message.answer("✅ Файл сохранен!"); await state.clear()

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); users = cursor.fetchall(); c = 0
    for u in users:
        try:
            await bot.send_message(u[0], txt); c += 1; await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Отправлено {c} чел.")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
    conn.commit(); await message.answer("✅ Бот ВКЛ.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
    conn.commit(); await message.answer("❌ Бот ВЫКЛ.")

@dp.message(Command("delete_review"), F.from_user.id == ADMIN_ID)
async def del_review(message: types.Message):
    try:
        rid = int(message.text.split()[1])
        cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
        conn.commit(); await message.answer(f"✅ Отзыв {rid} удален.")
    except: await message.answer("Используй: /delete_review ID")

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
