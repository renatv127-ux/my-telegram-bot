
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
CONTACT_INFO = "если будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ ---
DB_FILE = "bot_data.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time REAL DEFAULT 0, join_date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def is_subscribed(user_id):
    if user_id == ADMIN_ID: return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return False

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else ""

def get_stats():
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file=1")
    dls = cursor.fetchone()[0]
    cursor.execute("SELECT AVG(rating) FROM reviews")
    avg = cursor.fetchone()[0]
    return dls, round(avg, 1) if avg else 0

# --- НАСТРОЙКА КНОПКИ МЕНЮ ---

async def set_main_menu(bot: Bot):
    # Команды для всех
    user_cmds = [
        BotCommand(command="start", description="Скачать файл"),
        BotCommand(command="grade", description="Оставить отзыв"),
        BotCommand(command="review", description="Все отзывы"),
        BotCommand(command="help", description="Помощь")
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    
    # Команды для Админа
    admin_cmds = user_cmds + [
        BotCommand(command="admin", description="Админка"),
        BotCommand(command="full_stats", description="Статистика"),
        BotCommand(command="set_file", description="Загрузить файл"),
        BotCommand(command="on", description="Включить бота"),
        BotCommand(command="off", description="Выключить бота"),
        BotCommand(command="sms", description="Рассылка"),
        BotCommand(command="wipe_users", description="Очистить базу юзеров")
    ]
    await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return
    
    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, join_date) VALUES (?, ?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name, now_date))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="📥 Скачать файл", callback_data="dl_file")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!\n\n{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "dl_file")
async def process_dl(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True); return
    
    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл еще не загружен.", show_alert=True); return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    curr_t = time.time()

    if u_data and u_data[0] and curr_t - float(u_data[0]) < 300:
        left = int(300 - (curr_t - float(u_data[0])))
        await callback.answer(f"⏳ Подождите {left // 60}м {left % 60}с.", show_alert=True); return

    msg = await callback.message.edit_text("⏳ <b>Вы в очереди...</b> Отправка через 4 секунды.")
    
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (curr_t, date_str, user_id))
        conn.commit()
        
        dl_count, avg_rating = get_stats()
        
        # ТВОЙ ТЕКСТ ПОД ФАЙЛОМ
        caption = (
            f"🥁 <b>Ваш файл готов!</b>\n"
            f"📈 Скачало человек: {dl_count}\n"
            f"⭐️ Рейтинг: {avg_rating}/5\n\n"
            f"/grade — оставить отзыв\n"
            f"/review — все отзывы\n\n"
            f"{CONTACT_INFO}"
        )
        
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await msg.delete()
        except:
            await callback.message.answer("❌ Ошибка при отправке файла.")
    await callback.answer()

# --- ОТЗЫВЫ (ЛИМИТ 200 СИМВОЛОВ) ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"r_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцените файл (1-5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("r_"), ReviewStates.waiting_for_rating)
async def rate_sel(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(rating=int(call.data.split("_")[1]))
    await call.message.edit_text("✍️ Напишите краткий отзыв (<b>лимит 200 символов</b>):")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_rev(message: types.Message, state: FSMContext):
    if len(message.text) > 200:
        await message.answer("❌ <b>Ошибка!</b> Отзыв слишком длинный. Пожалуйста, напишите короче (до 200 символов).")
        return
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, now))
    conn.commit()
    await message.answer("✅ <b>Спасибо!</b> Ваш отзыв сохранен.")
    await state.clear()

@dp.message(Command("review"))
async def cmd_reviews(message: types.Message):
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Отзывов пока нет.")
        return
    res = "💬 <b>Последние отзывы:</b>\n\n"
    for r in rows:
        res += f"👤 @{r[0]} | {'⭐'*r[1]}\n📝 {r[2]}\n📅 {r[3]}\n\n"
    await message.answer(res)

# --- АДМИНКА ---

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users"); total = cursor.fetchone()[0]
    m_ago = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (m_ago,)); m_c = cursor.fetchone()[0]
    dl_count, _ = get_stats()
    
    res = f"📊 <b>ПОЛНАЯ СТАТИСТИКА</b>\n\n🌎 Пользователей: <code>{total}</code>\n└ За месяц: <code>{m_c}</code>\n📥 Скачиваний: <code>{dl_count}</code>\n\n📋 <b>Последние 10 скачавших:</b>\n"
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 10")
    for r in cursor.fetchall():
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"• @{r[1]} | {r[2]} | Саб: {sub}\n"
    await message.answer(res)

@dp.message(Command("wipe_users"), F.from_user.id == ADMIN_ID)
async def wipe_db(message: types.Message):
    cursor.execute("DELETE FROM users"); conn.commit()
    await message.answer("⚠️ <b>База пользователей полностью очищена!</b>")

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_f(message: types.Message, state: FSMContext):
    await message.answer("Отправьте файл, который будут скачивать пользователи.")
    await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def file_up(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit()
    await message.answer("✅ <b>Файл успешно обновлен!</b>")
    await state.clear()

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def b_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit()
    await message.answer("✅ <b>Бот включен.</b> Пользователи могут скачивать файл.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def b_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit()
    await message.answer("❌ <b>Бот выключен.</b> Доступ для пользователей закрыт.")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); c = 0
    for u in cursor.fetchall():
        try: await bot.send_message(u[0], txt); c += 1; await asyncio.sleep(0.04)
        except: pass
    await message.answer(f"✅ Рассылка завершена. Получили: {c} чел.")

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def adm_panel(message: types.Message):
    await message.answer(f"🛠 <b>Админ-панель</b>\n\nСтатус: {get_setting('bot_status')}\nФайл: {'✅' if len(get_setting('file_id')) > 5 else '❌'}\n\nИспользуйте кнопку Меню для управления.")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    txt = "📖 <b>Команды:</b>\n/start - Скачать\n/grade - Оценить\n/review - Отзывы\n/help - Помощь"
    if message.from_user.id == ADMIN_ID:
        txt += "\n\n👑 <b>Админ:</b> /admin, /full_stats, /set_file, /on, /off, /wipe_users"
    await message.answer(txt)

async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
