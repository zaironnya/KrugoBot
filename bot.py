# -*- coding: utf-8 -*-
import os
import sys
import time
import math
import asyncio
import threading
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Tuple
from http.server import SimpleHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# ==========================
# 🔧 Конфиг
# ==========================
TOKEN = os.getenv("TG_TOKEN")
CHANNEL_ID = -1003223590941
ADMIN_ID = 1052210475
TEMP_DIR = "temp_videos"
MAX_DURATION = 60
MAX_FILE_SIZE_MB = 20
KEEPALIVE_PORT = int(os.getenv("PORT", 10000))

os.makedirs(TEMP_DIR, exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher()

PROCESS_START_TS = time.time()

# ==========================
# 📊 Статистика за 24 часа
# ==========================
_events_last_24h: List[Tuple[float, int]] = []

def _prune_events() -> None:
    cutoff = time.time() - 24 * 3600
    while _events_last_24h and _events_last_24h[0][0] < cutoff:
        _events_last_24h.pop(0)

def add_video_event(user_id: int) -> None:
    _events_last_24h.append((time.time(), user_id))
    _prune_events()

def get_stats_last_24h() -> Tuple[int, int]:
    _prune_events()
    users = {u for _, u in _events_last_24h}
    return len(users), len(_events_last_24h)

# ==========================
# 🧵 Очередь
# ==========================
queue: "asyncio.Queue[dict]" = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None

@dataclass
class TaskItem:
    chat_id: int
    user_id: int
    original_message_id: int
    status_message_id: int
    src_path: str

# ==========================
# 🔘 Прогресс-бар
# ==========================
def reactor_bar(progress: int) -> str:
    total = 11
    center = total // 2
    bar = ["░"] * total
    bar[center] = "💠"
    wave_symbols = ["💫", "🔥", "💥"]
    wave_steps = min(len(wave_symbols), progress // 33 + 1)
    for i in range(1, wave_steps + 1):
        left, right = center - i, center + i
        if left >= 0:
            bar[left] = wave_symbols[i - 1]
        if right < total:
            bar[right] = wave_symbols[i - 1]
    return "[" + "".join(bar) + "]"

progress_phrases = [
    "⚙️ Запуск реактора...",
    "⚡ Стабилизация потока энергии...",
    "🔥 Волновое расширение...",
    "💥 Критическая энергия достигнута...",
    "✨ Рендер завершён!"
]

async def animate_progress(msg: types.Message):
    last = ""
    for i in range(0, 101, 10):
        bar = reactor_bar(i)
        phrase_index = min(i // 25, len(progress_phrases) - 1)
        text = f"{bar}\n     {i}%\n{progress_phrases[phrase_index]}"
        if text != last:
            try:
                await msg.edit_text(text)
                last = text
            except:
                pass
        await asyncio.sleep(0.25)

# ==========================
# 🧠 Подписка
# ==========================
async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def get_sub_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Подписаться", url="https://t.me/Krugobotchanel"),
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
    ]])

last_confirm_messages: dict[int, int] = {}

@dp.callback_query(F.data == "check_sub")
async def on_check_sub(cb: types.CallbackQuery):
    user = cb.from_user
    if await check_subscription(user.id):
        try:
            await cb.message.delete()
        except:
            pass
        m = await cb.message.answer("✅ Подписка подтверждена! Можешь отправить видео 🎥")
        last_confirm_messages[user.id] = m.message_id
    else:
        await cb.answer("Ты ещё не подписался!", show_alert=True)

# ==========================
# 🗣 Команды
# ==========================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        f"⚡ Привет!\n"
        f"Скинь видео до {MAX_DURATION} секунд и не более {MAX_FILE_SIZE_MB} МБ — я сделаю из него стильный кружок ⭕\n\n"
        f"Видео обрабатываются по очереди — если очередь занята, я подскажу позицию.\n\n"
        "Проект создан в стиле Video Reactor 💠"
    )

@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uptime = int(time.time() - PROCESS_START_TS)
    hours, minutes = uptime // 3600, (uptime % 3600) // 60
    files = os.listdir(TEMP_DIR)
    total_size_mb = sum(os.path.getsize(os.path.join(TEMP_DIR, f)) for f in files) / (1024 * 1024)
    users24, videos24 = get_stats_last_24h()
    await message.reply(
        "💠 KrugoBot активен!\n"
        f"⏱ Аптайм: {hours} ч {minutes} мин\n"
        f"👥 За 24 ч: {users24} пользователей\n"
        f"🎬 Отправлено видео: {videos24}\n"
        f"🧰 Очередь: {queue.qsize()} в ожидании\n"
        f"📂 В temp_videos: {len(files)} файлов ({total_size_mb:.1f} МБ)\n"
        "🌐 Keep-alive OK, авто-рестарт включён ✅"
    )

