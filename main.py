
import os
import asyncio
import sqlite3
import logging
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.exceptions import TelegramBadRequest

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 1753037099
CHANNEL_ID = "@TWIXER_MUSIC"  # ID или юзернейм канала
CHANNEL_URL = "https://t.me/TWIXER_MUSIC"
MSK = pytz.timezone('Europe/Moscow')

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
# Начальное кол-во скачиваний
cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads', '0')")
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

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    # Сохраняем пользователя в базу, если его нет
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.full_name))
    conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
    ])
    
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        f"Чтобы получить данный драм кит, нужно быть подписанным на канал TWIXER!",
        reply_markup=kb
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    subscribed = await is_subscribed(user_id)
    
    if subscribed:
        file_id = get_file_id()
        if not file_id:
            await callback.message.answer("Файл еще не загружен админом.")
            return
        
        increment_downloads()
        total = get_download_count()
        
        # Обновляем инфо в БД
        now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        cursor.execute("UPDATE users SET received_file=1, date_received=? WHERE user_id=?", (now, user_id))
        conn.commit()

        await callback.message.answer_document(
            file_id, 
            caption=f"Драм кит успешно получен!\n\n🔥 Общее количество скачиваний: {total}"
        )
        await callback.answer()
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на TWIXER", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="Получить драм кит", callback_data="check_sub")]
        ])
        await callback.message.answer(
            "К сожалению, вы не подписаны на канал.\n"
            "Чтобы получить драм кит, надо быть подписанным на канал!",
            reply_markup=kb
        )
        await callback.answer()

# --- АДМИН ПАНЕЛЬ ---

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    text = (
        "Команды администратора:\n"
        "/FileDK - Загрузить/изменить файл\n"
        "/Stata - Посмотреть статистику пользователей"
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
        status_text = "✅ подписан" if sub_status else "❌ НЕ ПОДПИСАН (отписался)"
        user_link = f"@{uname}" if uname else fname
        report += f"{user_link} {status_text} | получил: {date}\n"
    
    # Если текст слишком длинный, ТГ может выдать ошибку, поэтому режем
    await message.answer(report[:4000])

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
