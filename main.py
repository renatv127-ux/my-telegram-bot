
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

# --- 1. КЛАССЫ СОСТОЯНИЙ ---
class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

class LangStates(StatesGroup):
    choosing_lang = State()

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099 
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')

# --- ТЕКСТЫ ---
TEXTS = {
    'ru': {
        'start_msg': "Привет! Подпишись на канал и нажми кнопку!\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist",
        'sub_btn': "Подписаться на TWIXER",
        'dl_btn': "📥 Скачать файл",
        'bot_off': "❌ Бот временно отключен.",
        'not_sub': "❌ Подпишись на канал!",
        'no_file': "❌ Файл не загружен.",
        'queue': "⏳ <b>Вы в очереди...</b> Отправка через 4 сек.",
        'ready': "🥁 <b>Ваш файл готов!</b>\n📈 Скачало человек: {}\n⭐️ Рейтинг: {}/5\n\n/grade — оставить отзыв\n/review — все отзывы\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist",
        'err_send': "❌ Ошибка отправки.",
        'grade_ask': "⭐ Оцените файл (1-5):",
        'comment_ask': "✍️ Напишите отзыв (<b>до 200 симв.</b>):",
        'skip': "⏩ Пропустить",
        'saved': "✅ Сохранено!",
        'no_reviews': "💬 Отзывов пока нет.",
        'rev_header': "💬 <b>Последние отзывы (Страница {}/{}):</b>\n\n",
        'rev_line': "👤 @{} | {}\n📝 {}\n📅 {}\n\n",
        'back': "⬅️ Назад",
        'next': "Вперед ➡️",
        'not_dl': "❌ Вы не можете оставить отзыв, так как еще не скачали файл.",
        'too_long': "❌ Текст слишком длинный!",
        'wait': "⏳ Подождите {}м {}с.",
        'lang_change': "Выберите язык:"
    },
    'en': {
        'start_msg': "Hello! Subscribe to the channel and click the button!\n\nif you have any questions, write to @TwixerArtist",
        'sub_btn': "Subscribe to TWIXER",
        'dl_btn': "📥 Download file",
        'bot_off': "❌ Bot is temporarily disabled.",
        'not_sub': "❌ Subscribe to the channel!",
        'no_file': "❌ File not uploaded.",
        'queue': "⏳ <b>In queue...</b> Sending in 4 sec.",
        'ready': "🥁 <b>Your file is ready!</b>\n📈 Downloads: {}\n⭐️ Rating: {}/5\n\n/grade — leave review\n/review — all reviews\n\nif you have questions, write to @TwixerArtist",
        'err_send': "❌ Sending error.",
        'grade_ask': "⭐ Rate the file (1-5):",
        'comment_ask': "✍️ Write a review (<b>up to 200 chars</b>):",
        'skip': "⏩ Skip",
        'saved': "✅ Saved!",
        'no_reviews': "💬 No reviews yet.",
        'rev_header': "💬 <b>Last reviews (Page {}/{}):</b>\n\n",
        'rev_line': "👤 @{} | {}\n📝 {}\n📅 {}\n\n",
        'back': "⬅️ Back",
        'next': "Next ➡️",
        'not_dl': "❌ You cannot leave a review because you haven't downloaded the file yet.",
        'too_long': "❌ Text is too long!",
        'wait': "⏳ Please wait {}m {}s.",
        'lang_change': "Choose language:"
    }
}

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
download_queue = asyncio.Semaphore(2)

# --- БАЗА ДАННЫХ ---
DB_FILE = "bot_data.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time REAL DEFAULT 0, join_date TEXT, lang TEXT DEFAULT NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    try: cursor.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT NULL")
    except: pass
    conn.commit()

db_init()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_lang(user_id):
    cursor.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res and res[0] else None

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

async def check_bot_status(message_or_call):
    user_id = message_or_call.from_user.id
    if get_setting("bot_status") == "off" and user_id != ADMIN_ID:
        lang = get_lang(user_id) or 'ru'
        if isinstance(message_or_call, types.Message):
            await message_or_call.answer(TEXTS[lang]['bot_off'])
        else:
            await message_or_call.answer(TEXTS[lang]['bot_off'], show_alert=True)
        return False
    return True

# --- КЛАВИАТУРА ЯЗЫКА ---
def get_lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang_ru"),
         InlineKeyboardButton(text="🇺🇸 English", callback_data="setlang_en")]
    ])

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    lang = get_lang(user_id)

    # Если язык еще не выбран — просим выбрать
    if lang is None:
        await message.answer("Выберите язык / Choose language:", reply_markup=get_lang_kb())
        await state.set_state(LangStates.choosing_lang)
    else:
        # Если язык уже есть — сразу приветствие (если бот включен)
        if not await check_bot_status(message): return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]['sub_btn'], url=CHANNEL_URL)],
            [InlineKeyboardButton(text=TEXTS[lang]['dl_btn'], callback_data="dl_start")]
        ])
        await message.answer(TEXTS[lang]['start_msg'], reply_markup=kb)

