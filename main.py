
import os
import asyncio
import sqlite3
from datetime import datetime
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

# Очередь (Semaphore): одновременно обрабатываем не более 2-х скачиваний
download_queue = asyncio.Semaphore(2)

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
                  (rev_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                   username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
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

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(rev_id) FROM reviews")
    avg, count = cursor.fetchone()
    if not avg: return "0", 0
    return round(avg, 1), count

def get_bot_status():
    cursor.execute("SELECT value FROM settings WHERE key='bot_status'")
    return cursor.fetchone()[0]

# --- ЛОГИКА ОТЗЫВОВ (ЧЕРЕЗ 10 МИНУТ) ---

async def ask_for_review_timer(user_id):
    await asyncio.sleep(600) # Ожидание 10 минут
    try:
        await bot.send_message(user_id, "Как тебе драм кит? Напиши оценку от 0 до 5:")
    except: pass

@dp.message(F.text.regexp(r'^[0-5]$')) # Ловим оценку цифрой
async def handle_rating_input(message: types.Message, state: FSMContext):
    cursor.execute("SELECT received_file FROM users WHERE user_id=?", (message.from_user.id,))
    user_status = cursor.fetchone()
    if user_status and user_status[0] == 1: # Проверяем, что пользователь уже скачивал файл
        current_state = await state.get_state()
        if current_state is None: # Если не в состоянии, то это первое сообщение с оценкой
            await state.update_data(rating=message.text)
            await message.answer(f"Оценка {message.text}/5 принята! Теперь напиши свой отзыв:")
            await state.set_state(ReviewStates.waiting_for_comment)
    # Иначе игнорируем, если пользователь не скачивал или уже в другом состоянии

@dp.message(ReviewStates.waiting_for_comment)
async def handle_comment_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    rating = data['rating']
    comment = message.text
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    
    cursor.execute("INSERT INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (message.from_user.id, message.from_user.username or message.from_user.first_name, rating, comment, now))
    conn.commit()
    await message.answer("Спасибо за твой отзыв! ❤️" + CONTACT_INFO)
    await state.clear()

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

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
    await message.answer(f"Привет! Подпишись на канал, чтобы скачать драм кит!" + CONTACT_INFO, reply_markup=kb)

@dp.message(Command("review"))
async def cmd_view_reviews(message: types.Message):
    cursor.execute("SELECT username, rating, comment FROM reviews ORDER BY rev_id DESC LIMIT 10")
    rows = cursor.fetchall()
    avg, count = get_average_rating()
    if not rows:
        await message.answer("Отзывов пока нет.")
        return
    res = f"⭐ Средний рейтинг: {avg}/5 (Всего отзывов: {count})\n\n💬 **Последние отзывы:**\n\n"
    for r in rows:
        res += f"👤 {r[0]} | Оценка: {r[1]}/5\n📝 {r[2]}\n\n"
    await message.answer(res)

@dp.callback_query(F.data == "check_sub")
async def process_sub_check(callback: types.CallbackQuery):
    if get_bot_status() == "off" and callback.from_user.id != ADMIN_ID:
        await callback.answer("Бот выключен.", show_alert=True)
        return

    if await is_subscribed(callback.from_user.id):
        cursor.execute("SELECT value FROM settings WHERE key='file_id'")
        file_res = cursor.fetchone()
        if not file_res:
            await callback.answer("Файл не загружен.", show_alert=True)
            return
        
        file_id = file_res[0]
        await callback.message.edit_text("⏳ Вы в очереди... Файл будет отправлен через 3-5 секунд.")

        async with download_queue:
            await asyncio.sleep(4) # Задержка для разгрузки сервера
            
            cursor.execute("SELECT received_file FROM users WHERE user_id=?", (callback.from_user.id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")
                cursor.execute("UPDATE users SET received_file=1, date_received=? WHERE user_id=?", 
                               (datetime.now(MSK).strftime("%d.%m.%Y %H:%M"), callback.from_user.id))
                conn.commit()
                asyncio.create_task(ask_for_review_timer(callback.from_user.id))

            avg, count = get_average_rating()
            caption = (f"🔥 Драм кит успешно получен!\n\n"
                       f"📊 Рейтинг файла: {avg}/5 (Отзывов: {count})\n"
                       f"Все отзывы: /review" + CONTACT_INFO)
            
            await callback.message.answer_document(file_id, caption=caption)
            await callback.message.delete()
    else:
        await callback.message.answer("К сожалению, вы не подписаны на канал!" + CONTACT_INFO)
        await callback.answer()

# --- КОМАНДЫ АДМИНИСТРАТОРА ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_menu_cmd(message: types.Message):
    """Выводит список всех админ-команд."""
    admin_commands_list = (
        "⚙️ **Админ-панель:**\n\n"
        "**Управление ботом:**\n"
        "`/on` - Включить бота для всех пользователей.\n"
        "`/off` - Выключить бота (доступен только админу).\n\n"
        "**Управление файлами и рассылкой:**\n"
        "`/FileDK` - Загрузить или обновить раздаваемый файл (пришлите документ после этой команды).\n"
        "`/sms [текст]` - Отправить сообщение всем пользователям, которые запускали бота.\n\n"
        "**Статистика и отзывы:**\n"
        "`/Stata` - Показать статистику скачиваний и последние отзывы (с ID для удаления).\n"
        "`/delete_review [ID]` - Удалить отзыв по его номеру (ID видно в /Stata)."
    )
    await message.answer(admin_commands_list, parse_mode="Markdown")


@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
    conn.commit()
    await message.answer("✅ Бот включен.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
    conn.commit()
    await message.answer("❌ Бот выключен.")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def admin_sms(message: types.Message):
    text = message.text.replace("/sms", "").strip()
    if not text:
        await message.answer("Используй: `/sms Ваш текст для рассылки`", parse_mode="Markdown")
        return
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    count = 0
    for u in users:
        try:
            await bot.send_message(u[0], text)
            count += 1
            await asyncio.sleep(0.05) # Небольшая задержка, чтобы не превышать лимиты Telegram
        except: continue
    await message.answer(f"✅ Рассылка завершена. Доставлено: {count}")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def admin_stata(message: types.Message):
    cursor.execute("SELECT username, rev_id, rating FROM reviews ORDER BY rev_id DESC LIMIT 10")
    revs = cursor.fetchall()
    
    msg = "📊 **Статистика отзывов (ID для удаления):**\n"
    if revs:
        for r in revs:
            msg += f"ID: `{r[1]}` | @{r[0] if r[0] else 'Без никнейма'} | Оценка: {r[2]}/5\n"
    else:
        msg += "Нет отзывов.\n"
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE received_file=1")
    msg += f"\n🔥 Всего уникальных скачиваний: {cursor.fetchone()[0]}"
    await message.answer(msg, parse_mode="Markdown")

@dp.message(Command("delete_review"), F.from_user.id == ADMIN_ID)
async def admin_del_review(message: types.Message):
    try:
        rid = int(message.text.split()[1])
        cursor.execute("DELETE FROM reviews WHERE rev_id=?", (rid,))
        conn.commit()
        await message.answer(f"✅ Отзыв ID {rid} удален.")
    except IndexError:
        await message.answer("Ошибка. Укажите ID отзыва. Пример: `/delete_review 5`", parse_mode="Markdown")
    except ValueError:
        await message.answer("Ошибка. ID должен быть числом. Пример: `/delete_review 5`", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"Произошла ошибка при удалении: {e}")

@dp.message(Command("FileDK"), F.from_user.id == ADMIN_ID)
async def admin_file(message: types.Message):
    await message.answer("Пришли файл (документ), который хочешь раздавать.")

@dp.message(F.document, F.from_user.id == ADMIN_ID)
async def admin_save_file(message: types.Message):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
    conn.commit()
    await message.answer("✅ Файл успешно сохранен.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
