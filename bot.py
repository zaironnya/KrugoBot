# -*- coding: utf-8 -*-
import os
import time
import asyncio
import threading
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from http.server import SimpleHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# ==========================
# 🔧 Конфиг
# ==========================
TOKEN = os.getenv("TG_TOKEN")                        # Токен из переменной окружения
CHANNEL_ID = -1003223590941                          # Канал для подписки
ADMIN_ID = 1052210475                                # твой Telegram ID
TEMP_DIR = "temp_videos"                             # временные файлы
MAX_DURATION = 60                                    # сек.
MAX_FILE_SIZE_MB = 20                                # МБ (до скачивания)
KEEPALIVE_PORT = int(os.getenv("PORT", 10000))       # Render Free “порт для скана”
SUB_CACHE_TTL = 6 * 3600                             # кэш подписки, 6 часов

os.makedirs(TEMP_DIR, exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher()

PROCESS_START_TS = time.time()

# ==========================
# 📊 Статистика 24ч
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
# 👤 Лимит: 1 видео на пользователя
# ==========================
active_users: set[int] = set()

# ==========================
# 🧠 Подписка (с кэшем)
# ==========================
_sub_cache: Dict[int, Tuple[bool, float]] = {}  # user_id -> (is_ok, ts)

async def check_subscription(user_id: int) -> bool:
    now = time.time()
    cached = _sub_cache.get(user_id)
    if cached and now - cached[1] < SUB_CACHE_TTL:
        return cached[0]
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        ok = m.status in ("member", "administrator", "creator")
    except Exception:
        ok = False
    _sub_cache[user_id] = (ok, now)
    return ok

def get_sub_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Подписаться", url="https://t.me/Krugobotchanel"),
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
    ]])

last_confirm_messages: Dict[int, int] = {}

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
        idx = min(i // 25, len(progress_phrases) - 1)
        text = f"{bar}\n     {i}%\n{progress_phrases[idx]}"
        if text != last:
            try:
                await msg.edit_text(text)
                last = text
            except Exception:
                pass
        await asyncio.sleep(0.25)

# ==========================
# 🗣 Команды
# ==========================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        f"⚡ Привет!\n"
        f"Отправь видео до {MAX_DURATION} секунд и не более {MAX_FILE_SIZE_MB} МБ — я сделаю кружок ⭕\n\n"
        f"⚠️ За раз можно только одно видео. Дождись завершения перед следующим.\n"
        "Проект в стиле Video Reactor 💠"
    )

@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uptime = int(time.time() - PROCESS_START_TS)
    hours, minutes = uptime // 3600, (uptime % 3600) // 60
    files = []
    try:
        files = os.listdir(TEMP_DIR)
    except Exception:
        pass
    total_size_mb = 0.0
    for f in files:
        p = os.path.join(TEMP_DIR, f)
        try:
            total_size_mb += os.path.getsize(p) / (1024 * 1024)
        except Exception:
            pass
    users24, videos24 = get_stats_last_24h()
    await message.reply(
        "💠 KrugoBot активен!\n"
        f"⏱ Аптайм: {hours} ч {minutes} мин\n"
        f"👥 За 24 ч: {users24} пользователей\n"
        f"🎬 Отправлено видео: {videos24}\n"
        f"⚙️ Активных пользователей: {len(active_users)}\n"
        f"📂 TEMP: {len(files)} файлов ({total_size_mb:.1f} МБ)\n"
        "🌐 Keep-alive OK, авто-рестарт включён ✅"
    )

