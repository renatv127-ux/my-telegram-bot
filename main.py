
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
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                   received_file INTEGER DEFAULT 0, date_received TEXT,
                   spam_count INTEGER DEFAULT 0, last_download_time TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                  (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                  (key TEXT PRIMARY KEY, value TEXT)''')

# Проверка на случай, если столбцы еще не созданы в существующей БД
try:
    cursor.execute("ALTER TABLE users ADD COLUMN spam_count INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE users ADD COLUMN last_download_time TEXT")
except:
    pass

cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
conn.commit()

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
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    
    if not res or res[0] == 0:
        await message.answer("❌ Сначала получи драм кит через /start!" + CONTACT_INFO)
        return

    # ПРОВЕРКА ОГРАНИЧЕНИЯ 2 ЧАСА НА ИЗМЕНЕНИЕ
    cursor.execute("SELECT rating, comment, date FROM reviews WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        review_date_str = existing[2]
        try:
            review_dt = datetime.strptime(review_date_str, "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if datetime.now(MSK) - review_dt > timedelta(hours=2):
                await message.answer("❌ Изменить отзыв можно только в течение 2 часов после его публикации!" + CONTACT_INFO)
                return
        except:
            pass
        text = f"🔄 У тебя уже есть отзыв ({existing[0]}/5).\nВыбери новую оценку для обновления (доступно в течение 2ч после первого отзыва):"
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
    await callback.message.edit_text(f"Оценка {rating}/5 принята!\nТеперь напиши текст отзыва или нажми 'Пропустить':" + CONTACT_INFO, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)
    await callback.answer()

@dp.message(ReviewStates.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Проверяем, есть ли старая дата, чтобы не обновлять её (для таймера 2 часа)
    cursor.execute("SELECT date FROM reviews WHERE user_id=?", (user_id,))
    existing_date = cursor.fetchone()
    date_to_save = existing_date[0] if existing_date else datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, message.from_user.username or message.from_user.first_name, data['rating'], message.text, date_to_save))
    conn.commit()
    await message.answer("✅ Твой отзыв успешно сохранен!" + CONTACT_INFO)
    await state.clear()

@dp.callback_query(F.data == "skip_comment", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    
    cursor.execute("SELECT date FROM reviews WHERE user_id=?", (user_id,))
    existing_date = cursor.fetchone()
    date_to_save = existing_date[0] if existing_date else datetime.now(MSK).strftime("%d.%m.%Y %H:%M")

    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без описания", date_to_save))
    conn.commit()
    await callback.message.edit_text("✅ Оценка сохранена!" + CONTACT_INFO)
    await state.clear()
    await callback.answer()

@dp.message(Command("review"))
async def cmd_view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Всего отзывов: {count})\n\n"
    for r in rows:
        res += f"👤 @{r[0]} | {r[1]}/5\n📝 {r[2]}\n📅 {r[3]}\n\n"
    if not rows: res = "Отзывов пока нет."
    await message.answer(res + CONTACT_INFO, parse_mode="HTML")

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
    await message.answer("Привет! Чтобы скачать драм кит, подпишись на наш канал!" + CONTACT_INFO, reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_sub(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        file_res = cursor.fetchone()
        
        if not file_res:
            await callback.answer("❌ Ошибка: Админ еще не загрузил файл через /FileDK", show_alert=True)
            return
        
        # --- ПРОВЕРКА СПАМА (АНТИ-ПОВТОР) ---
        user_id = callback.from_user.id
        cursor.execute("SELECT received_file, spam_count, last_download_time FROM users WHERE user_id=?", (user_id,))
        user_data = cursor.fetchone()
        
        now_dt = datetime.now(MSK)
        now_str = now_dt.strftime("%d.%m.%Y %H:%M")
        
        if user_data and user_data[0] == 1:
            spam_count = user_data[1] or 0
            last_time_str = user_data[2]
            
            # Логика повышения срока
            wait_hours = 1
            if spam_count == 1: wait_hours = 1
            elif spam_count == 2: wait_hours = 2
            elif spam_count == 3: wait_hours = 5
            elif spam_count == 4: wait_hours = 10
            elif spam_count >= 5: wait_hours = 24
            
            if last_time_str:
                try:
                    last_dt = datetime.strptime(last_time_str, "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
                    diff = now_dt - last_dt
                    
                    if diff < timedelta(hours=wait_hours):
                        new_spam = spam_count + 1
                        cursor.execute("UPDATE users SET spam_count=? WHERE user_id=?", (new_spam, user_id))
                        conn.commit()
                        
                        rem_time = timedelta(hours=wait_hours) - diff
                        hours_rem = int(rem_time.total_seconds() // 3600)
                        mins_rem = int((rem_time.total_seconds() % 3600) // 60)
                        
                        await callback.answer(
                            f"❌ Ты уже скачал файл!\n"
                            f"Повторное скачивание доступно через: {hours_rem}ч {mins_rem}мин.\n"
                            f"(Попыток: {new_spam})", 
                            show_alert=True
                        )
                        return
                except:
                    pass

        # Оформление скачивания
        if not user_data:
            cursor.execute("INSERT INTO users (user_id, username, full_name, received_file, date_received, spam_count, last_download_time) VALUES (?, ?, ?, 1, ?, 0, ?)",
                           (user_id, callback.from_user.username, callback.from_user.first_name, now_str, now_str))
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
        else:
            if user_data[0] == 0:
                cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
            cursor.execute("UPDATE users SET received_file=1, date_received=?, last_download_time=?, spam_count=spam_count+1 WHERE user_id=?", 
                           (now_str, now_str, user_id))
        conn.commit()
        
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
        msg = (
            "🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
            "/on | /off — Вкл/Выкл бота\n"
            "/FileDK — Обновить файл\n"
            "/Stata — Список скачавших\n"
            "/sms [текст] — Рассылка всем\n"
            "/delete_review [ID] — Удалить отзыв юзера\n\n"
            "👤 <b>Юзер-команды:</b> /start, /grade, /review"
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
            sub = await is_subscribed(r[0])
            status = "✅ Подписан" if sub else "❌ Отписался"
            name = f"@{r[1]}" if r[1] else r[2]
            res += f"{name} | {status} | {r[3]}\n"
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
        except: await message.answer("Используй: /delete_review [ID пользователя]")

@dp.message(Command("FileDK"))
async def ask_file(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Пришли файл драм-кита.")

@dp.message(F.document)
async def save_file(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
        conn.commit()
        await message.answer("✅ Файл сохранен! Теперь пользователи могут его скачивать.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
