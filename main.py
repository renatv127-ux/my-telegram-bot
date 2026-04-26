
import os
import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
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
DB_FILE = os.getenv("DB_PATH", "bot_data.db")
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    # Создаем таблицы
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time REAL DEFAULT 0, join_date TEXT, is_banned INTEGER DEFAULT 0)''')
    
    # Проверка и добавление новых колонок в старую таблицу
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if "join_date" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN join_date TEXT")
    if "is_banned" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")

    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_unique_downloads_count():
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file = 1")
    res = cursor.fetchone()
    return res[0] if res else 0

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else ""

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    res = cursor.fetchone()
    if not res or res[0] is None: return 0, 0
    return round(res[0], 1), res[1]

async def check_is_banned(user_id: int):
    cursor.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] == 1 if res else False

async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return False

# --- МЕНЮ КОМАНД ---

async def set_main_menu(bot: Bot):
    user_cmds = [
        BotCommand(command="start", description="Получить файл"),
        BotCommand(command="grade", description="Оценить файл"),
        BotCommand(command="review", description="Список отзывов"),
        BotCommand(command="help", description="Помощь")
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    
    admin_cmds = user_cmds + [
        BotCommand(command="admin", description="Панель управления"),
        BotCommand(command="full_stats", description="Вся статистика"),
        BotCommand(command="Stata", description="Список ID скачавших"),
        BotCommand(command="banlist", description="Список забаненных"),
        BotCommand(command="set_file", description="Загрузить файл"),
        BotCommand(command="sms", description="Рассылка сообщений"),
        BotCommand(command="wipe_users", description="Полный сброс базы юзеров")
    ]
    await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    
    if await check_is_banned(user_id):
        await message.answer(f"⛔️ <b>Админ заблокировал Вам доступ к боту!</b>{CONTACT_INFO}")
        return

    if get_setting("bot_status") == "off" and user_id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return

    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, join_date) VALUES (?, ?, ?, ?)", 
                   (user_id, message.from_user.username, message.from_user.full_name, now_date))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="📥 Скачать файл", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await check_is_banned(user_id):
        await callback.answer("⛔️ Доступ ограничен!", show_alert=True); return

    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True); return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл не загружен.", show_alert=True); return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    current_time = time.time()

    if u_data and u_data[0] and current_time - float(u_data[0]) < 300:
        left = int(300 - (current_time - float(u_data[0])))
        await callback.answer(f"⏳ Подождите {left // 60}м {left % 60}с.", show_alert=True); return

    wait_msg = await callback.message.edit_text("⏳ <b>Вы в очереди...</b> Отправка через 4 сек.")
    
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (current_time, date_str, user_id))
        conn.commit()
        
        avg, _ = get_average_rating()
        dl_count = get_unique_downloads_count()
        caption = f"🥁 <b>Ваш файл готов!</b>\n📈 Скачало: {dl_count}\n⭐ Рейтинг: {avg}/5\n\n/grade — оставить отзыв"
        
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await wait_msg.delete()
        except: await callback.message.answer("❌ Ошибка отправки файла.")
    await callback.answer()

# --- ОТЗЫВЫ С ПАГИНАЦИЕЙ ---

@dp.message(Command("review"))
async def cmd_reviews(message: types.Message):
    await show_reviews_page(message, 0)

async def show_reviews_page(message_or_call, page: int):
    limit = 5
    offset = page * limit
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM reviews"); total_revs = cursor.fetchone()[0]
    total_pages = (total_revs + limit - 1) // limit
    avg, _ = get_average_rating()

    text = f"⭐ <b>ОТЗЫВЫ ({total_revs})</b>\nСредний рейтинг: {avg}/5\n\n"
    if not rows:
        text += "Отзывов пока нет."
    else:
        for r in rows:
            text += f"👤 <b>@{r[0]}</b> | {'⭐️'*r[1]}\n💬 {r[2]}\n📅 {r[3]}\n\n"
    
    kb = []
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"revp_{page-1}"))
    if total_pages > 1: nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"revp_{page+1}"))
    if nav: kb.append(nav)
    
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=markup)
    else:
        await message_or_call.message.edit_text(text, reply_markup=markup)

@dp.callback_query(F.data.startswith("revp_"))
async def process_rev_page(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[1])
    await show_reviews_page(callback, page)
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    if await check_is_banned(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцените файл (1-5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(rating=int(callback.data.split("_")[1]))
    await callback.message.edit_text("✍️ Напишите Ваш отзыв (до 200 символов):")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    if len(message.text) > 200:
        await message.answer("❌ Слишком длинный отзыв! Попробуйте короче (до 200 символов).")
        return
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, now))
    conn.commit(); await message.answer("✅ Отзыв сохранен!"); await state.clear()

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats_cmd(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users"); total_ever = cursor.fetchone()[0]
    month = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (month,)); total_month = cursor.fetchone()[0]
    year = (datetime.now(MSK) - timedelta(days=365)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (year,)); total_year = cursor.fetchone()[0]
    
    res = f"📊 <b>ПОЛНАЯ СТАТИСТИКА БОТА</b>\n\n🌎 <b>Пользователей в базе:</b>\n"
    res += f"└ За все время: <code>{total_ever}</code>\n└ За год: <code>{total_year}</code>\n└ За месяц: <code>{total_month}</code>\n\n"
    res += f"📥 <b>Уникальных скачиваний:</b> {get_unique_downloads_count()}\n\n📋 <b>Последние 15 скачавших:</b>\n"
    
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 15")
    for r in cursor.fetchall():
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"• @{r[1]} | {r[2]} | Саб: {sub}\n"
    await message.answer(res)

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def stata_cmd(message: types.Message):
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    if not rows: await message.answer("Скачиваний нет."); return
    res = "📊 <b>Список ID скачавших:</b>\n\n"
    for r in rows: res += f"🆔 <code>{r[0]}</code> | @{r[1]} | {r[2]}\n"
    await message.answer(res)

@dp.message(Command("ban"), F.from_user.id == ADMIN_ID)
async def ban_cmd(message: types.Message):
    try:
        uid = int(message.text.split()[1])
        cursor.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
        conn.commit(); await message.answer(f"🚫 Пользователь {uid} забанен.")
    except: await message.answer("Формат: /ban ID")

@dp.message(Command("unban"), F.from_user.id == ADMIN_ID)
async def unban_cmd(message: types.Message):
    try:
        uid = int(message.text.split()[1])
        cursor.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
        conn.commit(); await message.answer(f"✅ Пользователь {uid} разбанен.")
    except: await message.answer("Формат: /unban ID")

@dp.message(Command("banlist"), F.from_user.id == ADMIN_ID)
async def banlist_cmd(message: types.Message):
    cursor.execute("SELECT user_id, username FROM users WHERE is_banned=1")
    rows = cursor.fetchall()
    if not rows: await message.answer("Бан-лист пуст."); return
    res = "🚫 <b>БАН-ЛИСТ:</b>\n\n"
    for r in rows: res += f"• <code>{r[0]}</code> (@{r[1]})\n"
    await message.answer(res)

@dp.message(Command("wipe_users"), F.from_user.id == ADMIN_ID)
async def wipe_cmd(message: types.Message):
    cursor.execute("DELETE FROM users"); conn.commit(); await message.answer("⚠️ База пользователей полностью очищена!")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def sms_cmd(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); c = 0
    for u in cursor.fetchall():
        try: await bot.send_message(u[0], txt); c += 1; await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Отправлено {c} пользователям.")

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_cmd(message: types.Message):
    await message.answer(f"🛠 <b>Админ-панель</b>\n\nБот: {get_setting('bot_status')}\nФайл: {'✅' if len(get_setting('file_id')) > 5 else '❌'}")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def on_cmd(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit(); await message.answer("✅ Бот ВКЛ.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def off_cmd(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit(); await message.answer("❌ Бот ВЫКЛ.")

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_file_cmd(message: types.Message, state: FSMContext):
    await message.answer("Отправьте файл."); await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def proc_file(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit(); await message.answer("✅ Файл сохранен!"); await state.clear()

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    is_admin = message.from_user.id == ADMIN_ID
    text = "📖 <b>Команды:</b>\n/start — Получить файл\n/grade — Оценить\n/review — Отзывы\n/help — Помощь"
    if is_admin: text += "\n\n👑 <b>Админ:</b> /admin, /full_stats, /banlist, /sms, /wipe_users"
    await message.answer(text)

async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
