# Используем стабильную версию Python
FROM python:3.11-slim

# Настройки Python
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
# Указываем корень проекта для корректного импорта модулей из папки src
ENV PYTHONPATH=/app

WORKDIR /app

# Системные зависимости (для psycopg2 и сборки пакетов)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем и устанавливаем зависимости (кэшируем этот слой)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Команды определены в docker-compose.yml