import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from src.core.config import settings

LOGS_DIR = os.path.join(os.getcwd(), "logs")

class CustomFormatter(logging.Formatter):
    # Добавили [%(process)d], чтобы отличать процессы воркера
    fmt = "%(asctime)s | %(levelname)-8s | [%(process)d] %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    def format(self, record):
        return super().format(record)

def setup_logging(service_name: str):
    if not os.path.exists(LOGS_DIR):
        try:
            os.makedirs(LOGS_DIR)
        except: pass

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # 1. Настройка корневого логгера
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Очищаем все хендлеры у корня
    root_logger.handlers = []

    # Консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
    root_logger.addHandler(console_handler)

    # 2. Настройка логгера проекта ("src")
    project_logger = logging.getLogger("src")
    project_logger.setLevel(log_level)
    
    # КРИТИЧЕСКИ ВАЖНО: Удаляем ВСЕ хендлеры, которые могли быть добавлены при импортах
    project_logger.handlers = [] 
    project_logger.propagate = True 

    # Добавляем файл
    file_path = os.path.join(LOGS_DIR, f"{service_name}.log")
    file_handler = RotatingFileHandler(
        file_path, 
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        delay=True 
    )
    file_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
    project_logger.addHandler(file_handler)

    # Тишим библиотеки
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    return project_logger

# Экспортируем логгер
logger = logging.getLogger("src")