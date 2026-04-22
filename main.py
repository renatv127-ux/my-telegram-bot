
import os
import asyncio
import sqlite3
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ (FSM) ---
class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                   received_file INTEGER DEFAULT 0, date_received TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                  (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                  (key TEXT PRIMARY KEY, value TEXT)''')

cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
conn.commit()

# --- ФУНКЦИИ ---
async def is_subscribed(user_id):
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except: return False

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    avg, count = cursor.fetchone()
    if not avg: return 0, 0
    return round(avg, 1), count

# --- ХЕНДЛЕРЫ ОТЗЫВОВ (/grade) ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    
    if not res or res[0] == 0:
        await message.answer("❌ Ты еще не получил файл. Сначала скачай его!")
        return

    # Проверка существующего отзыва
    cursor.execute("SELECT rating FROM reviews WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        text = "🔄 Ты уже оставлял отзыв. Давай обновим его!\n\nВыбери новую оценку от 1 до 5:"
    else:
        text = "⭐ Оцени драм кит! Выбери оценку от 1 до 5:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]
    ])
    await message.answer(text, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_comment")]
    ])
    await callback.message.edit_text(f"Твоя оценка: {rating}/5\n\nТеперь напиши краткий отзыв (текстом) или нажми кнопку 'Пропустить':", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)
    await callback.answer()

@dp.message(ReviewStates.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    save_review(message.from_user.id, message.from_user.username or message.from_user.first_name, data['rating'], message.text)
    await message.answer("✅ Спасибо! Твой отзыв сохранен.")
    await state.clear()

@dp.callback_query(F.data == "skip_comment", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    save_review(callback.from_user.id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без текста")
    await callback.message.edit_text("✅ Спасибо! Оценка сохранена.")
    await state.clear()
    await callback.answer()

def save_review(user_id, username, rating, comment):
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, username, rating, comment, now))
    conn.commit()

@dp.message(Command("review"))
async def cmd_review(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    
    text = f"⭐ Средний рейтинг: {avg}/5 ({count} отзывов)\n\nПоследние отзывы:\n"
    for r in rows:
        text += f"👤 @{r[0]} — Оценка: {r[1]}/5\n💬 {r[2]}\n\n"
    await message.answer(text if rows else "Отзывов пока нет.")

# --- ОСНОВНАЯ ЛОГИКА ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет!\nДля получения Драм кита надо быть подписанным на канал TWIXER!", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        file_id = cursor.fetchone()[0] if cursor.fetchone() else None
        
        if not file_id:
            await callback.message.answer("Админ еще не загрузил файл.")
            return

        # Увеличиваем счетчик скачиваний
        cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
        now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, date_received=? WHERE user_id=?", (now, callback.from_user.id))
        conn.commit()

        cursor.execute("SELECT value FROM settings WHERE key='downloads'")
        total = cursor.fetchone()[0]

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оценить файл", callback_data="go_grade")]
        ])

        await callback.message.answer_document(file_id, caption=f"Драм кит у тебя! 🥁\nОбщее кол-во скачиваний: {total}\n\nБудем рады твоему отзыву: /grade", reply_markup=kb)
        await callback.answer()
    else:
        await callback.message.answer("К сожалению вы не подписаны на канал.")
        await callback.answer()

@dp.callback_query(F.data == "go_grade")
async def go_grade_btn(callback: types.CallbackQuery, state: FSMContext):
    await cmd_grade(callback.message, state)
    await callback.answer()

# --- АДМИН КОМАНДЫ ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    text = ("🔧 **Команды админа:**\n\n"
            "/FileDK — Изменить файл для выдачи\n"
            "/Stata — Статистика по людям (кто скачал и статус подписки)\n"
            "/admin — Этот список")
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("FileDK"), F.from_user.id == ADMIN_ID)
async def file_dk_cmd(message: types.Message):
    await message.answer("Пришли новый файл драм-кита.")

@dp.message(F.document, F.from_user.id == ADMIN_ID)
async def save_new_file(message: types.Message):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
    conn.commit()
    await message.answer("✅ Файл обновлен!")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def get_stata(message: types.Message):
    cursor.execute("SELECT user_id, username, full_name, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    
    if not rows:
        await message.answer("Файл еще никто не скачивал.")
        return

    report = "📊 **Люди, получившие файл:**\n\n"
    for row in rows:
        uid, uname, fname, date = row
        sub = await is_subscribed(uid)
        status = "✅ подписан" if sub else "❌ отписался"
        name = f"@{uname}" if uname else fname
        report += f"{name} | {status} | {date}\n"
    
    await message.answer(report[:4000], parse_mode="Markdown")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
