
import os
import asyncio
import sqlite3
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
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

CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

def db_init():
    # Создаем таблицы если их нет
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    
    # Проверка и добавление недостающих колонок (Repair)
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'last_download_time' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_download_time TEXT")
    if 'received_file' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN received_file INTEGER DEFAULT 0")
    
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    conn.commit()

db_init()

# --- ФУНКЦИИ ---
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

# --- ЛОГИКА ОТЗЫВОВ ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем статус в базе
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    
    # Если записи нет или статус 0
    if not res or res[0] == 0:
        await message.answer("❌ Сначала скачай драм кит через кнопку в /start!" + CONTACT_INFO)
        return

    cursor.execute("SELECT rating, comment, date FROM reviews WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        review_date_str = existing[2]
        try:
            review_dt = datetime.strptime(review_date_str, "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if datetime.now(MSK) - review_dt > timedelta(hours=2):
                await message.answer("❌ Изменить отзыв можно только в течение 2-х часов после публикации первого отзыва!" + CONTACT_INFO)
                return
        except:
            pass
        text = f"🔄 Твой отзыв ({existing[0]}/5).\nВыбери новую оценку:"
    else:
        text = "⭐ Оцени драм кит! Выбери число от 1 до 5:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]
    ])
    await message.answer(text + CONTACT_INFO, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Пропустить описание", callback_data="skip_comment")]
    ])
    await callback.message.edit_text(f"Оценка {rating}/5 принята!\nНапиши комментарий или нажми 'Пропустить':" + CONTACT_INFO, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)
    await callback.answer()

