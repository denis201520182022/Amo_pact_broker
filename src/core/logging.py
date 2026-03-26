import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from src.core.config import settings

# Определяем пути абсолютно для Docker
LOGS_DIR = os.path.join(os.getcwd(), "logs")

class CustomFormatter(logging.Formatter):
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    def format(self, record):
        return super().format(record)

def setup_logging(service_name: str):
    if not os.path.exists(LOGS_DIR):
        try:
            os.makedirs(LOGS_DIR)
        except: pass

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Очищаем только если нет хендлеров, чтобы не дублировать в консоль
    if not root_logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(CustomFormatter(CustomFormatter.fmt))
        root_logger.addHandler(console_handler)

    # Настраиваем логгер проекта ("src")
    project_logger = logging.getLogger("src")
    project_logger.setLevel(log_level)
    project_logger.propagate = True 

    # Удаляем существующие файловые хендлеры
    for h in project_logger.handlers[:]:
        if isinstance(h, RotatingFileHandler):
            project_logger.removeHandler(h)

    # Добавляем файл с задержкой (delay=True), чтобы подпроцессы TaskIQ не конфликтовали
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

    # Тишим спам
    for lib in ["uvicorn.access", "sqlalchemy.engine", "aioredis", "httpx", "httpcore"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

    return project_logger