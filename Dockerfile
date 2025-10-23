FROM python:3.11-slim

# Устанавливаем ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Настраиваем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Чтобы логи сразу писались в консоль Render
ENV PYTHONUNBUFFERED=1

# Запуск бота
CMD ["python", "bot.py"]