@dp.message(ReviewStates.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    
    cursor.execute("SELECT date FROM reviews WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    date_to_save = row[0] if row else datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, message.from_user.username or message.from_user.first_name, data['rating'], message.text, date_to_save))
    conn.commit()
    await message.answer("✅ Отзыв успешно сохранен!" + CONTACT_INFO)
    await state.clear()

@dp.callback_query(F.data == "skip_comment", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    
    cursor.execute("SELECT date FROM reviews WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    date_to_save = row[0] if row else datetime.now(MSK).strftime("%d.%m.%Y %H:%M")

    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без описания", date_to_save))
    conn.commit()
    await callback.message.edit_text("✅ Оценка сохранена!" + CONTACT_INFO)
    await state.clear()
    await callback.answer()

@dp.message(Command("review"))
async def cmd_view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Всего отзывов: {count})\n\n"
    
    for r in rows:
        username, rating, comment, date, user_id = r
        # Только админ видит ID
        admin_info = f"🆔 ID: <code>{user_id}</code>\n" if message.from_user.id == ADMIN_ID else ""
        res += f"👤 @{username} | {rating}/5\n{admin_info}📝 {comment}\n📅 {date}\n\n"
    
    if not rows: res = "Отзывов пока нет."
    await message.answer(res + CONTACT_INFO, parse_mode="HTML")

# --- ОСНОВНЫЕ КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_bot_status() == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором." + CONTACT_INFO)
        return
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, received_file) VALUES (?, ?, ?, 0)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer("Привет! Чтобы скачать драм кит, подпишись на наш канал!" + CONTACT_INFO, reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if await is_subscribed(user_id):
        # 1. Проверяем файл
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        file_res = cursor.fetchone()
        if not file_res or not file_res[0]:
            await callback.answer("❌ Ошибка: Файл еще не загружен админом через /FileDK", show_alert=True)
            return

        # 2. Проверяем кулдаун и статус
        cursor.execute("SELECT received_file, last_download_time FROM users WHERE user_id=?", (user_id,))
        user_data = cursor.fetchone()
        
        now_dt = datetime.now(MSK)
        now_str = now_dt.strftime("%d.%m.%Y %H:%M")
        
        if user_data and user_data[1]: # Если уже скачивал
            try:
                last_dt = datetime.strptime(user_data[1], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
                if now_dt - last_dt < timedelta(minutes=15):
                    rem = (timedelta(minutes=15) - (now_dt - last_dt))
                    await callback.answer(f"❌ Подожди {int(rem.total_seconds() // 60)} мин. перед повторным скачиванием!", show_alert=True)
                    return
            except:
                pass

        # 3. ОБЯЗАТЕЛЬНОЕ ОБНОВЛЕНИЕ СТАТУСА ПЕРЕД ОТПРАВКОЙ
        # Сначала считаем общее количество скачиваний если это первый раз
        if not user_data or user_data[0] == 0:
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")

        # Обновляем данные юзера (ставим статус 1)
        cursor.execute('''INSERT OR REPLACE INTO users 
                          (user_id, username, full_name, received_file, last_download_time, date_received) 
                          VALUES (?, ?, ?, 1, ?, IFNULL((SELECT date_received FROM users WHERE user_id=?), ?))''',
                       (user_id, callback.from_user.username, callback.from_user.first_name, 
                        now_str, user_id, now_str))
        conn.commit()
        
        # 4. Отправка
        try:
            await callback.message.delete()
        except:
            pass 
        
        cursor.execute("SELECT value FROM settings WHERE key='downloads'")
        total = cursor.fetchone()[0]
        avg, count = get_average_rating()
        
        caption_text = (
            f"🥁 <b>Файл готов!</b>\n"
            f"📈 Всего скачиваний: {total}\n"
            f"⭐ Рейтинг: {avg}/5 (отзывов: {count})\n\n"
            f"/grade — написать отзыв\n"
            f"/review — отзывы"
            f"{CONTACT_INFO}"
        )
        
        await callback.message.answer_document(file_res[0], caption=caption_text, parse_mode="HTML")
        await callback.answer()
    else:
        await callback.answer("❌ Ты не подписан на канал!", show_alert=True)

# --- АДМИН-ПАНЕЛЬ ---

@dp.message(Command("admin"))
async def admin_menu(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        f_exists = "✅ Загружен" if cursor.fetchone() else "❌ НЕ ЗАГРУЖЕН"
        
        msg = (
            f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
            f"Статус файла: {f_exists}\n\n"
            f"/on | /off — Вкл/Выкл бота\n"
            f"/FileDK — Обновить файл\n"
            f"/Stata — Список скачавших\n"
            f"/sms [текст] — Рассылка всем\n"
            f"/delete_review [ID] — Удалить отзыв"
        )
        await message.answer(msg, parse_mode="HTML")

@dp.message(Command("on"))
async def bot_on(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
        conn.commit()
        await message.answer("✅ Бот включен.")

@dp.message(Command("off"))
async def bot_off(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
        conn.commit()
        await message.answer("❌ Бот выключен.")

@dp.message(Command("Stata"))
async def admin_stata(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("SELECT user_id, username, full_name, date_received FROM users WHERE received_file=1")
        rows = cursor.fetchall()
        if not rows:
            await message.answer("Файл еще никто не скачал."); return
        res = "<b>📊 Статистика скачиваний:</b>\n\n"
        for r in rows:
            name = f"@{r[1]}" if r[1] else r[2]
            res += f"{name} (ID: {r[0]}) | {r[3]}\n"
        await message.answer(res[:4000], parse_mode="HTML")

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        text = message.text.replace("/sms", "").strip()
        if not text:
            await message.answer("Введите текст. Пример: /sms Привет!")
            return
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        success_count = 0
        for u in users:
            try: 
                await bot.send_message(u[0], text)
                success_count += 1
                await asyncio.sleep(0.05)
            except: continue
        await message.answer(f"📢 Рассылка завершена!\n✅ Получили: {success_count} чел.")

@dp.message(Command("delete_review"))
async def del_rev(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        try:
            rid = int(message.text.split()[1])
            cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
            conn.commit()
            await message.answer(f"✅ Отзыв пользователя {rid} удален.")
        except: await message.answer("Используй: /delete_review [ID]")

@dp.message(Command("FileDK"))
async def ask_file(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Пришли файл драм-кита (отправь как ДОКУМЕНТ).")

@dp.message(F.document)
async def save_file(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        # Сохраняем file_id в настройки
        f_id = message.document.file_id
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (f_id,))
        conn.commit()
        await message.answer(f"✅ Файл успешно сохранен!\nID: <code>{f_id}</code>", parse_mode="HTML")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