# ==========================
# 📥 Приём видео
# ==========================
@dp.message(lambda m: m.video or m.document)
async def handle_incoming_video(message: types.Message):
    user_id = message.from_user.id
    mid = last_confirm_messages.pop(user_id, None)
    if mid:
        try:
            await bot.delete_message(message.chat.id, mid)
        except:
            pass

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

    file_id = (message.video or message.document).file_id
    file_info = await bot.get_file(file_id)
    if file_info.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply(f"⚠️ Файл больше {MAX_FILE_SIZE_MB} МБ!")
        return

    src_path = os.path.join(TEMP_DIR, os.path.basename(file_info.file_path))
    await bot.download_file(file_info.file_path, destination=src_path)

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", src_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    duration = float(result.stdout or 0)
    if duration > MAX_DURATION:
        os.remove(src_path)
        await message.reply(f"⚠️ Ошибка: видео длиннее {MAX_DURATION} секунд.")
        return

    status_msg = await message.reply("⚙️ Запуск реактора...")
    await queue.put(TaskItem(
        chat_id=message.chat.id,
        user_id=user_id,
        original_message_id=message.message_id,
        status_message_id=status_msg.message_id,
        src_path=src_path
    ).__dict__)

# ==========================
# 🛠 Воркёр очереди
# ==========================
async def worker_loop():
    while True:
        item: dict = await queue.get()
        task = TaskItem(**item)
        try:
            status_msg = await bot.edit_message_text(
                chat_id=task.chat_id,
                message_id=task.status_message_id,
                text="🌀 Обработка началась..."
            )
            await animate_progress(status_msg)
            await status_msg.edit_text("✨ Рендер завершён!\n🌀 Финализация видео...")

            video_note_path = os.path.join(TEMP_DIR, f"video_note_{task.original_message_id}.mp4")

            # ✅ Оптимизированный ffmpeg
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", task.src_path,
                "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=480:480",
                "-pix_fmt", "yuv420p",
                "-threads", "2",
                "-preset", "ultrafast", "-tune", "zerolatency",
                "-movflags", "+faststart",
                "-c:v", "libx264", "-c:a", "aac", video_note_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await proc.wait()

            await bot.send_video_note(task.chat_id, video_note=FSInputFile(video_note_path))
            add_video_event(task.user_id)

            await bot.delete_message(task.chat_id, task.original_message_id)
            await bot.delete_message(task.chat_id, status_msg.message_id)
        except Exception as e:
            if "Conflict" in str(e):
                await asyncio.sleep(10)
            else:
                await bot.send_message(task.chat_id, f"❌ Ошибка: {e}")
        finally:
            for p in (task.src_path, os.path.join(TEMP_DIR, f"video_note_{task.original_message_id}.mp4")):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except:
                    pass
            queue.task_done()

# ==========================
# 🧹 Очистка temp
# ==========================
def _clean_temp_loop():
    while True:
        now = time.time()
        for f in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, f)
            if os.path.isfile(path) and now - os.path.getmtime(path) > 900:
                try:
                    os.remove(path)
                    print(f"🧹 Удалён старый файл: {f}")
                except:
                    pass
        time.sleep(900)

# ==========================
# 🌐 Keep-alive
# ==========================
class LoggingHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        ip = self.client_address[0]
        ua = self.headers.get("User-Agent", "")
        if "cron-job.org" in ua:
            print(f"⏰ Пинг от cron-job.org ({ip})")
        else:
            print(f"🔁 Пинг от {ip}")

def run_keepalive_server():
    server = HTTPServer(("0.0.0.0", KEEPALIVE_PORT), LoggingHandler)
    print(f"🌐 Keep-alive server на порту {KEEPALIVE_PORT}")
    server.serve_forever()

# ==========================
# 🚀 Запуск
# ==========================
if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    print("═════════════════════════════════════════════")
    print("✅ BOT STARTED — Telegram Video Reactor active")
    print("═════════════════════════════════════════════")

    threading.Thread(target=_clean_temp_loop, daemon=True).start()
    threading.Thread(target=run_keepalive_server, daemon=True).start()

    async def main():
        global _worker_task
        _worker_task = asyncio.create_task(worker_loop())
        while True:
            try:
                await dp.start_polling(bot)
            except Exception as e:
                if "Conflict" in str(e):
                    print("⚠️ Conflict при polling. Жду 10 сек...")
                    await asyncio.sleep(10)
                else:
                    print(f"⚠️ Ошибка polling: {e}. Перезапуск через 5 сек.")
                    await asyncio.sleep(5)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Завершение работы...")
