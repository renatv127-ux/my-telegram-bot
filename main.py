
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
                       last_download_time REAL DEFAULT 0, join_date TEXT, is_banned INTEGER DEFAULT 0)''')
    # Обновление структуры для старых баз
    cols = [column[1] for column in cursor.execute("PRAGMA table_info(users)")]
    if "join_date" not in cols: cursor.execute("ALTER TABLE users ADD COLUMN join_date TEXT")
    if "is_banned" not in cols: cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- КНОПКА МЕНЮ ---

async def set_main_menu(bot: Bot):
    user_cmds = [
        BotCommand(command="start", description="Получить файл"),
        BotCommand(command="grade", description="Оставить отзыв"),
        BotCommand(command="review", description="Все отзывы"),
        BotCommand(command="help", description="Помощь")
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    admin_cmds = user_cmds + [
        BotCommand(command="full_stats", description="Статистика"),
        BotCommand(command="banlist", description="Список банов"),
        BotCommand(command="set_file", description="Загрузить файл"),
        BotCommand(command="sms", description="Рассылка")
    ]
    await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# --- ПРОВЕРКА БАНА ---

async def check_ban(user_id: int):
    cursor.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] == 1 if res else False

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    if await check_ban(user_id):
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
    await message.answer(f"🥁 <b>Привет!</b> Чтобы скачать файл, подпишись на канал и нажми кнопку ниже!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await check_ban(user_id):
        await callback.answer("⛔️ Вы заблокированы!", show_alert=True); return

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
        await callback.answer(f"⏳ Кулдаун! Подождите {left // 60}м {left % 60}с.", show_alert=True)
        return

    wait_msg = await callback.message.edit_text("⏳ <b>Вы в очереди...</b> Отправка через 4 сек.")
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (current_time, date_str, user_id))
        conn.commit()
        avg, _ = get_average_rating()
        caption = (f"✅ <b>Ваш файл готов!</b>\n📈 Скачало: {get_unique_downloads_count()}\n⭐ Рейтинг: {avg}/5\n\n"
                   f"💬 /grade — Оставить отзыв")
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await wait_msg.delete()
        except: await callback.message.answer("❌ Ошибка отправки.")
    await callback.answer()

# --- ПАГИНАЦИЯ ОТЗЫВОВ ---

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

    text = f"⭐ <b>ОТЗЫВЫ ПОЛЬЗОВАТЕЛЕЙ</b>\nСредний рейтинг: {avg}/5\n\n"
    if not rows:
        text += "Отзывов пока нет."
    else:
        for r in rows:
            stars = "⭐️" * r[1]
            text += f"👤 <b>@{r[0]}</b> {stars}\n💬 {r[2]}\n📅 {r[3]}\n\n"
    
    kb = []
    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"rev_page_{page-1}"))
    if total_pages > 1: nav_btns.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="none"))
    if page < total_pages - 1: nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"rev_page_{page+1}"))
    
    if nav_btns: kb.append(nav_btns)
    markup = InlineKeyboardMarkup(inline_keyboard=kb)

    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=markup)
    else:
        await message_or_call.message.edit_text(text, reply_markup=markup)

@dp.callback_query(F.data.startswith("rev_page_"))
async def process_rev_page(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[2])
    await show_reviews_page(callback, page)
    await callback.answer()

# --- СОЗДАНИЕ ОТЗЫВА ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    if await check_ban(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ <b>Оцените файл (1-5):</b>", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить ➡️", callback_data="skip_comment")]])
    await callback.message.edit_text(f"⭐ Ваша оценка: {rating}/5\n\nНапишите краткий отзыв (до 200 символов):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    if len(message.text) > 200:
        await message.answer("❌ <b>Слишком длинный текст!</b> Пожалуйста, напишите отзыв короче (до 200 символов).")
        return
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, now))
    conn.commit(); await message.answer("✅ <b>Спасибо за Ваш отзыв!</b>"); await state.clear()

@dp.callback_query(F.data == "skip_comment", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (callback.from_user.id, callback.from_user.username or "User", data['rating'], "Без описания", datetime.now(MSK).strftime("%d.%m.%Y %H:%M")))
    conn.commit(); await callback.message.edit_text("✅ <b>Оценка сохранена!</b>"); await state.clear()

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats_cmd(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users"); total = cursor.fetchone()[0]
    month = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (month,)); m_count = cursor.fetchone()[0]
    year = (datetime.now(MSK) - timedelta(days=365)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (year,)); y_count = cursor.fetchone()[0]
    
    res = f"📊 <b>ПОЛНАЯ СТАТИСТИКА БОТА</b>\n\n"
    res += f"🌎 <b>Пользователей в базе:</b>\n└ За все время: <code>{total}</code>\n└ За год: <code>{y_count}</code>\n└ За месяц: <code>{m_count}</code>\n\n"
    res += f"📥 <b>Уникальных скачиваний:</b> {get_unique_downloads_count()}\n\n📋 <b>Последние скачавшие:</b>\n"
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 10")
    for r in cursor.fetchall():
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"• @{r[1]} | {r[2]} | Саб: {sub}\n"
    await message.answer(res)

@dp.message(Command("ban"), F.from_user.id == ADMIN_ID)
async def ban_user(message: types.Message):
    try:
        uid = int(message.text.split()[1])
        cursor.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
        conn.commit(); await message.answer(f"🚫 Пользователь <code>{uid}</code> заблокирован.")
    except: await message.answer("Формат: /ban ID")

@dp.message(Command("unban"), F.from_user.id == ADMIN_ID)
async def unban_user(message: types.Message):
    try:
        uid = int(message.text.split()[1])
        cursor.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
        conn.commit(); await message.answer(f"✅ Пользователь <code>{uid}</code> разблокирован.")
    except: await message.answer("Формат: /unban ID")

@dp.message(Command("banlist"), F.from_user.id == ADMIN_ID)
async def banlist_cmd(message: types.Message):
    cursor.execute("SELECT user_id, username FROM users WHERE is_banned=1")
    rows = cursor.fetchall()
    if not rows: await message.answer("Список банов пуст."); return
    res = "🚫 <b>СПИСОК ЗАБЛОКИРОВАННЫХ:</b>\n\n"
    for r in rows: res += f"• <code>{r[0]}</code> (@{r[1]})\n"
    await message.answer(res)

@dp.message(Command("wipe_users"), F.from_user.id == ADMIN_ID)
async def wipe_all_users(message: types.Message):
    cursor.execute("DELETE FROM users"); conn.commit()
    await message.answer("⚠️ <b>БАЗА ПОЛЬЗОВАТЕЛЕЙ ПОЛНОСТЬЮ ОЧИЩЕНА!</b>\nВсе статистики (год/месяц) сброшены.")

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer(f"🛠 <b>Админ-панель</b>\n\nБот: {get_setting('bot_status')}\nФайл: {'✅' if len(get_setting('file_id')) > 5 else '❌'}\n\n"
                         f"Управление:\n/on | /off — Вкл/Выкл бота\n/set_file — Загрузить файл\n/full_stats — Статистика\n"
                         f"/ban ID | /unban ID — Бан\n/banlist — Список банов\n/wipe_users — Сброс ВСЕХ юзеров")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); c = 0
    for u in cursor.fetchall():
        try: await bot.send_message(u[0], txt); c += 1; await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Отправлено {c} чел.")

@dp.message(Command("clear_reviews"), F.from_user.id == ADMIN_ID)
async def clear_revs(message: types.Message):
    cursor.execute("DELETE FROM reviews"); conn.commit(); await message.answer("✅ Отзывы удалены.")

@dp.message(Command("clear_stata"), F.from_user.id == ADMIN_ID)
async def clear_st(message: types.Message):
    cursor.execute("UPDATE users SET received_file=0, last_download_time=0, date_received=NULL"); conn.commit(); await message.answer("✅ Скачивания обнулены.")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit(); await message.answer("✅ ВКЛ.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit(); await message.answer("❌ ВЫКЛ.")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = "📖 <b>Команды:</b>\n/start — Файл\n/grade — Отзыв\n/review — Список отзывов\n/help — Помощь"
    if message.from_user.id == ADMIN_ID: text += "\n\n👑 <b>Админ:</b> /admin, /full_stats, /banlist, /sms"
    await message.answer(text)

# --- ФУНКЦИИ ВСПОМОГАТЕЛЬНЫЕ ---
def get_unique_downloads_count():
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file = 1"); return cursor.fetchone()[0]
def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,)); res = cursor.fetchone(); return res[0] if res else ""
def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews"); res = cursor.fetchone()
    if not res or res[0] is None: return 0, 0
    return round(res[0], 1), res[1]
async def is_subscribed(user_id):
    try: member = await bot.get_chat_member(CHANNEL_ID, user_id); return member.status in ["member", "administrator", "creator"]
    except: return False

async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
