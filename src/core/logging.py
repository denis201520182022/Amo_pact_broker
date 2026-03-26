import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from src.core.config import settings

# Жесткий путь для Docker
LOGS_DIR = "/app/logs"

class CustomFormatter(logging.Formatter):
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    def format(self, record):
        return super().format(record)

def setup_logging(service_name: str):
    # Создаем папку, если её нет
    if not os.path.exists(LOGS_DIR):
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
        except: pass

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # Настраиваем наш логгер проекта
    project_logger = logging.getLogger("src")
    project_logger.setLevel(log_level)
    
    # КРИТИЧЕСКИ ВАЖНО: 
    # 1. Запрещаем пробрасывать логи в Root (TaskIQ их больше не перехватит)
    project_logger.propagate = False 
    # 2. Очищаем все хендлеры, чтобы не дублировать
    project_logger.handlers = []

    # Создаем форматтер
    formatter = CustomFormatter()

    # ХЕНДЛЕР 1: В файл (теперь без delay для теста, чтобы файл создался сразу)
    file_path = os.path.join(LOGS_DIR, f"{service_name}.log")
    file_handler = RotatingFileHandler(
        file_path, 
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    project_logger.addHandler(file_handler)

    # ХЕНДЛЕР 2: В консоль (чтобы ты видел логи в docker logs)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    project_logger.addHandler(console_handler)

    # Тишим спам библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    return project_logger

# Объект для импортов
logger = logging.getLogger("src")