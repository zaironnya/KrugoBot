# -*- coding: utf-8 -*-
import os
import asyncio
import subprocess
import sys
import time
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# 🔐 Токен теперь берется из переменной окружения (для безопасности)
TOKEN = os.getenv("TG_TOKEN")

CHANNEL_ID = -1003223590941
TEMP_DIR = "temp_videos"
MAX_DURATION = 60  # секунд

os.makedirs(TEMP_DIR, exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Храним сообщение "подписка подтверждена", чтобы удалить при отправке видео
last_confirm_messages = {}

# 🎮 Реакторный прогрессбар
def reactor_bar(progress: int):
    total = 11
    center = total // 2
    bar = ["░"] * total
    bar[center] = "💠"
    wave_symbols = ["💫", "🔥", "💥"]
    wave_steps = min(len(wave_symbols), progress // 33 + 1)
    for i in range(1, wave_steps + 1):
        left = center - i
        right = center + i
        if left >= 0:
            bar[left] = wave_symbols[i - 1]
        if right < total:
            bar[right] = wave_symbols[i - 1]
    return "[" + "".join(bar) + "]"

# 🎨 Список фраз для прогресса (Unicode безопасно для Windows)
progress_phrases = [
    "\u2699\ufe0f Запуск реактора...",                 # ⚙️
    "\u26a1 Стабилизация потока энергии...",           # ⚡
    "\U0001F525 Волновое расширение...",               # 🔥
    "\U0001F4A5 Критическая энергия достигнута...",    # 💥
    "\u2728 Рендер завершён!"                         # ✨
]

# 🧠 Проверка подписки
async def check_subscription(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# 🧩 Кнопка подписки
def get_sub_button():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Подписаться", url="https://t.me/Krugobotchanel"),
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
    ]])

# 🌀 Анимация прогресса
async def animate_progress(message: types.Message):
    last_text = ""
    for i in range(0, 101, 10):
        bar = reactor_bar(i)
        phrase_index = min(i // 25, len(progress_phrases) - 1)
        text = f"{bar}\n     {i}%\n{progress_phrases[phrase_index]}"
        try:
            if text != last_text:
                await message.edit_text(text)
                last_text = text
        except Exception as e:
            if "message is not modified" not in str(e):
                print(f"[WARN] Ошибка обновления прогресса: {e}")
        await asyncio.sleep(0.25)

# 🚀 Команда /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        "⚡ Привет!\n"
        "Скинь видео до 1 минуты — я сделаю из него стильный кружок ⭕\n\n"
        "Проект создан в стиле Video Reactor 💠"
    )

# 🔁 Проверка подписки через кнопку
@dp.callback_query(F.data == "check_sub")
async def check_subscription_callback(callback: types.CallbackQuery):
    user = callback.from_user
    if await check_subscription(user.id):
        try:
            await callback.message.delete()
        except:
            pass
        confirm_msg = await callback.message.answer("✅ Подписка подтверждена! Можешь отправить видео 🎥")
        last_confirm_messages[user.id] = confirm_msg.message_id
    else:
        await callback.answer("Ты ещё не подписался!", show_alert=True)

# 🎥 Обработка видео
@dp.message(lambda m: m.video or m.document)
async def handle_video(message: types.Message):
    user_id = message.from_user.id

    if user_id in last_confirm_messages:
        try:
            await bot.delete_message(message.chat.id, last_confirm_messages[user_id])
            del last_confirm_messages[user_id]
        except:
            pass

    subscribed = await check_subscription(user_id)
    if not subscribed:
        sent = await message.reply(
            "🚫 Доступ ограничен!\n\nПодпишись на канал, чтобы использовать бота 👇",
            reply_markup=get_sub_button()
        )
        try:
            await message.delete()
        except:
            pass
        return

    sent_message = await message.reply("⚙️ Запуск реактора...")

    try:
        file_id = message.video.file_id if message.video else message.document.file_id
        file_info = await bot.get_file(file_id)
        local_path = os.path.join(TEMP_DIR, os.path.basename(file_info.file_path))
        await bot.download_file(file_info.file_path, destination=local_path)

        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", local_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        try:
            duration = float(result.stdout or 0)
        except ValueError:
            await sent_message.edit_text("❌ Ошибка: пожалуйста, отправь видео до 1 минуты 🎬")
            os.remove(local_path)
            return

        if duration > MAX_DURATION:
            await sent_message.edit_text(f"⚠️ Ошибка: видео длиннее {MAX_DURATION} секунд.")
            os.remove(local_path)
            return

        await animate_progress(sent_message)
        await sent_message.edit_text("✨ Рендер завершён!\n🌀 Финализация видео... Пару секунд!")
        await asyncio.sleep(1.5)
        for phase in ["💫 Сжимаем видео...", "🔥 Завершаем упаковку...", "✅ Готово!"]:
            await sent_message.edit_text(phase)
            await asyncio.sleep(0.8)

        video_note_path = os.path.join(TEMP_DIR, "video_note.mp4")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", local_path,
            "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=512:512",
            "-preset", "ultrafast", "-c:v", "libx264", "-c:a", "aac", video_note_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.wait()

        await bot.send_video_note(message.chat.id, video_note=FSInputFile(video_note_path))

        await sent_message.delete()
        try:
            await message.delete()
        except:
            pass
        os.remove(local_path)
        os.remove(video_note_path)

    except Exception as e:
        await sent_message.edit_text(f"❌ Ошибка: {e}")
        try:
            os.remove(local_path)
        except:
            pass
        try:
            await message.delete()
        except:
            pass

# 🟢 Точка входа
if __name__ == "__main__":
    # 👁 Очистка консоли
    os.system('cls' if os.name == 'nt' else 'clear')
    print("═════════════════════════════════════════════")
    print("✅ BOT STARTED — Telegram Video Reactor active")
    print("═════════════════════════════════════════════")

    # 🧩 1. Автоочистка временных файлов
    def clean_temp_folder():
        now = time.time()
        for f in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, f)
            if os.path.isfile(path) and now - os.path.getmtime(path) > 3600:
                os.remove(path)
                print(f"🧹 Deleted old temp file: {f}")

    def clean_loop():
        while True:
            clean_temp_folder()
            time.sleep(600)  # каждые 10 минут

    threading.Thread(target=clean_loop, daemon=True).start()

    # 🧩 2. Сервер keep-alive + логирование IP
    class LoggingHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            ip = self.client_address[0]
            if "cron-job.org" in self.headers.get("User-Agent", ""):
                print(f"⏰ Received keep-alive ping from cron-job.org ({ip})")
            else:
                print(f"🔁 Received keep-alive ping from {ip}")

    def run_server():
        port = int(os.getenv("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), LoggingHandler)
        print(f"🌐 Keep-alive server running on port {port}")
        server.serve_forever()

    threading.Thread(target=run_server, daemon=True).start()

    # 🧩 3. Автоперезапуск при сбоях
    while True:
        try:
            asyncio.run(dp.start_polling(bot))
        except Exception as e:
            print(f"⚠️ Restarting bot due to error: {e}")
            time.sleep(5)
