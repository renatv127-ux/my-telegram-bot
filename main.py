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

class AdminStates(StatesGroup):
    waiting_for_file = State()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

def db_init():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                       received_file INTEGER DEFAULT 0, date_received TEXT,
                       last_download_time TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews 
                      (user_id INTEGER PRIMARY KEY, username TEXT, rating INTEGER, comment TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    conn.commit()

db_init()

async def is_subscribed(user_id):
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except:
        return False

def get_setting(key):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else None

def get_average_rating():
    cursor.execute("SELECT AVG(rating), COUNT(user_id) FROM reviews")
    avg, count = cursor.fetchone()
    if not avg: return 0, 0
    return round(avg, 1), count

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if get_setting("bot_status") == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("❌ Бот временно отключен администратором.")
        return
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, received_file) VALUES (?, ?, ?, 0)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    # 👇 имя пользователя
    name = message.from_user.username
    if name:
        name = f"@{name}"
    else:
        name = message.from_user.first_name or "друг"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Скачать драм кит", callback_data="check_sub")]
    ])
    
    await message.answer(
        f"Привет, {name}! 👋\nПодпишись на канал и нажми кнопку, чтобы получить файл!" + CONTACT_INFO,
        reply_markup=kb
    )

@dp.callback_query(F.data == "check_sub")
async def process_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if not await is_subscribed(user_id):
        await callback.answer("❌ Сначала подпишись на канал!", show_alert=True)
        return

    file_id = get_setting("file_id")
    if not file_id:
        await callback.answer("❌ Админ еще не загрузил файл через /FileDK", show_alert=True)
        return

    cursor.execute("SELECT last_download_time, received_file FROM users WHERE user_id=?", (user_id,))
    u_data = cursor.fetchone()
    
    now_dt = datetime.now(MSK)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")

    if u_data and u_data[0]:
        try:
            last_dt = datetime.strptime(u_data[0], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if now_dt - last_dt < timedelta(minutes=15):
                diff = timedelta(minutes=15) - (now_dt - last_dt)
                await callback.answer(f"❌ Повторная загрузка будет через {int(diff.total_seconds() // 60)} мин.", show_alert=True)
                return
        except:
            pass

    if not u_data or u_data[1] == 0:
        cursor.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='downloads'")

    cursor.execute('''INSERT OR REPLACE INTO users 
                      (user_id, username, full_name, received_file, last_download_time, date_received) 
                      VALUES (?, ?, ?, 1, ?, IFNULL((SELECT date_received FROM users WHERE user_id=?), ?))''',
                   (user_id, callback.from_user.username, callback.from_user.first_name, now_str, user_id, now_str))
    conn.commit()

    avg, count = get_average_rating()
    total_dl = get_setting("downloads")
    
    caption = (f"🥁 <b>Файл готов!</b>\n📈 Скачиваний: {total_dl}\n⭐ Рейтинг: {avg}/5\n\n"
               f"/grade — оставить отзыв\n/review — отзывы" + CONTACT_INFO)
    
    await callback.message.answer_document(file_id, caption=caption, parse_mode="HTML")
    await callback.answer()

@dp.message(Command("grade"))
async def cmd_grade(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # ✅ ФИКС
    cursor.execute("SELECT last_download_time FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    
    if not row or not row[0]:
        await message.answer("❌ Сначала скачай драм кит через /start!" + CONTACT_INFO)
        return

    cursor.execute("SELECT rating, comment, date FROM reviews WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        try:
            r_dt = datetime.strptime(existing[2], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
            if datetime.now(MSK) - r_dt > timedelta(hours=2):
                await message.answer("❌ Изменить отзыв можно только в течение 2-х часов!" + CONTACT_INFO)
                return
        except:
            pass
        text = f"🔄 Твой отзыв ({existing[0]}/5). Выбери новую оценку:"
    else:
        text = "⭐ Оцени драм кит (1-5):"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"rate_{i}") for i in range(1, 6)]
    ])
    await message.answer(text + CONTACT_INFO, reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_rating)

@dp.callback_query(F.data.startswith("rate_"), ReviewStates.waiting_for_rating)
async def process_rating(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    await state.update_data(rating=rating)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_text")]])
    await callback.message.edit_text(f"Оценка {rating}/5!\nНапиши комментарий или нажми пропустить:", reply_markup=kb)
    await state.set_state(ReviewStates.waiting_for_comment)

@dp.message(ReviewStates.waiting_for_comment)
async def save_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    
    dt = datetime.now(MSK).strftime("%d.%м.%Y %H:%M")
    
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, message.from_user.username or message.from_user.first_name, data['rating'], message.text, dt))
    conn.commit()
    await message.answer("✅ Отзыв сохранен!" + CONTACT_INFO)
    await state.clear()

