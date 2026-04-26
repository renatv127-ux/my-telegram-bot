
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

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
download_queue = asyncio.Semaphore(2)

# Глобальные переменные для защиты от накрутки
DOWNLOAD_ATTEMPTS = [] 

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

# --- ФУНКЦИИ ---

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

def get_stats_data():
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file=1")
    dls = cursor.fetchone()[0]
    cursor.execute("SELECT AVG(rating) FROM reviews")
    avg = cursor.fetchone()[0]
    return dls, round(avg, 1) if avg else 0

# --- ПАГИНАЦИЯ ОТЗЫВОВ ---

async def show_reviews_page(message_or_call, page: int):
    limit = 5
    offset = page * limit
    cursor.execute("SELECT COUNT(*) FROM reviews")
    total_reviews = cursor.fetchone()[0]
    total_pages = (total_reviews + limit - 1) // limit

    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()

    if not rows:
        text = "💬 Отзывов пока нет."
        kb = None
    else:
        text = f"💬 <b>Последние отзывы (Страница {page + 1}/{max(1, total_pages)}):</b>\n\n"
        for r in rows:
            text += f"👤 @{r[0]} | {'⭐'*r[1]}\n📝 {r[2]}\n📅 {r[3]}\n\n"
        
        nav_btns = []
        if page > 0:
            nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"revp_{page-1}"))
        if page < total_pages - 1:
            nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"revp_{page+1}"))
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_btns]) if nav_btns else None

    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=kb)
    else:
        await message_or_call.message.edit_text(text, reply_markup=kb)

# --- МЕНЮ ---

async def set_main_menu(bot: Bot):
    user_cmds = [
        BotCommand(command="start", description="Скачать файл"),
        BotCommand(command="grade", description="Оставить отзыв"),
        BotCommand(command="review", description="Все отзывы"),
        BotCommand(command="help", description="Помощь")
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    
    admin_cmds = user_cmds + [
        BotCommand(command="full_stats", description="Статистика"),
        BotCommand(command="set_file", description="Загрузить файл"),
        BotCommand(command="on", description="Включить бота"),
        BotCommand(command="off", description="Выключить бота"),
        BotCommand(command="sms", description="Рассылка"),
        BotCommand(command="wipe_users", description="Очистить базу")
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
        [InlineKeyboardButton(text="📥 Скачать файл", callback_data="dl_req")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist", reply_markup=kb)

@dp.callback_query(F.data == "dl_req")
async def process_dl(callback: types.CallbackQuery):
    global DOWNLOAD_ATTEMPTS
    
    # Защита от накрутки (Anti-Flood)
    now_ts = time.time()
    DOWNLOAD_ATTEMPTS.append(now_ts)
    # Очищаем старые метки (старше 1 секунды)
    DOWNLOAD_ATTEMPTS = [t for t in DOWNLOAD_ATTEMPTS if now_ts - t < 1]
    
    if len(DOWNLOAD_ATTEMPTS) > 50: # Если больше 50 кликов в секунду
        cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
        conn.commit()
        await bot.send_message(ADMIN_ID, "🚨 <b>ВНИМАНИЕ!</b> Зафиксирована накрутка очереди (атака ботов). Бот автоматически <b>ВЫКЛЮЧЕН</b>. Проверь логи.")
        await callback.answer("❌ Бот временно заблокирован из-за накрутки.", show_alert=True)
        return

    if get_setting("bot_status") == "off" and callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Бот временно выключен.", show_alert=True); return

    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True); return
    
    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Файл не загружен.", show_alert=True); return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()

    if u_data and u_data[0] and now_ts - float(u_data[0]) < 300:
        left = int(300 - (now_ts - float(u_data[0])))
        await callback.answer(f"⏳ Подождите {left // 60}м {left % 60}с.", show_alert=True); return

    msg = await callback.message.edit_text("⏳ <b>Вы в очереди...</b> Отправка через 4 сек.")
    
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (now_ts, date_str, user_id))
        conn.commit()
        
        dls, avg = get_stats_data()
        caption = (
            f"🥁 <b>Ваш файл готов!</b>\n"
            f"📈 Скачало человек: {dls}\n"
            f"⭐️ Рейтинг: {avg}/5\n\n"
            f"/grade — оставить отзыв\n"
            f"/review — все отзывы\n\n"
            f"если будут вопросы или проблемы, пиши в лс @TwixerArtist"
        )
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await msg.delete()
        except:
            await callback.message.answer("❌ Ошибка отправки.")
    await callback.answer()

# --- ОТЗЫВЫ (С ПРОВЕРКОЙ СКАЧИВАНИЯ) ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    # Проверка: скачивал ли файл?
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (message.from_user.id,))
    res = cursor.fetchone()
    if not res or res[0] == 0:
        await message.answer("❌ <b>Ошибка!</b>\nВы не можете оставить отзыв, так как еще не скачали файл.")
        return

    # Анти-спам (раз в 10 минут)
    cursor.execute("SELECT date FROM reviews WHERE user_id=? ORDER BY date DESC LIMIT 1", (message.from_user.id,))
    last_rev = cursor.fetchone()
    if last_rev:
        # Простое ограничение, чтобы не спамили кнопками
        pass 

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rt_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцените файл (1-5):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rt_"), ReviewStates.waiting_for_rating)
async def rate_sel(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(rating=int(call.data.split("_")[1]))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ Пропустить текст", callback_data="skip_text")]])
    await call.message.edit_text("✍️ Напишите краткий отзыв (<b>до 200 символов</b>):", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.callback_query(F.data == "skip_text", ReviewStates.waiting_for_comment)
async def skip_text(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (call.from_user.id, call.from_user.username or "User", data['rating'], "Без комментария", now))
    conn.commit()
    await call.message.edit_text("✅ Оценка сохранена!")
    await state.clear()

@dp.message(ReviewStates.waiting_for_comment)
async def save_rev(message: types.Message, state: FSMContext):
    if len(message.text) > 200:
        await message.answer("❌ <b>Текст слишком длинный!</b> Напишите до 200 символов."); return
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, now))
    conn.commit()
    await message.answer("✅ Отзыв сохранен!"); await state.clear()

