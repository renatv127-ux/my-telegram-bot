
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

# Защита от накрутки
DOWNLOAD_ATTEMPTS = []

# --- ЯЗЫКОВЫЕ НАСТРОЙКИ ---
LANGUAGES = {
    "ru": {
        "welcome": "Привет! Пожалуйста, выбери язык:\nHello! Please choose your language:",
        "lang_select_ru": "🇷🇺 Русский",
        "lang_select_en": "🇬🇧 English",
        "choose_lang_prompt": "Выберите язык:",
        "message_after_lang_select": "Язык установлен. Теперь все будет на {} языке.",
        "download_channel_prompt": "Подпишись на канал и нажми кнопку!\n\nЕсли будут вопросы или проблемы, пиши в лс @TwixerArtist",
        "subscribe_button": "Подписаться на TWIXER",
        "download_button": "📥 Скачать файл",
        "download_queue_msg": "⏳ <b>Вы в очереди...</b> Отправка через 4 сек.",
        "download_limit_exceeded": "❌ Бот временно заблокирован.",
        "bot_off_msg": "❌ Бот временно отключен.",
        "subscribe_needed": "❌ Подпишись на канал!",
        "file_not_uploaded": "❌ Файл не загружен.",
        "wait_msg": "⏳ Подождите {}м {}с.",
        "download_complete_caption": (
            "🥁 <b>Ваш файл готов!</b>\n"
            "📈 Скачало человек: {}\n"
            "⭐️ Рейтинг: {}/5\n\n"
            "/grade — оставить отзыв\n/review — все отзывы\n\n"
            "Если будут вопросы или проблемы, пиши в лс @TwixerArtist"
        ),
        "error_sending_file": "❌ Ошибка отправки.",
        "grade_prompt": "⭐ Оцените файл (1-5):",
        "skip_comment_button": "⏩ Пропустить",
        "comment_prompt": "✍️ Напишите отзыв (<b>до 200 симв.</b>):",
        "rating_saved": "✅ Оценка сохранена!",
        "comment_saved": "✅ Отзыв сохранен!",
        "no_reviews": "💬 Отзывов пока нет.",
        "latest_reviews_header": "💬 <b>Последние отзывы (Страница {}/{}):</b>\n\n",
        "review_entry": "👤 @{} | {}⭐\n📝 {}\n📅 {}\n\n",
        "prev_button": "⬅️ Назад",
        "next_button": "Вперед ➡️",
        "all_reviews_header": "💬 <b>Все отзывы:</b>\n\n",
        "stats_header": "📊 <b>ПОЛНАЯ СТАТИСТИКА БОТА</b>\n\n",
        "users_base_header": "🌎 <b>Пользователей в базе:</b>\n",
        "total_users": "└ За все время: <code>{}</code>\n",
        "users_year": "└ За год: <code>{}</code>\n",
        "users_month": "└ За месяц: <code>{}</code>\n",
        "unique_downloads": "📥 <b>Уникальных скачиваний:</b> <code>{}</code>\n\n",
        "recent_downloaders_header": "📋 <b>Последние 15 скачавших:</b>\n",
        "recent_downloader_entry": "• @{} | {} | Саб: {}\n",
        "set_file_prompt": "Отправьте файл.",
        "file_saved": "✅ Сохранено!",
        "bot_turned_on": "✅ ВКЛ.",
        "bot_turned_off": "❌ ВЫКЛ.",
        "reviews_wiped": "✅ <b>Все отзывы удалены!</b>",
        "downloads_wiped": "✅ <b>История скачиваний очищена!</b> (Юзеры могут скачать снова)",
        "users_wiped": "⚠️ <b>База пользователей полностью очищена!</b>",
        "error_sending_file_to_admin": "❌ Ошибка отправки файла администратору.",
        "not_downloaded_yet": "❌ Вы не можете оставить отзыв, так как еще не скачали файл.",
        "comment_too_long": "❌ Текст слишком длинный!"
    },
    "en": {
        "welcome": "Hello! Please choose your language:\nПривет! Пожалуйста, выбери язык:",
        "lang_select_ru": "🇷🇺 Russian",
        "lang_select_en": "🇬🇧 English",
        "choose_lang_prompt": "Choose language:",
        "message_after_lang_select": "Language set. Now everything will be in {} language.",
        "download_channel_prompt": "Subscribe to the channel and press the button!\n\nIf you have questions or problems, write to @TwixerArtist",
        "subscribe_button": "Subscribe to TWIXER",
        "download_button": "📥 Download File",
        "download_queue_msg": "⏳ <b>You are in the queue...</b> Sending in 4 sec.",
        "download_limit_exceeded": "❌ Bot is temporarily blocked.",
        "bot_off_msg": "❌ Bot is temporarily disabled.",
        "subscribe_needed": "❌ Subscribe to the channel!",
        "file_not_uploaded": "❌ File not uploaded.",
        "wait_msg": "⏳ Wait {}m {}s.",
        "download_complete_caption": (
            "🥁 <b>Your file is ready!</b>\n"
            "📈 Downloads: {}\n"
            "⭐️ Rating: {}/5\n\n"
            "/grade — leave a review\n/review — all reviews\n\n"
            "If you have questions or problems, write to @TwixerArtist"
        ),
        "error_sending_file": "❌ Error sending file.",
        "grade_prompt": "⭐ Rate the file (1-5):",
        "skip_comment_button": "⏩ Skip",
        "comment_prompt": "✍️ Write your review (<b>up to 200 chars</b>):",
        "rating_saved": "✅ Rating saved!",
        "comment_saved": "✅ Review saved!",
        "no_reviews": "💬 No reviews yet.",
        "latest_reviews_header": "💬 <b>Latest reviews (Page {}/{}):</b>\n\n",
        "review_entry": "👤 @{} | {}⭐\n📝 {}\n📅 {}\n\n",
        "prev_button": "⬅️ Back",
        "next_button": "Next ➡️",
        "all_reviews_header": "💬 <b>All reviews:</b>\n\n",
        "stats_header": "📊 <b>FULL BOT STATISTICS</b>\n\n",
        "users_base_header": "🌎 <b>Users in database:</b>\n",
        "total_users": "└ All time: <code>{}</code>\n",
        "users_year": "└ Last year: <code>{}</code>\n",
        "users_month": "└ Last month: <code>{}</code>\n",
        "unique_downloads": "📥 <b>Unique downloads:</b> <code>{}</code>\n\n",
        "recent_downloaders_header": "📋 <b>Last 15 downloaders:</b>\n",
        "recent_downloader_entry": "• @{} | {} | Sub: {}\n",
        "set_file_prompt": "Send the file.",
        "file_saved": "✅ Saved!",
        "bot_turned_on": "✅ ON.",
        "bot_turned_off": "❌ OFF.",
        "reviews_wiped": "✅ <b>All reviews deleted!</b>",
        "downloads_wiped": "✅ <b>Download history cleared!</b> (Users can download again)",
        "users_wiped": "⚠️ <b>User database completely cleared!</b>",
        "error_sending_file_to_admin": "❌ Error sending file to admin.",
        "not_downloaded_yet": "❌ You cannot leave a review as you have not downloaded the file yet.",
        "comment_too_long": "❌ Text is too long!"
    }
}

