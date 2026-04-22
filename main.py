
import os
import asyncio
import sqlite3
import logging
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbidden

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')
CONTACT_INFO = "\n\nесли будут вопросы или проблемы, пиши в лс @TwixerArtist"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
                   received_file INTEGER DEFAULT 0, date_received TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                  (key TEXT PRIMARY KEY, value TEXT)''')

# Начальные настройки
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
conn.commit()

# --- ФУНКЦИИ ---
async def is_subscribed(user_id):
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

def get_download_count():
    cursor.execute("SELECT value FROM settings WHERE key='downloads'")
    return int(cursor.fetchone()[0])

def increment_downloads():
    count = get_download_count() + 1
    cursor.execute("UPDATE settings SET value=? WHERE key='downloads'", (str(count),))
    conn.commit()

def save_file_id(file_id):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('file_id', ?)", (file_id,))
    conn.commit()

def get_file_id():
    cursor.execute("SELECT value FROM settings WHERE key='file_id'")
    res = cursor.fetchone()
    return res[0] if res else None

def get_bot_status():
    cursor.execute("SELECT value FROM settings WHERE key='bot_status'")
    return cursor.fetchone()[0]

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    # Проверка на включение бота (для всех кроме админа)
    if get_bot_status() == "off" and message.from_user.id != ADMIN_ID:
        await message.answer("Данный бот не работает, админ выключил его.")
        return

    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
    ])
    
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        f"Чтобы получить данный драм кит, нужно быть подписанным на канал TWIXER!"
        + CONTACT_INFO,
        reply_markup=kb
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    # Проверка на включение бота
    if get_bot_status() == "off" and callback.from_user.id != ADMIN_ID:
        await callback.answer("Бот выключен админом.", show_alert=True)
        return

    user_id = callback.from_user.id
    subscribed = await is_subscribed(user_id)
    
    if subscribed:
        file_id = get_file_id()
        if not file_id:
            await callback.message.answer("Файл еще не загружен админом.")
            return
        
        # Проверяем, скачивал ли пользователь ранее
        cursor.execute("SELECT received_file FROM users WHERE user_id=?", (user_id,))
        user_data = cursor.fetchone()
        
        if user_data and user_data[0] == 0:
            increment_downloads()
            now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
            cursor.execute("UPDATE users SET received_file=1, date_received=? WHERE user_id=?", (now, user_id))
            conn.commit()
        
        total = get_download_count()
        
        await callback.message.answer_document(
            file_id, 
            caption=f"Драм кит успешно получен!\n\n🔥 Общее количество скачиваний: {total}" + CONTACT_INFO
        )
        await callback.answer()
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
        ])
        await callback.message.answer(
            "К сожалению, вы не подписаны на канал.\n"
            "Чтобы получить драм кит, надо быть подписанным на канал!"
            + CONTACT_INFO,
            reply_markup=kb
        )
        await callback.answer()

# --- АДМИН ПАНЕЛЬ ---

@dp.message(Command("on"), F.from_user.id == ADMIN_ID)
async def bot_on(message: types.Message):
    cursor.execute("UPDATE settings SET value='on' WHERE key='bot_status'")
    conn.commit()
    await message.answer("✅ Бот включен для пользователей.")

@dp.message(Command("off"), F.from_user.id == ADMIN_ID)
async def bot_off(message: types.Message):
    cursor.execute("UPDATE settings SET value='off' WHERE key='bot_status'")
    conn.commit()
    await message.answer("❌ Бот выключен для пользователей.")

@dp.message(Command("sms"), F.from_user.id == ADMIN_ID)
async def broadcast_sms(message: types.Message):
    # Извлекаем текст после команды /sms
    broadcast_text = message.text.replace("/sms", "").strip()
    if not broadcast_text:
        await message.answer("Пример использования: `/sms Всем привет!`", parse_mode="Markdown")
        return

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    
    count = 0
    await message.answer(f"📢 Начинаю рассылку для {len(users)} чел...")
    
    for user in users:
        try:
            await bot.send_message(user[0], broadcast_text)
            count += 1
            await asyncio.sleep(0.05) # Небольшая задержка, чтобы не спамить сервер ТГ
        except Exception:
            continue
            
    await message.answer(f"✅ Рассылка завершена. Сообщение получили {count} человек.")

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    text = (
        "Команды администратора:\n"
        "/on / /off - Включить/Выключить бота\n"
        "/FileDK - Загрузить/изменить файл\n"
        "/Stata - Статистика\n"
        "/sms текст - Сделать рассылку"
    )
    await message.answer(text)

@dp.message(Command("FileDK"), F.from_user.id == ADMIN_ID)
async def file_dk_cmd(message: types.Message):
    await message.answer("Пришлите файл (документ), который бот будет раздавать.")

@dp.message(F.document, F.from_user.id == ADMIN_ID)
async def handle_docs(message: types.Message):
    save_file_id(message.document.file_id)
    await message.answer("✅ Файл успешно сохранен и готов к выдаче!")

@dp.message(Command("Stata"), F.from_user.id == ADMIN_ID)
async def get_stata(message: types.Message):
    cursor.execute("SELECT user_id, username, full_name, date_received FROM users WHERE received_file=1")
    rows = cursor.fetchall()
    
    if not rows:
        await message.answer("Файл еще никто не получал.")
        return

    report = "Статистика (те, кто получил файл):\n\n"
    for row in rows:
        uid, uname, fname, date = row
        sub_status = await is_subscribed(uid)
        status_text = "✅" if sub_status else "❌"
        user_link = f"@{uname}" if uname else fname
        report += f"{user_link} {status_text} | {date}\n"
    
    await message.answer(report[:4000])

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
