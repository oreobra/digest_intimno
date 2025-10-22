FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt telethon

# Копирование исходного кода
COPY . .

# Создание пользователя для безопасности
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Переменные окружения по умолчанию
ENV TZ=Europe/Amsterdam
ENV WEEKDAY=0
ENV POST_HOUR=9
ENV POST_MINUTE=0
ENV LOOKBACK_DAYS=7
ENV ITEMS_MAX=6
ENV LANG_PREF=ru

# Запуск приложения
CMD ["python3", "main.py"]