# --- КОНТЕКСТ ДЛЯ ВЫБОРА ЯЗЫКА ---
class LanguageState(StatesGroup):
    waiting_for_language = State()

# --- СОСТОЯНИЯ АДМИНА ---
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
                       last_download_time REAL DEFAULT 0, join_date TEXT, language TEXT DEFAULT 'ru')''') # Добавлено поле language
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- ФУНКЦИИ ---

async def get_user_language(user_id):
    cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 'ru' # По умолчанию русский

def get_text(user_id, key):
    lang = get_user_language(user_id)
    return LANGUAGES[lang].get(key, f"Translation not found for key: {key} in {lang}")

async def is_subscribed(user_id):
    if user_id == ADMIN_ID: return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"Error checking subscription for user {user_id}: {e}")
        return False

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

# --- ПАГИНАЦИЯ ---

async def show_reviews_page(message_or_call, page: int, user_id):
    limit = 5
    offset = page * limit
    cursor.execute("SELECT COUNT(*) FROM reviews")
    total_reviews = cursor.fetchone()[0]
    total_pages = (total_reviews + limit - 1) // limit
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()

    if not rows:
        text = get_text(user_id, "no_reviews")
        kb = None
    else:
        text = get_text(user_id, "latest_reviews_header").format(page + 1, max(1, total_pages))
        for r in rows:
            text += get_text(user_id, "review_entry").format(r[0], r[1], r[2], r[3])
        nav_btns = []
        if page > 0: nav_btns.append(InlineKeyboardButton(text=get_text(user_id, "prev_button"), callback_data=f"rvp_{page-1}"))
        if page < total_pages - 1: nav_btns.append(InlineKeyboardButton(text=get_text(user_id, "next_button"), callback_data=f"rvp_{page+1}"))
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_btns]) if nav_btns else None

    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=kb)
    else:
        await message_or_call.message.edit_text(text, reply_markup=kb)

# --- МЕНЮ ---

async def set_main_menu(bot: Bot):
    user_cmds_ru = [
        BotCommand(command="start", description="Скачать файл"),
        BotCommand(command="grade", description="Оставить отзыв"),
        BotCommand(command="review", description="Все отзывы"),
        BotCommand(command="help", description="Помощь")
    ]
    user_cmds_en = [
        BotCommand(command="start", description="Download file"),
        BotCommand(command="grade", description="Leave a review"),
        BotCommand(command="review", description="All reviews"),
        BotCommand(command="help", description="Help")
    ]

    admin_cmds = user_cmds_ru + [ # Используем русские описания для админа, чтобы не усложнять
        BotCommand(command="full_stats", description="Статистика"),
        BotCommand(command="set_file", description="Загрузить файл"),
        BotCommand(command="on", description="Включить"),
        BotCommand(command="off", description="Выключить"),
        BotCommand(command="wipe_reviews", description="Удалить отзывы"),
        BotCommand(command="wipe_downloads", description="Сбросить скачивания"),
        BotCommand(command="wipe_users", description="Очистить всех юзеров")
    ]

    await bot.set_my_commands(user_cmds_ru, scope=BotCommandScopeDefault(), language_code="ru")
    await bot.set_my_commands(user_cmds_en, scope=BotCommandScopeDefault(), language_code="en")
    await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    user_id = message.from_user.id
    lang = await get_user_language(user_id)

    if get_setting("bot_status") == "off" and user_id != ADMIN_ID:
        await message.answer(LANGUAGES[lang]["bot_off_msg"])
        return

    # Проверка, выбрал ли пользователь язык
    cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    user_lang_setting = cursor.fetchone()

    if not user_lang_setting or user_lang_setting[0] is None:
        # Если язык не выбран, просим выбрать
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=LANGUAGES["ru"]["lang_select_ru"], callback_data="lang_ru")],
            [InlineKeyboardButton(text=LANGUAGES["en"]["lang_select_en"], callback_data="lang_en")]
        ])
        await message.answer(LANGUAGES["ru"]["welcome"], reply_markup=kb) # Приветствие на русском, но с выбором
        await state.set_state(LanguageState.waiting_for_language)
    else:
        # Если язык выбран, продолжаем как обычно
        now_date = datetime.now(MSK).strftime("%Y-%m-%d")
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, join_date, language) VALUES (?, ?, ?, ?, ?)",
                       (user_id, message.from_user.username, message.from_user.full_name, now_date, lang))
        conn.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(user_id, "subscribe_button"), url=CHANNEL_URL)],
            [InlineKeyboardButton(text=get_text(user_id, "download_button"), callback_data="dl_start")]
        ])
        await message.answer(get_text(user_id, "download_channel_prompt"), reply_markup=kb)

@dp.callback_query(LanguageState.waiting_for_language)
async def process_language_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang_code = callback.data.split("_")[1] # 'ru' or 'en'

    if lang_code not in LANGUAGES:
        await callback.answer("Invalid language selection.", show_alert=True)
        return

    cursor.execute("UPDATE users SET language=? WHERE user_id=?", (lang_code, user_id))
    conn.commit()

    await callback.message.edit_text(LANGUAGES[lang_code]["message_after_lang_select"].format(LANGUAGES[lang_code]["lang_select_"+lang_code].split(" ")[1]))
    await callback.answer()
    await state.clear()

    # После выбора языка, показываем главное меню
    now_date = datetime.now(MSK).strftime("%Y-%m-%d")
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, join_date, language) VALUES (?, ?, ?, ?, ?)",
                   (user_id, callback.from_user.username, callback.from_user.full_name, now_date, lang_code))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(user_id, "subscribe_button"), url=CHANNEL_URL)],
        [InlineKeyboardButton(text=get_text(user_id, "download_button"), callback_data="dl_start")]
    ])
    await callback.message.answer(get_text(user_id, "download_channel_prompt"), reply_markup=kb)


@dp.callback_query(F.data == "dl_start")
async def process_dl(callback: types.CallbackQuery):
    global DOWNLOAD_ATTEMPTS
    now_ts = time.time()
    DOWNLOAD_ATTEMPTS.append(now_ts)
    DOWNLOAD_ATTEMPTS = [t for t in DOWNLOAD_ATTEMPTS if now_ts - t < 1]

    user_id = callback.from_user.id
    lang = await get_user_language(user_id)

    if len(DOWNLOAD_ATTEMPTS) > 50:
        cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit()
        await bot.send_message(ADMIN_ID, "🚨 <b>ВНИМАНИЕ!</b> Бот выключен из-за накрутки!")
        await callback.answer(LANGUAGES[lang]["download_limit_exceeded"], show_alert=True)
        return

    if get_setting("bot_status") == "off" and user_id != ADMIN_ID:
        await callback.answer(LANGUAGES[lang]["bot_off_msg"], show_alert=True)
        return

    if not await is_subscribed(user_id):
        await callback.answer(LANGUAGES[lang]["subscribe_needed"], show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer(LANGUAGES[lang]["file_not_uploaded"], show_alert=True)
        return

    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    if u_data and u_data[0] and now_ts - float(u_data[0]) < 300:
        left = int(300 - (now_ts - float(u_data[0])))
        await callback.answer(LANGUAGES[lang]["wait_msg"].format(left // 60, left % 60), show_alert=True)
        return

    msg = await callback.message.edit_text(LANGUAGES[lang]["download_queue_msg"])
    async with download_queue:
        await asyncio.sleep(4)
        date_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?",
                       (now_ts, date_str, user_id))
        conn.commit()
        dls, avg = get_stats_data()
        caption = get_text(user_id, "download_complete_caption").format(dls, avg)
        try:
            await bot.send_document(user_id, file_id, caption=caption)
            await msg.delete()
        except Exception as e:
            print(f"Error sending document to {user_id}: {e}")
            await callback.message.answer(LANGUAGES[lang]["error_sending_file"])
    await callback.answer()

# --- ОТЗЫВЫ ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_language(user_id)

    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    if not res or res[0] == 0:
        await message.answer(get_text(user_id, "not_downloaded_yet"))
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rt_{i}") for i in range(1, 6)]])
    await message.answer(get_text(user_id, "grade_prompt"), reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rt_"), ReviewStates.waiting_for_rating)
async def rate_sel(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    lang = await get_user_language(user_id)
    await state.update_data(rating=int(call.data.split("_")[1]))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip_comment_button"), callback_data="sk_comment")]])
    await call.message.edit_text(get_text(user_id, "comment_prompt"), reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.callback_query(F.data == "sk_comment", ReviewStates.waiting_for_comment)
async def sk_comment(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    lang = await get_user_language(user_id)
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)", (call.from_user.id, call.from_user.username or "User", data['rating'], "Без комментария", now))
    conn.commit()
    await call.message.edit_text(get_text(user_id, "rating_saved"))
    await state.clear()

@dp.message(ReviewStates.waiting_for_comment)
async def save_rev(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_language(user_id)

    if len(message.text) > 200:
        await message.answer(get_text(user_id, "comment_too_long"))
        return
    data = await state.get_data()
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)", (message.from_user.id, message.from_user.username or "User", data['rating'], message.text, now))
    conn.commit()
    await message.answer(get_text(user_id, "comment_saved"))
    await state.clear()

@dp.message(Command("review"))
async def cmd_reviews(message: types.Message):
    user_id = message.from_user.id
    await show_reviews_page(message, 0, user_id)

@dp.callback_query(F.data.startswith("rvp_"))
async def process_rev_page(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await show_reviews_page(callback, int(callback.data.split("_")[1]), user_id)
    await callback.answer()

# --- АДМИН КОМАНДЫ ОЧИСТКИ ---

@dp.message(Command("wipe_reviews"), F.from_user.id == ADMIN_ID)
async def wipe_reviews_cmd(message: types.Message):
    cursor.execute("DELETE FROM reviews"); conn.commit()
    await message.answer(get_text(ADMIN_ID, "reviews_wiped"))

@dp.message(Command("wipe_downloads"), F.from_user.id == ADMIN_ID)
async def wipe_downloads_cmd(message: types.Message):
    cursor.execute("UPDATE users SET received_file=0, date_received=NULL, last_download_time=0")
    conn.commit()
    await message.answer(get_text(ADMIN_ID, "downloads_wiped"))

@dp.message(Command("wipe_users"), F.from_user.id == ADMIN_ID)
async def wipe_users_cmd(message: types.Message):
    cursor.execute("DELETE FROM users"); conn.commit()
    await message.answer(get_text(ADMIN_ID, "users_wiped"))

@dp.message(Command("full_stats"), F.from_user.id == ADMIN_ID)
async def full_stats_cmd(message: types.Message):
    cursor.execute("SELECT COUNT(*) FROM users"); total = cursor.fetchone()[0]
    y_ago = (datetime.now(MSK) - timedelta(days=365)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (y_ago,)); t_y = cursor.fetchone()[0]
    m_ago = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (m_ago,)); t_m = cursor.fetchone()[0]
    dls, _ = get_stats_data()
    res = get_text(ADMIN_ID, "stats_header")
    res += get_text(ADMIN_ID, "users_base_header")
    res += get_text(ADMIN_ID, "total_users").format(total)
    res += get_text(ADMIN_ID, "users_year").format(t_y)
    res += get_text(ADMIN_ID, "users_month").format(t_m)
    res += get_text(ADMIN_ID, "unique_downloads").format(dls)
    res += get_text(ADMIN_ID, "recent_downloaders_header")
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1 ORDER BY date_received DESC LIMIT 15")
    for r in cursor.fetchall():
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        res += get_text(ADMIN_ID, "recent_downloader_entry").format(r[1], r[2], sub)
    await message.answer(res)

@dp.message(Command("set_file"), F.from_user.id == ADMIN_ID)
async def set_f(message: types.Message, state: FSMContext):
    await message.answer(get_text(ADMIN_ID, "set_file_prompt"))
    await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def file_up(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE settings SET value=? WHERE key='file_id'", (message.document.file_id,))
    conn.commit()
    await message.answer(get_text(ADMIN_ID, "file_saved"))
    await state.clear()

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def b_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'"); conn.commit()
    await message.answer(get_text(ADMIN_ID, "bot_turned_on"))

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def b_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'"); conn.commit()
    await message.answer(get_text(ADMIN_ID, "bot_turned_off"))

async def main():
    await set_main_menu(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