@dp.callback_query(F.data == "skip_text", ReviewStates.waiting_for_comment)
async def skip_comment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    
    dt = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    
    cursor.execute("INSERT OR REPLACE INTO reviews (user_id, username, rating, comment, date) VALUES (?, ?, ?, ?, ?)",
                   (user_id, callback.from_user.username or callback.from_user.first_name, data['rating'], "Без описания", dt))
    conn.commit()
    await callback.message.edit_text("✅ Оценка сохранена!" + CONTACT_INFO)
    await state.clear()

@dp.message(Command("review"))
async def view_reviews(message: types.Message):
    avg, count = get_average_rating()
    cursor.execute("SELECT username, rating, comment, date, user_id FROM reviews ORDER BY date DESC LIMIT 10")
    rows = cursor.fetchall()
    res = f"⭐ <b>Средний рейтинг: {avg}/5</b> (Отзывов: {count})\n\n"
    for r in rows:
        id_info = f"🆔 ID: <code>{r[4]}</code>\n" if message.from_user.id == ADMIN_ID else ""
        res += f"👤 @{r[0]} | {r[1]}/5\n{id_info}📝 {r[2]}\n📅 {r[3]}\n\n"
    if not rows: res = "Отзывов пока нет."
    await message.answer(res + CONTACT_INFO, parse_mode="HTML")

# --- АДМИН ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    f_status = "❌ Не загружен" if not get_setting("file_id") else "✅ Загружен"
    b_status = "Вкл" if get_setting("bot_status") == "on" else "Выкл"
    text = (f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
            f"Статус файла: {f_status}\n"
            f"Статус бота: {b_status}\n\n"
            f"⚙️ <b>КОМАНДЫ АДМИНА:</b>\n"
            f"/FileDK — Загрузить/Обновить файл\n"
            f"/on | /off — Включить/Выключить бота\n"
            f"/Stata — Посмотреть всех скачавших\n"
            f"/sms [текст] — Рассылка сообщения всем\n"
            f"/delete_review [ID] — Удалить отзыв\n\n"
            f"👤 <b>КОМАНДЫ ПОЛЬЗОВАТЕЛЯ:</b>\n"
            f"/start — Главное меню\n"
            f"/grade — Поставить оценку\n"
            f"/review — Посмотреть отзывы")
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("FileDK"))
async def admin_file_req(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.answer("📁 Отправь файл драм-кита:")
        await state.set_state(AdminStates.waiting_for_file)

@dp.message(AdminStates.waiting_for_file, F.document)
async def admin_file_save(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (message.document.file_id,))
        conn.commit()
        await message.answer("✅ Файл сохранен!")
        await state.clear()

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
        cursor.execute("SELECT user_id, username, date_received FROM users WHERE received_file=1")
        rows = cursor.fetchall()
        if not rows:
            await message.answer("Никто еще не скачал."); return
        res = "<b>📊 Статистика:</b>\n\n"
        for r in rows:
            res += f"ID: <code>{r[0]}</code> | @{r[1]} | {r[2]}\n"
        await message.answer(res[:4000], parse_mode="HTML")

@dp.message(Command("sms"))
async def admin_sms(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        txt = message.text.replace("/sms", "").strip()
        if not txt: return
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        c = 0
        for u in users:
            try:
                await bot.send_message(u[0], txt); c += 1
                await asyncio.sleep(0.05)
            except: pass
        await message.answer(f"✅ Рассылка: {c} чел.")

@dp.message(Command("delete_review"))
async def admin_del_review(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        try:
            rid = int(message.text.split()[1])
            cursor.execute("DELETE FROM reviews WHERE user_id=?", (rid,))
            conn.commit()
            await message.answer(f"✅ Отзыв {rid} удален.")
        except:
            await message.answer("Пример: /delete_review 1234567")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())