@dp.message(Command("review"))
async def cmd_reviews(message: types.Message):
    await show_reviews_page(message, 0)

@dp.callback_query(F.data.startswith("revp_"))
async def process_rev_page(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[1])
    await show_reviews_page(callback, page)
    await callback.answer()

# --- АДМИНКА ---

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats_cmd(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users"); total_ever = cursor.fetchone()[0]
    y_ago = (datetime.now(MSK) - timedelta(days=365)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (y_ago,)); total_year = cursor.fetchone()[0]
    m_ago = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (m_ago,)); total_month = cursor.fetchone()[0]
    dls, _ = get_stats_data()

    res = f"📊 <b>ПОЛНАЯ СТАТИСТИКА БОТА</b>\n\n🌎 <b>Пользователей в базе:</b>\n"
    res += f"└ За все время: <code>{total_ever}</code>\n└ За год: <code>{total_year}</code>\n└ За месяц: <code>{total_month}</code>\n\n"
    res += f"📥 <b>Уникальных скачиваний:</b> <code>{dls}</code>\n\n📋 <b>Последние 15 скачавших:</b>\n"
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 15")
    for r in cursor.fetchall():
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += f"• @{r[1]} | {r[2]} | Саб: {sub}\n"
    await message.answer(res)

@dp.message(Command("wipe_users"), F.from_user.id == ADMIN_ID)
async def wipe_db(message: types.Message):
    cursor.execute("DELETE FROM users"); conn.commit()
    await message.answer("⚠️ <b>База пользователей очищена!</b>")

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_f(message: types.Message, state: FSMContext):
    await message.answer("Отправьте файл."); await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def file_up(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit(); await message.answer("✅ Файл сохранен!"); await state.clear()

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def b_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit()
    await message.answer("✅ Бот включен.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def b_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit()
    await message.answer("❌ Бот выключен.")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users"); c = 0
    for u in cursor.fetchall():
        try: await bot.send_message(u[0], txt); c += 1; await asyncio.sleep(0.04)
        except: pass
    await message.answer(f"✅ Отправлено {c} чел.")

async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
