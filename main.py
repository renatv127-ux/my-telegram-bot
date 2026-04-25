
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

# Очередь скачивания (не более 2-х человек одновременно)
download_queue = asyncio.Semaphore(2)

class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ (ФИНАЛЬНАЯ ВЕРСИЯ ДЛЯ RAILWAY) ---
# Определяем путь к файлу базы данных
# 1. Сначала ищем в переменной окружения DB_PATH (если она задана в Railway)
# 2. Если нет, проверяем, существует ли папка /data (т.е. подключен ли Volume)
# 3. Если ничего из этого нет, сохраняем локально (для запуска на компе)
if os.getenv("DB_PATH"):
    DB_FILE = os.getenv("DB_PATH")
elif os.path.exists("/data"): # Если папка /data существует, используем её
    DB_FILE = "/data/bot_data.db"
else: # Иначе, сохраняем в текущую директорию
    DB_FILE = "bot_data.db"

# --- ЭТОТ PRINT ОЧЕНЬ ВАЖЕН! ПОСМОТРИ ЕГО В ЛОГАХ RAILWAY ---
print(f"--- DATABASE INITIALIZED. BOT IS USING PATH: {DB_FILE} ---")
# --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- ---

# Создаем папку для базы, если она указана и не существует
db_dir = os.path.dirname(DB_FILE)
if db_dir and not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir, exist_ok=True)
        print(f"--- Created directory for database: {db_dir} ---")
    except Exception as e:
        print(f"--- ERROR creating database directory {db_dir}: {e} ---")

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time TEXT)''') # last_msg_id удален, т.к. не использовался
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    # Проверяем, есть ли уже файл кита, иначе устанавливаем заглушку
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('file_id', '')")
    conn.commit()

db_init()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"Error checking subscription for {user_id}: {e}")
        return False

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else None

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    res = cursor.fetchone()
    if not res or res[0] is None: return 0, 0
    return round(res[0], 1), res[1]

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    if get_setting("bot_status") == "off" and user_id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return

    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (user_id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    await message.answer(f"Привет! Подпишись на канал и нажми кнопку ниже, чтобы получить файл!{CONTACT_INFO}", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id or file_id == '': # Проверяем, что file_id не пустой
        await callback.answer("❌ Файл еще не загружен администратором. Попробуйте позже.", show_alert=True)
        return

    # Проверка Кулдауна 5 минут
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    now_dt = datetime.now(MSK)
    if u_data and u_data[0]:
        try:
            last_dt = datetime.strptime(u_data[0], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now_dt - last_dt < timedelta(minutes=5):
                await callback.answer("⏳ Подождите 5 минут перед повторным скачиванием.", show_alert=True)
                return
        except: # Если формат даты в базе сломался, считаем, что кулдауна нет
            pass 

    await callback.message.edit_text("⏳ Вы в очереди... Файл будет отправлен через 4 секунды.")

    async with download_queue:
        await asyncio.sleep(4) # Защита от перегрузки сервера
        
        now_str = now_dt.strftime("%d.%m.%Y %H:%M")
        
        # Считаем скачивание только если это первое скачивание для пользователя
        cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
        user_received_file = cursor.fetchone()[0] if cursor.fetchone() else 0

        if user_received_file == 0:
            cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
        
        # Обновляем данные пользователя. COALESCE сохраняет дату первого получения, если она уже есть.
        cursor.execute("UPDATE users SET received_file=1, last_download_time=?, date_received=COALESCE(date_received, ?) WHERE user_id=?", 
                       (now_str, now_str, user_id))
        conn.commit()

        avg, count = get_average_rating()
        caption = (f"🥁 <b>Файл готов!</b>\n📈 Скачиваний: {get_setting('downloads')}\n⭐ Рейтинг: {avg}/5\n\n/grade — оставить отзыв\n/review — все отзывы{CONTACT_INFO}")
        
        await callback.message.answer_document(file_id, caption=caption)
        try: await callback.message.delete()
        except: pass
    await callback.answer() # Закрываем callback

# --- ОТЗЫВЫ (С КНОПКОЙ "ПРОПУСТИТЬ") ---

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT rating FROM reviews WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        # Если отзыв уже есть, то просто предложим обновить.
        await message.answer("🔄 Ты уже оставлял отзыв. Давай обновим его.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]])
    await message.answer("⭐ Оцени драм кит от 1 до 5:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = callback.data.split("_")[1]
    await state.update_data(rating=int(rating)) # Сохраняем оценку в FSM
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Пропустить комментарий", callback_data="skip_comment_text")]
    ])
    await callback.message.edit_text(f"Твоя оценка: {rating}/5!\nТеперь напиши краткий отзыв или нажми кнопку ниже:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)
    await callback.answer() # Закрываем callback

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comment = message.text # Просто сохраняем текст пользователя
    now_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or message.from_user.first_name, data['rating'], comment, now_str))
    conn.commit()
    await message.answer(f"✅ Отзыв сохранен!{CONTACT_INFO}")
    await state.clear()

@dp.callback_query(F.data == "skip_comment_text", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    now_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (callback.from_user.id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без описания", now_str))
    conn.commit()
    await callback.message.edit_text(f"✅ Оценка сохранена! Комментарий пропущен.{CONTACT_INFO}")
    await state.clear()
    await callback.answer() # Закрываем callback

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Всего отзывов: {count})\n\n"
    rows = cursor.fetchall()
    for r in rows:
        admin_info = f"🆔 ID: <code>{r[4]}</code>\n" if message.from_user.id == ADMIN_ID else ""
        res += f"👤 @{r[0]} | {r[1]}/5\n{admin_info}📝 {r[2]}\n📅 {r[3]}\n\n"
    await message.answer(res if rows else "Отзывов пока нет.", disable_web_page_preview=True) # Отключаем предпросмотр ссылок

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    f_status = "✅ Загружен" if get_setting("file_id") and get_setting("file_id") != '' else "❌ Нет файла"
    b_status = "Включен" if get_setting("bot_status") == "on" else "Выключен"
    await message.answer(f"🛠 <b>Админ-панель</b>\n\nФайл: {f_status}\nБот: {b_status}\n\n"
                         f"Команды:\n/Stata — Статистика скачиваний\n/sms [текст] — Рассылка\n"
                         f"/on | /off — Состояние бота\n/delete_review [ID] — Удалить отзыв")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def admin_stata(message: types.Message):
    cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    res = f"📊 <b>Статистика скачавших ({len(rows)}):</b>\n\n"
    if not rows:
        await message.answer("Никто еще не скачал.")
        return

    await message.answer("📊 Собираю статистику и проверяю подписки... Это может занять время.")
    
    current_message = ""
    for r in rows:
        sub = "✅" if await is_subscribed(r[0]) else "❌"
        line = f"ID: <code>{r[0]}</code> | @{r[1] or 'нет ника'} | {r[2]} | Подписка: {sub}\n"
        
        if len(current_message) + len(line) > 4000: # Лимит сообщения 4096 символов
            await message.answer(res + current_message)
            current_message = ""
            await asyncio.sleep(0.5) # Небольшая задержка между отправкой больших сообщений
        current_message += line
    
    if current_message:
        await message.answer(res + current_message)


@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    txt = message.text.replace("/sms", "").strip()
    if not txt:
        await message.answer("Использование: /sms [текст сообщения]")
        return
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall(); c = 0
    await message.answer(f"Начинаю рассылку для {len(users)} пользователей...")
    for u in users:
        try:
            await bot.send_message(u[0], txt)
            c += 1
            await asyncio.sleep(0.05) # Небольшая задержка, чтобы не получить бан от Telegram
        except Exception as e:
            print(f"Failed to send message to user {u[0]}: {e}")
            pass # Игнорируем ошибки отправки, если пользователь заблокировал бота
    await message.answer(f"✅ Рассылка завершена. Сообщение отправлено {c} пользователям.")

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
    conn.commit(); await message.answer("✅ Бот включен для всех пользователей.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
    conn.commit(); await message.answer("❌ Бот выключен для всех пользователей (кроме админа).")

@dp.message(Command("delete_review"), F.from_user.id == ADMIN_ID)
async def admin_del_review(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /delete_review [ID пользователя]. Пример: /delete_review 1234567")
            return
        
        user_id_to_delete = int(parts[1])
        
        cursor.execute("SELECT user_id FROM reviews WHERE user_id=?", (user_id_to_delete,))
        if cursor.fetchone():
            cursor.execute("DELETE FROM reviews WHERE user_id=?", (user_id_to_delete,))
            conn.commit()
            await message.answer(f"✅ Отзыв пользователя с ID <code>{user_id_to_delete}</code> успешно удален.")
        else:
            await message.answer(f"❌ Отзыва от пользователя с ID <code>{user_id_to_delete}</code> не найдено.")
            
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом.")
    except Exception as e:
        await message.answer(f"Произошла ошибка при удалении отзыва: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
