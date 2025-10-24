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
TOKEN = os.getenv("TG_TOKEN")                  # Токен из переменной окружения
CHANNEL_ID = -1003223590941                    # ID канала для проверки подписки
ADMIN_ID = 1052210475                          # твой Telegram ID
TEMP_DIR = "temp_videos"                       # папка с временными файлами
MAX_DURATION = 60                              # макс. длительность, секунд
MAX_FILE_SIZE_MB = 20                          # макс. размер файла, MB
KEEPALIVE_PORT = int(os.getenv("PORT", 10000)) # порт для Render keep-alive

os.makedirs(TEMP_DIR, exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Время старта процесса (для аптайма)
PROCESS_START_TS = time.time()

# ==========================
# 📊 Статистика за 24 часа
# ==========================
# Храним события вида (ts, user_id)
_events_last_24h: List[Tuple[float, int]] = []

def _prune_events() -> None:
    """Удаляем события старше 24 часов."""
    cutoff = time.time() - 24*3600
    while _events_last_24h and _events_last_24h[0][0] < cutoff:
        _events_last_24h.pop(0)

def add_video_event(user_id: int) -> None:
    """Добавляем факт отправки видео в статистику."""
    _events_last_24h.append((time.time(), user_id))
    _prune_events()

def get_stats_last_24h() -> Tuple[int, int]:
    """Возвращает (кол-во уникальных пользователей, кол-во отправленных видео) за 24 часа."""
    _prune_events()
    users = {u for _, u in _events_last_24h}
    return (len(users), len(_events_last_24h))

# ==========================
# 🧵 Очередь на обработку
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
# 🔘 Прогресс-бар / анимация
# ==========================
def reactor_bar(progress: int) -> str:
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
            except Exception:
                pass
        await asyncio.sleep(0.25)

# ==========================
# 🧠 Подписка / Кнопки
# ==========================
async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
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
        except Exception:
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

    # файлы в temp
    files = []
    try:
        files = os.listdir(TEMP_DIR)
    except Exception:
        files = []
    total_size_mb = 0.0
    for f in files:
        try:
            total_size_mb += os.path.getsize(os.path.join(TEMP_DIR, f)) / (1024 * 1024)
        except Exception:
            pass

    users24, videos24 = get_stats_last_24h()

    qsize = queue.qsize()
    await message.reply(
        "💠 KrugoBot активен!\n"
        f"⏱ Аптайм: {hours} ч {minutes} мин\n"
        f"👥 За 24 ч: {users24} пользователей\n"
        f"🎬 Отправлено видео: {videos24}\n"
        f"🧰 Очередь: {qsize} в ожидании\n"
        f"📂 В temp_videos: {len(files)} файлов ({total_size_mb:.1f} МБ)\n"
        "🌐 Keep-alive OK, авто-рестарт включён ✅"
    )

# ==========================
# 📥 Приём видео (POST → очередь)
# ==========================
@dp.message(lambda m: m.video or m.document)
async def handle_incoming_video(message: types.Message):
    user_id = message.from_user.id

    # Если было "подтверждение подписки" — удалим
    mid = last_confirm_messages.pop(user_id, None)
    if mid:
        try:
            await bot.delete_message(message.chat.id, mid)
        except Exception:
            pass

    # Проверяем подписку
    if not await check_subscription(user_id):
        sent = await message.reply(
            "🚫 Доступ ограничен!\n\nПодпишись на канал, чтобы использовать бота 👇",
            reply_markup=get_sub_button()
        )
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Получаем file_id и file_info
    try:
        file_id = (message.video or message.document).file_id
        file_info = await bot.get_file(file_id)
    except Exception as e:
        await message.reply(f"❌ Не удалось получить файл: {e}")
        return

    # Лимит размера
    if file_info.file_size and file_info.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply(f"⚠️ Файл больше {MAX_FILE_SIZE_MB} МБ! Отправь меньший файл.")
        return

    # Скачиваем файл локально
    src_path = os.path.join(TEMP_DIR, os.path.basename(file_info.file_path))
    try:
        await bot.download_file(file_info.file_path, destination=src_path)
    except Exception as e:
        await message.reply(f"❌ Ошибка скачивания: {e}")
        return

    # Проверяем длительность (ffprobe)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", src_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        duration = float(result.stdout or 0)
    except Exception:
        duration = 0.0

    if duration > MAX_DURATION:
        try:
            os.remove(src_path)
        except Exception:
            pass
        await message.reply(f"⚠️ Ошибка: видео длиннее {MAX_DURATION} секунд.")
        return

    # Сообщение статуса
    status_msg = await message.reply("⚙️ Запуск реактора...")

    # Ставим в очередь
    await queue.put(TaskItem(
        chat_id=message.chat.id,
        user_id=user_id,
        original_message_id=message.message_id,
        status_message_id=status_msg.message_id,
        src_path=src_path
    ).__dict__)

    pos = queue.qsize()  # позиция после добавления
    if pos > 1:
        await status_msg.edit_text(f"⏳ Видео добавлено в очередь ({pos} в ожидании).")
    else:
        await status_msg.edit_text("🌀 Начинаю обработку твоего видео...")

# ==========================
# 🛠 Воркёр очереди
# ==========================
async def worker_loop():
    """Один воркёр: обрабатывает видео последовательно."""
    while True:
        item: dict = await queue.get()
        task = TaskItem(**item)

        try:
            # Обновим статус
            try:
                status_msg = await bot.edit_message_text(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                    text="🌀 Обработка началась..."
                )
            except Exception:
                # получим сам объект сообщения для анимации, если не удалось — создадим новое
                try:
                    status_msg = await bot.send_message(task.chat_id, "🌀 Обработка началась...")
                except Exception:
                    status_msg = None

            # Анимация
            if status_msg:
                try:
                    await animate_progress(status_msg)
                except Exception:
                    pass

            # Финализация (визуальные этапы)
            if status_msg:
                try:
                    await bot.edit_message_text(
                        chat_id=task.chat_id,
                        message_id=status_msg.message_id,
                        text="✨ Рендер завершён!\n🌀 Финализация видео... Пару секунд!"
                    )
                except Exception:
                    pass
                await asyncio.sleep(1.5)
                for phase in ["💫 Сжимаем видео...", "🔥 Завершаем упаковку...", "✅ Готово!"]:
                    try:
                        await bot.edit_message_text(
                            chat_id=task.chat_id,
                            message_id=status_msg.message_id,
                            text=phase
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(0.8)

            # Готовим путь для кружка
            video_note_path = os.path.join(TEMP_DIR, f"video_note_{task.original_message_id}.mp4")

            # ffmpeg → кружок
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", task.src_path,
                "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=512:512",
                "-preset", "ultrafast", "-c:v", "libx264", "-c:a", "aac", video_note_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await proc.wait()

            # Отправляем кружок
            await bot.send_video_note(task.chat_id, video_note=FSInputFile(video_note_path))

            # Статистика
            add_video_event(task.user_id)

            # Удаляем исходное сообщение пользователя
            try:
                await bot.delete_message(task.chat_id, task.original_message_id)
            except Exception:
                pass

            # Удаляем статусное сообщение
            if status_msg:
                try:
                    await bot.delete_message(task.chat_id, status_msg.message_id)
                except Exception:
                    pass

        except Exception as e:
            # Если конфликт — подождём и продолжим «мягко»
            if "Conflict" in str(e):
                print("⚠️ TelegramConflictError в воркёре. Ждём 10 сек и продолжаем.")
                await asyncio.sleep(10)
            else:
                # попробуем уведомить пользователя
                try:
                    await bot.send_message(task.chat_id, f"❌ Ошибка обработки: {e}")
                except Exception:
                    pass
        finally:
            # Удаляем файлы
            for p in (task.src_path, os.path.join(TEMP_DIR, f"video_note_{task.original_message_id}.mp4")):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            queue.task_done()

# ==========================
# 🧹 Авто-очистка temp
# ==========================
def _clean_temp_once():
    now = time.time()
    for f in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, f)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > 900:  # 15 минут
                os.remove(path)
                print(f"🧹 Удалён старый файл: {f}")
        except Exception:
            pass

def _clean_temp_loop():
    while True:
        _clean_temp_once()
        time.sleep(900)  # каждые 15 минут

# ==========================
# 🌐 Keep-alive сервер
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
# 🚀 Точка входа
# ==========================
if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    print("═════════════════════════════════════════════")
    print("✅ BOT STARTED — Telegram Video Reactor active")
    print("═════════════════════════════════════════════")

    # Поток: авто-очистка
    threading.Thread(target=_clean_temp_loop, daemon=True).start()

    # Поток: keep-alive сервер
    threading.Thread(target=run_keepalive_server, daemon=True).start()

    async def main():
        global _worker_task
        # Стартуем воркёр очереди
        _worker_task = asyncio.create_task(worker_loop())
        # Стартуем polling. Если Conflict — мягко подождём и перезапустим.
        while True:
            try:
                await dp.start_polling(bot)
            except Exception as e:
                if "Conflict" in str(e):
                    print("⚠️ Conflict при polling. Жду 10 сек и пробую подключиться снова...")
                    await asyncio.sleep(10)
                    continue
                print(f"⚠️ Ошибка polling: {e}. Перезапуск через 5 сек.")
                await asyncio.sleep(5)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Завершение работы...")
