# -*- coding: utf-8 -*-
import os
import time
import asyncio
import threading
import subprocess
from typing import List, Tuple, Dict
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
# 📊 Статистика
# ==========================
_events_last_24h: List[Tuple[float, int]] = []

def _prune_events():
    cutoff = time.time() - 24 * 3600
    while _events_last_24h and _events_last_24h[0][0] < cutoff:
        _events_last_24h.pop(0)

def add_video_event(user_id: int):
    _events_last_24h.append((time.time(), user_id))
    _prune_events()

def get_stats_last_24h() -> Tuple[int, int]:
    _prune_events()
    users = {u for _, u in _events_last_24h}
    return len(users), len(_events_last_24h)

# ==========================
# 🧠 Проверка подписки (исправлено)
# ==========================
_sub_cache: Dict[int, Tuple[bool, float]] = {}
SUB_CACHE_TTL = 6 * 3600  # 6 часов

async def check_subscription(user_id: int, force_refresh: bool = False) -> bool:
    """
    Проверяет подписку пользователя.
    Если force_refresh=True — игнорирует кэш и делает реальный запрос с несколькими попытками.
    """
    now = time.time()
    if not force_refresh:
        cached = _sub_cache.get(user_id)
        if cached and now - cached[1] < SUB_CACHE_TTL:
            return cached[0]

    try:
        # Telegram иногда возвращает старый статус, поэтому 3 попытки
        for _ in range(3):
            member = await bot.get_chat_member(CHANNEL_ID, user_id)
            if member.status in ("member", "administrator", "creator"):
                _sub_cache[user_id] = (True, now)
                return True
            await asyncio.sleep(1.2)  # подождать, если только что подписался
        _sub_cache[user_id] = (False, now)
        return False
    except Exception:
        return False

def get_sub_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Подписаться", url="https://t.me/Krugobotchanel"),
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
    ]])

last_confirm_messages: Dict[int, int] = {}

@dp.callback_query(F.data == "check_sub")
async def on_check_sub(cb: types.CallbackQuery):
    user = cb.from_user
    if await check_subscription(user.id, force_refresh=True):
        try:
            await cb.message.delete()
        except:
            pass
        m = await cb.message.answer("✅ Подписка подтверждена! Можешь отправить видео 🎥")
        last_confirm_messages[user.id] = m.message_id
    else:
        await cb.answer(
            "Проверь ещё раз через пару секунд — Telegram обновляет статус не сразу ⏳",
            show_alert=True
        )

# ==========================
# 🧵 Активные пользователи
# ==========================
active_users: set[int] = set()

# ==========================
# 🔘 Прогрессбар
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
            except:
                pass
        await asyncio.sleep(0.25)

# ==========================
# 🎬 Команды
# ==========================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.reply(
        f"⚡ Привет!\n"
        f"Отправь видео до {MAX_DURATION} секунд и не более {MAX_FILE_SIZE_MB} МБ — я сделаю кружок ⭕\n\n"
        f"⚠️ За раз можно только одно видео.\n"
        "Проект создан в стиле Video Reactor 💠"
    )

@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uptime = int(time.time() - PROCESS_START_TS)
    hours, minutes = uptime // 3600, (uptime % 3600) // 60
    users24, videos24 = get_stats_last_24h()
    temp_files = len(os.listdir(TEMP_DIR))
    await message.reply(
        f"💠 KrugoBot активен!\n"
        f"⏱ Аптайм: {hours} ч {minutes} мин\n"
        f"👥 За 24 ч: {users24} пользователей\n"
        f"🎬 Отправлено видео: {videos24}\n"
        f"⚙️ Активных пользователей: {len(active_users)}\n"
        f"📂 В TEMP: {temp_files} файлов\n"
        "🌐 Keep-alive OK ✅"
    )

# ==========================
# 🎥 Обработка видео
# ==========================
@dp.message(lambda m: m.video or m.document)
async def handle_video(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_users:
        await message.reply("⏳ Дождись завершения обработки предыдущего видео перед отправкой нового.")
        return
    active_users.add(user_id)

    if not await check_subscription(user_id):
        await message.reply(
            "🚫 Доступ ограничен!\nПодпишись на канал 👇",
            reply_markup=get_sub_button()
        )
        active_users.discard(user_id)
        return

    src_path = None
    video_note_path = None
    status_msg = None

    try:
        file_id = (message.video or message.document).file_id
        file_info = await bot.get_file(file_id)

        if file_info.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            await message.reply(f"⚠️ Файл больше {MAX_FILE_SIZE_MB} МБ!")
            return

        uniq = f"{user_id}_{int(time.time())}"
        src_path = os.path.join(TEMP_DIR, f"src_{uniq}.mp4")
        video_note_path = os.path.join(TEMP_DIR, f"note_{uniq}.mp4")

        await bot.download_file(file_info.file_path, destination=src_path)

        status_msg = await message.reply("⚙️ Запуск реактора...")
        await animate_progress(status_msg)

        await status_msg.edit_text("✨ Рендер завершён!\n🌀 Финализация видео...")
        await asyncio.sleep(1.1)
        for phase in ["💫 Сжимаем видео...", "🔥 Завершаем упаковку...", "✅ Готово!"]:
            await status_msg.edit_text(phase)
            await asyncio.sleep(1.2)

        # FFmpeg — безопасная запись
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-analyzeduration", "0", "-probesize", "32M",
            "-i", src_path,
            "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=480:480:flags=lanczos",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast", "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart",
            video_note_path,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await proc.wait()

        # 🔒 Принудительная синхронизация
        if os.path.exists(video_note_path):
            with open(video_note_path, "rb") as f:
                os.fsync(f.fileno())

        # 🕓 Проверяем доступность файла
        for _ in range(6):
            if os.path.exists(video_note_path) and os.path.getsize(video_note_path) > 0:
                break
            await asyncio.sleep(0.5)

        for text in ["📤 Отправка видео...", "☁️ Это может занять пару секунд..."]:
            await status_msg.edit_text(text)
            await asyncio.sleep(1.5)

        async def safe_send():
            delay = 2
            for i in range(3):
                try:
                    await bot.send_video_note(message.chat.id, video_note=FSInputFile(video_note_path))
                    return
                except Exception as e:
                    if i == 2:
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2

        await safe_send()
        await status_msg.edit_text("✅ Отправлено!")

        add_video_event(user_id)
        await bot.delete_message(message.chat.id, message.message_id)
        await bot.delete_message(message.chat.id, status_msg.message_id)

    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
    finally:
        active_users.discard(user_id)
        for p in [src_path, video_note_path]:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except:
                pass

# ==========================
# 🌐 Keep-alive
# ==========================
class LoggingHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        ip = self.client_address[0]
        print(f"🔁 Пинг от {ip}")

def run_keepalive_server():
    server = HTTPServer(("0.0.0.0", KEEPALIVE_PORT), LoggingHandler)
    print(f"🌐 Keep-alive server на порту {KEEPALIVE_PORT}")
    server.serve_forever()

# ==========================
# 🚀 Запуск
# ==========================
if __name__ == "__main__":
    os.system("cls" if os.name == "nt" else "clear")
    print("═════════════════════════════════════════════")
    print("✅ BOT STARTED — Video Reactor stable build")
    print("═════════════════════════════════════════════")

    threading.Thread(target=run_keepalive_server, daemon=True).start()

    async def main():
        while True:
            try:
                await dp.start_polling(bot)
            except Exception as e:
                print(f"⚠️ Ошибка polling: {e}. Перезапуск через 10с.")
                await asyncio.sleep(10)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Завершение работы...")
