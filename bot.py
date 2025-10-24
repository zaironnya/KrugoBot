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

# 🔐 Токен берется из переменной окружения
TOKEN = os.getenv("TG_TOKEN")

CHANNEL_ID = -1003223590941
TEMP_DIR = "temp_videos"
MAX_DURATION = 60        # секунд
MAX_FILE_SIZE_MB = 20    # лимит файла
ADMIN_ID = 1052210475    # твой Telegram ID

os.makedirs(TEMP_DIR, exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher()

last_confirm_messages = {}
start_time = time.time()


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


# 🎨 Список фраз для прогресса
progress_phrases = [
    "⚙️ Запуск реактора...",
    "⚡ Стабилизация потока энергии...",
    "🔥 Волновое расширение...",
    "💥 Критическая энергия достигнута...",
    "✨ Рендер завершён!"
]


# 🧠 Проверка подписки
async def check_subscription(user_id: int):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False


# 🔗 Кнопка подписки
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
        except Exception:
            pass
        await asyncio.sleep(0.25)


# 🚀 /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        f"⚡ Привет!\n"
        f"Скинь видео до {MAX_DURATION} секунд и не более {MAX_FILE_SIZE_MB} МБ — я сделаю из него стильный кружок ⭕\n\n"
        "Проект создан в стиле Video Reactor 💠"
    )


# 💬 /status — только для тебя
@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uptime = int(time.time() - start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes = remainder // 60
    files = os.listdir(TEMP_DIR)
    total_size = sum(os.path.getsize(os.path.join(TEMP_DIR, f)) for f in files) / (1024 * 1024)
    await message.reply(
        f"💠 KrugoBot активен!\n"
        f"⏱ Аптайм: {hours} ч {minutes} мин\n"
        f"📂 В temp_videos: {len(files)} файлов ({total_size:.1f} МБ)\n"
        f"🌐 Keep-alive OK, авто-рестарт включён ✅"
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

    # Удаляем старое подтверждение
    if user_id in last_confirm_messages:
        try:
            await bot.delete_message(message.chat.id, last_confirm_messages[user_id])
        except:
            pass
        last_confirm_messages.pop(user_id, None)

    # Проверяем подписку
    if not await check_subscription(user_id):
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

        # Проверяем размер файла
        if file_info.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            await sent_message.edit_text(f"⚠️ Ошибка: файл больше {MAX_FILE_SIZE_MB} МБ!")
            return

        local_path = os.path.join(TEMP_DIR, os.path.basename(file_info.file_path))
        await bot.download_file(file_info.file_path, destination=local_path)

        # Проверяем длительность
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", local_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        try:
            duration = float(result.stdout or 0)
        except ValueError:
            duration = 0.0
        if duration > MAX_DURATION:
            await sent_message.edit_text(f"⚠️ Ошибка: видео длиннее {MAX_DURATION} секунд.")
            os.remove(local_path)
            return

        # Прогресс и финализация
        await animate_progress(sent_message)
        await sent_message.edit_text("✨ Рендер завершён!\n🌀 Финализация видео... Пару секунд!")
        await asyncio.sleep(1.5)
        for phase in ["💫 Сжимаем видео...", "🔥 Завершаем упаковку...", "✅ Готово!"]:
            await sent_message.edit_text(phase)
            await asyncio.sleep(0.8)

        # Обработка видео → кружок
        video_note_path = os.path.join(TEMP_DIR, "video_note.mp4")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", local_path,
            "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=512:512",
            "-preset", "ultrafast", "-c:v", "libx264", "-c:a", "aac", video_note_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.wait()

        # Отправляем кружок
        await bot.send_video_note(message.chat.id, video_note=FSInputFile(video_note_path))

        # Удаляем исходное видео из чата после отправки кружка
        try:
            await message.delete()
        except:
            pass

        # Удаляем служебное сообщение
        await sent_message.delete()

        # Удаляем файлы
        os.remove(local_path)
        os.remove(video_note_path)

    except Exception as e:
        if "Conflict" in str(e):
            print("⚠️ Telegram Conflict, повторное подключение...")
            await asyncio.sleep(5)
            return
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
    os.system('cls' if os.name == 'nt' else 'clear')
    print("═════════════════════════════════════════════")
    print("✅ BOT STARTED — Telegram Video Reactor active")
    print("═════════════════════════════════════════════")

    # 🧹 Автоочистка temp
    def clean_temp_folder():
        now = time.time()
        for f in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, f)
            if os.path.isfile(path) and now - os.path.getmtime(path) > 900:
                os.remove(path)
                print(f"🧹 Удалён старый файл: {f}")

    def clean_loop():
        while True:
            clean_temp_folder()
            time.sleep(1800)

    threading.Thread(target=clean_loop, daemon=True).start()

    # 🌐 Keep-alive
    class LoggingHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            ip = self.client_address[0]
            ua = self.headers.get("User-Agent", "")
            if "cron-job.org" in ua:
                print(f"⏰ Пинг от cron-job.org ({ip})")
            else:
                print(f"🔁 Пинг от {ip}")

    def run_server():
        port = int(os.getenv("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), LoggingHandler)
        print(f"🌐 Keep-alive server на порту {port}")
        server.serve_forever()

    threading.Thread(target=run_server, daemon=True).start()

    # ♻️ Авто-рестарт
    while True:
        try:
            asyncio.run(dp.start_polling(bot))
        except Exception as e:
            print(f"⚠️ Перезапуск бота из-за ошибки: {e}")
            time.sleep(5)