@dp.message(Command("lang"))
async def cmd_lang(message: types.Message, state: FSMContext):
    if not await check_bot_status(message): return
    user_id = message.from_user.id
    lang = get_lang(user_id) or 'ru'
    await message.answer(TEXTS[lang]['lang_change'], reply_markup=get_lang_kb())
    await state.set_state(LangStates.choosing_lang)

@dp.callback_query(F.data.startswith("setlang_"), LangStates.choosing_lang)
async def process_lang_selection(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    
    cursor.execute("INSERT INTO users (user_id, username, full_name, join_date, lang) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT(user_id) DO UPDATE SET lang=?", 
                   (user_id, callback.from_user.username, callback.from_user.full_name, now_date, lang, lang))
    conn.commit()
    
    await callback.message.delete()
    await state.clear()
    
    if not await check_bot_status(callback): return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXTS[lang]['sub_btn'], url=CHANNEL_URL)],
        [InlineKeyboardButton(text=TEXTS[lang]['dl_btn'], callback_data="dl_start")]
    ])
    await callback.message.answer(TEXTS[lang]['start_msg'], reply_markup=kb)

@dp.callback_query(F.data == "dl_start")
async def process_dl(callback: types.CallbackQuery):
    if not await check_bot_status(callback): return
    user_id = callback.from_user.id
    lang = get_lang(user_id) or 'ru'
    
    if not await is_subscribed(user_id):
        await callback.answer(TEXTS[lang]['not_sub'], show_alert=True); return
    
    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer(TEXTS[lang]['no_file'], show_alert=True); return

    now_ts = time.time()
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    if u_data and u_data[0] and now_ts - float(u_data[0]) < 300:
        left = int(300 - (now_ts - float(u_data[0])))
        await callback.answer(TEXTS[lang]['wait'].format(left // 60, left % 60), show_alert=True); return

    msg = await callback.message.edit_text(TEXTS[lang]['queue'])
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (now_ts, date_str, user_id))
        conn.commit()
        dls, avg = get_stats_data()
        caption = TEXTS[lang]['ready'].format(dls, avg)
        try: 
            await bot.send_document(user_id, file_id, caption=caption)
            await msg.delete()
        except: 
            await callback.message.answer(TEXTS[lang]['err_send'])
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    if not await check_bot_status(message): return
    user_id = message.from_user.id
    lang = get_lang(user_id) or 'ru'
    
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    if not res or res[0] == 0:
        await message.answer(TEXTS[lang]['not_dl']); return
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rt_{i}") for i in range(1, 6)]])
    await message.answer(TEXTS[lang]['grade_ask'], reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rt_"), ReviewStates.waiting_for_rating)
async def rate_sel(call: types.CallbackQuery, state: FSMContext):
    if not await check_bot_status(call): return
    lang = get_lang(call.from_user.id) or 'ru'
    await state.update_data(rating=int(call.data.split("_")[1]))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=TEXTS[lang]['skip'], callback_data="sk_comment")]])
    await call.message.edit_text(TEXTS[lang]['comment_ask'], reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(Command("review"))
async def cmd_reviews(message: types.Message):
    if not await check_bot_status(message): return
    user_id = message.from_user.id
    lang = get_lang(user_id) or 'ru'
    
    limit = 5
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    if not rows:
        await message.answer(TEXTS[lang]['no_reviews'])
    else:
        text = TEXTS[lang]['rev_header'].format(1, 1)
        for r in rows:
            text += TEXTS[lang]['rev_line'].format(r[0], '⭐'*r[1], r[2], r[3])
        await message.answer(text)

# --- АДМИН КОМАНДЫ ---

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def b_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit(); await message.answer("✅ Бот ВКЛЮЧЕН.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def b_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit(); await message.answer("❌ Бот ВЫКЛЮЧЕН.")

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_f(message: types.Message, state: FSMContext):
    await message.answer("Отправьте файл."); await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def file_up(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit(); await message.answer("✅ Файл сохранен!"); await state.clear()

async def main():
    user_cmds = [
        BotCommand(command="start", description="Запуск / Start"),
        BotCommand(command="lang", description="Язык / Language"),
        BotCommand(command="grade", description="Отзыв / Review"),
        BotCommand(command="review", description="Все отзывы / All reviews")
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