# ==========================
# 🎥 Обработка видео (1 пользователь → 1 активная задача)
# ==========================
@dp.message(lambda m: m.video or m.document)
async def handle_video(message: types.Message):
    user_id = message.from_user.id

    # Не даём спамить, пока идёт их предыдущая задача
    if user_id in active_users:
        await message.reply("⏳ Дождись завершения обработки предыдущего видео перед новым.")
        return
    active_users.add(user_id)

    # Убираем старое «подтверждение»
    mid = last_confirm_messages.pop(user_id, None)
    if mid:
        try:
            await bot.delete_message(message.chat.id, mid)
        except Exception:
            pass

    # Подписка
    if not await check_subscription(user_id):
        sent = await message.reply(
            "🚫 Доступ ограничен!\nПодпишись на канал, чтобы использовать бота 👇",
            reply_markup=get_sub_button()
        )
        try:
            await message.delete()
        except Exception:
            pass
        active_users.discard(user_id)
        return

    src_path = None
    video_note_path = None

    try:
        file_id = (message.video or message.document).file_id
        file_info = await bot.get_file(file_id)

        # Размер до скачивания
        if file_info.file_size and file_info.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            await message.reply(f"⚠️ Файл больше {MAX_FILE_SIZE_MB} МБ!")
            return

        # Уникальные имена
        uniq = f"{message.chat.id}_{message.message_id}_{int(time.time())}"
        src_path = os.path.join(TEMP_DIR, f"src_{uniq}.mp4")
        video_note_path = os.path.join(TEMP_DIR, f"note_{uniq}.mp4")

        # Скачиваем
        await bot.download_file(file_info.file_path, destination=src_path)

        # Длительность
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", src_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
            )
            duration = float(res.stdout or 0)
        except Exception:
            duration = 0.0

        if duration > MAX_DURATION:
            try:
                os.remove(src_path)
            except Exception:
                pass
            await message.reply(f"⚠️ Ошибка: видео длиннее {MAX_DURATION} секунд.")
            return

        # Статус и анимация
        status_msg = await message.reply("⚙️ Запуск реактора...")
        await animate_progress(status_msg)

        # Финальные этапы
        await status_msg.edit_text("✨ Рендер завершён!\n🌀 Финализация видео...")
        await asyncio.sleep(1.2)
        for phase in ["💫 Сжимаем видео...", "🔥 Завершаем упаковку...", "✅ Готово!"]:
            try:
                await status_msg.edit_text(phase)
            except Exception:
                pass
            await asyncio.sleep(1.2)

        # ffmpeg (надёжные параметры для Free-инстанса)
        # баланс: скорость / стабильность / размер
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-analyzeduration", "0", "-probesize", "32M",
            "-i", src_path,
            "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=480:480:flags=lanczos",
            "-pix_fmt", "yuv420p",
            "-b:v", "1M", "-bufsize", "1M", "-maxrate", "1M",
            "-threads", "2",
            "-preset", "ultrafast", "-tune", "zerolatency",
            "-movflags", "+faststart",
            "-c:v", "libx264", "-c:a", "aac",
            video_note_path,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await proc.wait()

        # Маленькая пауза — файловая система точно всё сбросит
        await asyncio.sleep(0.3)
        # Проверим, что файл реально есть и не пуст
        if not (os.path.exists(video_note_path) and os.path.getsize(video_note_path) > 0):
            raise RuntimeError("ffmpeg produced empty output")

        # Отправочная сцена с «тянущим» UI
        try:
            await status_msg.edit_text("📤 Отправка видео...")
        except Exception:
            pass
        await asyncio.sleep(1.2)
        try:
            await status_msg.edit_text("☁️ Это может занять пару секунд…")
        except Exception:
            pass
        await asyncio.sleep(1.4)

        # Надёжная отправка с ретраями (устраняет ClientOSError)
        async def send_note_with_retries(path: str, chat_id: int, retries: int = 2):
            delay = 2
            for attempt in range(retries + 1):
                try:
                    await bot.send_video_note(chat_id, video_note=FSInputFile(path))
                    return
                except Exception as e:
                    if attempt >= retries:
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2  # экспоненциальная пауза

        await send_note_with_retries(video_note_path, message.chat.id)

        # Готово!
        try:
            await status_msg.edit_text("✅ Отправлено!")
        except Exception:
            pass

        # Статистика + клинап чатов
        add_video_event(user_id)
        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        try:
            await bot.delete_message(message.chat.id, status_msg.message_id)
        except Exception:
            pass

    except Exception as e:
        # Если конфликт — это “второй экземпляр polling”, подождём и продолжим жить
        if "Conflict" in str(e):
            print("⚠️ TelegramConflictError: мягкая пауза 10с.")
            await asyncio.sleep(10)
        else:
            await message.reply(f"❌ Ошибка: {e}")
    finally:
        active_users.discard(user_id)
        # Файлы убираем максимально надёжно
        for p in (src_path, video_note_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

# ==========================
# 🌐 Keep-alive (Render Free)
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
# 🚀 Запуск с авто-восстановлением
# ==========================
if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    print("═════════════════════════════════════════════")
    print("✅ BOT STARTED — Telegram Video Reactor active")
    print("═════════════════════════════════════════════")

    # Keep-alive сервер — отдельным демоном
    threading.Thread(target=run_keepalive_server, daemon=True).start()

    async def main():
        # бесконечный цикл polling с мягкими бэкоффами
        backoff = 5
        while True:
            try:
                await dp.start_polling(bot)
                backoff = 5  # если успешно отработали — сбросим
            except Exception as e:
                text = str(e)
                if "Conflict" in text:
                    # второй читатель getUpdates — ждём и продолжаем
                    wait = 10
                else:
                    # нестабильность сети/рендера — растущий бэкофф
                    wait = backoff
                    backoff = min(backoff * 2, 60)
                print(f"⚠️ Polling error: {e}. Retry in {wait}s.")
                await asyncio.sleep(wait)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Завершение работы...")
