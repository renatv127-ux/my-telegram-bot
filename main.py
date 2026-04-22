
import os
import asyncio
import sqlite3
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')

# Твоя подпись, которая будет в конце сообщений
CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ (FSM) ДЛЯ ОТЗЫВОВ ---
class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                   received_file INTEGER DEFAULT 0, date_received TEXT)''')
# Таблица отзывов: один пользователь — одна строка (UNIQUE user_id)
cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                  (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                  (key TEXT PRIMARY KEY, value TEXT)''')

cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
conn.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def is_subscribed(user_id):
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except: return False

def get_bot_status():
    cursor.execute("SELECT value FROM settings WHERE key='bot_status'")
    res = cursor.fetchone()
    return res[0] if res else "on"

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    avg, count = cursor.fetchone()
    if not avg: return 0, 0
    return round(avg, 1), count

# --- ЛОГИКА ОТЗЫВОВ (ТВОЯ ОБНОВЛЕННАЯ СИСТЕМА) ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем, скачивал ли пользователь файл
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    if not res or res[0] == 0:
        await message.answer("❌ Ты еще не скачивал файл. Сначала получи его через /start." + CONTACT_INFO)
        return
    
    # Проверяем существующий отзыв
    cursor.execute("SELECT rating, comment FROM reviews WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        text = f"🔄 Ты уже оставлял отзыв:\nОценка: {existing[0]}/5\nТекст: {existing[1]}\n\nВыбери новую оценку, чтобы изменить его:"
    else:
        text = "⭐ Оцени драм кит! Выбери число от 1 до 5:"

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
        [InlineKeyboardButton(text="➡️ Пропустить описание", callback_data="skip_comment")]
    ])
    await callback.message.edit_text(f"Твоя оценка: {rating}/5\n\nНапиши текст отзыва или нажми кнопку 'Пропустить':", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)
    await callback.answer()

@dp.message(ReviewStates.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    save_review_db(message.from_user.id, message.from_user.username or message.from_user.first_name, data['rating'], message.text)
    await message.answer("✅ Твой отзыв успешно обновлен/сохранен!" + CONTACT_INFO)
    await state.clear()

@dp.callback_query(F.data == "skip_comment", ReviewStates.waiting_for_comment)
async def skip_comment_handler(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    save_review_db(callback.from_user.id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без описания")
    await callback.message.edit_text("✅ Оценка сохранена!" + CONTACT_INFO)
    await state.clear()
    await callback.answer()

def save_review_db(uid, uname, rat, comm):
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (uid, uname, rat, comm, now))
    conn.commit()

@dp.message(Command("review"))
async def cmd_view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    
    res = f"⭐ Средний рейтинг: {avg}/5 (Всего отзывов: {count})\n\n"
    if not rows:
        res += "Отзывов пока нет."
    else:
        for r in rows:
            res += f"👤 @{r[0]} | {r[1]}/5\n📝 {r[2]}\n📅 {r[3]}\n\n"
            
    await message.answer(res + CONTACT_INFO)

# --- ОСНОВНЫЕ КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_bot_status() == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором." + CONTACT_INFO)
        return
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал, чтобы скачать драм кит!" + CONTACT_INFO, reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_sub_check(callback: types.CallbackQuery):
    if get_bot_status() == "off" and callback.from_user.id != ADMIN_ID:
        await callback.answer("Бот выключен.", show_alert=True); return

    if await is_subscribed(callback.from_user.id):
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        file_res = cursor.fetchone()
        if not file_res:
            await callback.answer("Файл не загружен.", show_alert=True); return
        
        file_id = file_res[0]
        
        # Обновляем статистику
        cursor.execute("SELECT received_file FROM users WHERE user_id=?", (callback.from_user.id,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
            now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
            cursor.execute("UPDATE users SET received_file=1, date_received=? WHERE user_id=?", 
                           (now, callback.from_user.id))
            conn.commit()

        cursor.execute("SELECT value FROM settings WHERE key='downloads'")
        total = cursor.fetchone()[0]
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оценить драм кит (/grade)", callback_data="dummy_btn")]
        ])
        
        await callback.message.answer_document(file_id, 
            caption=f"🔥 Драм кит успешно получен!\nВсего скачиваний: {total}\n\nБудем рады твоему отзыву: /grade" + CONTACT_INFO,
            reply_markup=kb)
        await callback.answer()
    else:
        await callback.message.answer("К сожалению, вы не подписаны на канал!" + CONTACT_INFO)
        await callback.answer()

# --- КОМАНДЫ АДМИНИСТРАТОРА ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_menu_cmd(message: types.Message):
    admin_msg = (
        "⚙️ **АДМИН-ПАНЕЛЬ**\n\n"
        "👑 **Управление:**\n"
        "/on | /off — Включить/Выключить бота\n"
        "/FileDK — Загрузить новый файл\n"
        "/Stata — Статистика скачавших и подписки\n"
        "/sms [текст] — Рассылка всем\n"
        "/delete_review [ID] — Удалить отзыв (ID пользователя)\n\n"
        "👤 **Команды пользователя:**\n"
        "/start — Получить файл\n"
        "/grade — Оставить отзыв\n"
        "/review — Посмотреть отзывы"
    )
    await message.answer(admin_msg, parse_mode="Markdown")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
    conn.commit()
    await message.answer("✅ Бот включен для всех пользователей.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
    conn.commit()
    await message.answer("❌ Бот выключен для всех (кроме админа).")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def admin_stata(message: types.Message):
    cursor.execute("SELECT user_id, username, full_name, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Файл еще никто не скачивал."); return
    
    res = "📊 **Список получивших файл:**\n\n"
    for r in rows:
        sub = await is_subscribed(r[0])
        status = "✅ Подписан" if sub else "❌ Отписался"
        name = f"@{r[1]}" if r[1] else r[2]
        res += f"{name} | {status} | {r[3]}\n"
    
    await message.answer(res[:4000])

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    text = message.text.replace("/sms", "").strip()
    if not text:
        await message.answer("Используй: `/sms текст`")
        return
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    count = 0
    for u in users:
        try:
            await bot.send_message(u[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except: continue
    await message.answer(f"✅ Рассылка завершена. Доставлено: {count}")

@dp.message(Command("delete_review"), F.from_user.id == ADMIN_ID)
async def admin_del_review(message: types.Message):
    try:
        rid = int(message.text.split()[1])
        cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
        conn.commit()
        await message.answer(f"✅ Отзыв пользователя {rid} удален.")
    except:
        await message.answer("Укажите ID пользователя. Пример: `/delete_review 1753037099`")

@dp.message(Command("FileDK"), F.from_user.id == ADMIN_ID)
async def admin_file_dk(message: types.Message):
    await message.answer("Пришли файл (документ), который хочешь раздавать.")

@dp.message(F.document, F.from_user.id == ADMIN_ID)
async def admin_save_file(message: types.Message):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
    conn.commit()
    await message.answer("✅ Файл успешно сохранен и готов к выдаче!")

@dp.callback_query(F.data == "dummy_btn")
async def dummy_btn_handler(callback: types.CallbackQuery):
    await callback.answer("Используй команду /grade для оценки!", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
