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
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # 1. Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Очищаем старые хендлеры, чтобы не дублировать логи при повторных вызовах
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 2. Вывод в консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
    root_logger.addHandler(console_handler)

    # 3. Настройка логгера проекта
    project_logger = logging.getLogger("src")
    project_logger.propagate = True 
    
    # Удаляем старые файловые хендлеры у логгера src, если они были
    for handler in project_logger.handlers[:]:
        if isinstance(handler, RotatingFileHandler):
            project_logger.removeHandler(handler)

    # Добавляем новый файловый хендлер
    file_name = f"{LOGS_DIR}/{service_name}.log"
    file_handler = RotatingFileHandler(
        file_name, 
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
    project_logger.addHandler(file_handler)

    return project_logger

# Глобальный объект логгера
logger = logging.getLogger("src")