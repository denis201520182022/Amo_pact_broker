import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from src.core.config import settings

# Создаем папку для логов, если её нет
LOGS_DIR = "logs"
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

class CustomFormatter(logging.Formatter):
    """Форматтер для красивого вывода логов"""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    
    def format(self, record):
        return super().format(record)

def setup_logging(service_name: str):
    """
    Настройка логирования:
    service_name: 'api', 'worker', 'scheduler' или 'tg_bot'
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # 1. Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = []

    # 2. Вывод в консоль (Docker stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
    root_logger.addHandler(console_handler)

    # 3. Запись в файл (только для нашего кода 'src')
    file_name = f"{LOGS_DIR}/{service_name}.log"
    file_handler = RotatingFileHandler(
        file_name, 
        maxBytes=10 * 1024 * 1024,  # Увеличил до 10 MB
        backupCount=5,             # Хранить 5 старых файлов
        encoding="utf-8"
    )
    file_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
    
    project_logger = logging.getLogger("src")
    project_logger.propagate = True 
    project_logger.addHandler(file_handler)

    # --- Настройка уровней для сторонних библиотек ---
    # Убираем лишний спам, оставляем только важное (WARNING и выше)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aioredis").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)     # Скрываем логи запросов к OpenAI/Pact/Amo
    logging.getLogger("aiogram").setLevel(logging.INFO)     # ТГ бот пусть пишет общие инфо
    logging.getLogger("taskiq").setLevel(logging.INFO)      # Логи задач

    return project_logger

# Глобальный объект логгера
logger = logging.getLogger("src")