
import os
import asyncio
import sqlite3
import logging
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')
CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Очередь: одновременно пропускаем 2-х человек к скачиванию
download_queue = asyncio.Semaphore(2)

# --- СОСТОЯНИЯ (FSM) ---
class ReviewStates(StatesGroup):
    waiting_for_comment = State()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                   received_file INTEGER DEFAULT 0, date_received TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                  (rev_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                   username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                  (key TEXT PRIMARY KEY, value TEXT)''')

cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
conn.commit()

# --- ФУНКЦИИ ---
async def is_subscribed(user_id):
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except Exception: return False

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(rev_id) FROM reviews")
    avg, count = cursor.fetchone()
    if not avg: return "Нет оценок", 0
    return round(avg, 1), count

def get_bot_status():
    cursor.execute("SELECT value FROM settings WHERE key='bot_status'")
    return cursor.fetchone()[0]

# --- ЛОГИКА ОТЗЫВОВ ---

async def ask_for_review(user_id):
    """Опрос через 10 минут"""
    await asyncio.sleep(600) # 10 минут
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rate_{i}") for i in range(1, 6)]
    ])
    try:
        await bot.send_message(user_id, "Привет! Ты недавно скачал драм кит. Оцени его от 1 до 5:", reply_markup=kb)
    except Exception: pass

@dp.callback_query(F.data.startswith("rate_"))
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)
    await callback.message.edit_text(f"Твоя оценка: {rating}/5 ⭐\nНапиши короткий отзыв (или напиши 'нет'):")
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def process_review_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    rating = data['rating']
    comment = message.text if message.text.lower() != 'нет' else "Без комментария"
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    
    cursor.execute("INSERT INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or message.from_user.first_name, rating, comment, now))
    conn.commit()
    await message.answer("Спасибо за твой отзыв! ❤️")
    await state.clear()

@dp.message(Command("review"))
async def show_reviews(message: types.Message):
    cursor.execute("SELECT username, rating, comment FROM reviews ORDER BY rev_id DESC LIMIT 10")
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Отзывов пока нет.")
        return
    text = "💬 **Последние отзывы:**\n\n"
    for r in rows:
        text += f"👤 @{r[0]}\n⭐ Оценка: {r[1]}/5\n📝 {r[2]}\n\n"
    await message.answer(text, parse_mode="Markdown")

# --- ОСНОВНЫЕ ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_bot_status() == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("Данный бот не работает, админ выключил его.")
        return
    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет, {message.from_user.first_name}!\nПодпишись на канал TWIXER, чтобы получить файл." + CONTACT_INFO, reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    if get_bot_status() == "off" and callback.from_user.id != ADMIN_ID:
        await callback.answer("Бот временно отключен.", show_alert=True)
        return

    if await is_subscribed(callback.from_user.id):
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        res = cursor.fetchone()
        file_id = res[0] if res else None
        if not file_id:
            await callback.answer("Файл еще не загружен.", show_alert=True)
            return

        # --- СИСТЕМА ОЧЕРЕДИ ---
        await callback.message.edit_text("⏳ Вы в очереди... Файл будет отправлен через несколько секунд.")
        
        async with download_queue:
            await asyncio.sleep(4) # Задержка сервера
            
            cursor.execute("SELECT received_file FROM users WHERE user_id=?", (callback.from_user.id,))
            is_new = cursor.fetchone()[0] == 0
            if is_new:
                cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
                cursor.execute("UPDATE users SET received_file=1, date_received=? WHERE user_id=?", 
                               (datetime.now(MSK).strftime("%d.%m.%Y %H:%M"), callback.from_user.id))
                conn.commit()
                # Планируем отзыв через 10 минут
                asyncio.create_task(ask_for_review(callback.from_user.id))

            avg, count = get_average_rating()
            caption = (f"🔥 Драм кит успешно получен!\n\n"
                       f"📊 Рейтинг файла: {avg} ⭐ (Отзывов: {count})\n"
                       f"Все отзывы: /review" + CONTACT_INFO)
            
            try:
                await callback.message.answer_document(file_id, caption=caption)
                await callback.message.delete()
            except Exception:
                await callback.message.answer("Ошибка при отправке.")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
        ])
        await callback.message.answer("К сожалению, вы не подписаны!" + CONTACT_INFO, reply_markup=kb)
        await callback.answer()

# --- АДМИН ПАНЕЛЬ ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Включить бота", callback_data="set_on"),
         InlineKeyboardButton(text="Выключить бота", callback_data="set_off")],
        [InlineKeyboardButton(text="Список отзывов (для удаления)", callback_data="admin_rev")],
        [InlineKeyboardButton(text="💾 Выгрузить Базу", callback_data="db_dump")]
    ])
    await message.answer("⚙️ Админ-панель:", reply_markup=kb)

@dp.callback_query(F.from_user.id == ADMIN_ID)
async def admin_callbacks(callback: types.CallbackQuery):
    if callback.data == "set_on":
        cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
        await callback.answer("Бот включен")
    elif callback.data == "set_off":
        cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
        await callback.answer("Бот выключен")
    elif callback.data == "db_dump":
        await callback.message.answer_document(FSInputFile("bot_data.db"), caption="Бэкап базы")
    elif callback.data == "admin_rev":
        cursor.execute("SELECT rev_id, username, rating FROM reviews ORDER BY rev_id DESC LIMIT 15")
        rows = cursor.fetchall()
        if not rows:
            await callback.message.answer("Отзывов нет.")
            return
        msg = "Чтобы удалить, пиши: `/delete_review ID`\n\n"
        for r in rows:
            msg += f"🆔 `{r[0]}` | @{r[1]} | {r[2]}/5\n"
        await callback.message.answer(msg, parse_mode="Markdown")
    conn.commit()
    await callback.answer()

@dp.message(Command("delete_review"), F.from_user.id == ADMIN_ID)
async def delete_rev(message: types.Message):
    try:
        rid = int(message.text.split()[1])
        cursor.execute("DELETE FROM reviews WHERE rev_id=?", (rid,))
        conn.commit()
        await message.answer(f"✅ Отзыв {rid} удален.")
    except: await message.answer("Пример: `/delete_review 5`")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def sms_broadcast(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt: return
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    for u in users:
        try:
            await bot.send_message(u[0], txt)
            await asyncio.sleep(0.05)
        except: continue
    await message.answer("Рассылка завершена.")

@dp.message(Command("FileDK"), F.from_user.id == ADMIN_ID)
async def file_dk(message: types.Message):
    await message.answer("Пришли файл (документ).")

@dp.message(F.document, F.from_user.id == ADMIN_ID)
async def save_doc(message: types.Message):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
    conn.commit()
    await message.answer("✅ Файл сохранен!")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def stata_cmd(message: types.Message):
    cursor.execute("SELECT username, date_received, user_id FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Скачиваний нет.")
        return
    res = "📊 Список скачавших:\n"
    for r in rows:
        sub = "✅" if await is_subscribed(r[2]) else "❌"
        res += f"{sub} @{r[0]} | {r[1]}\n"
    await message.answer(res[:4000])

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
