import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# --- ИМПОРТЫ ТВОЕГО ПРОЕКТА ---
from src.core.config import settings
from src.db.database import Base
# Обязательно импортируем модели, чтобы Alembic увидел таблицы для autogenerate
import src.db.models 

# Объект конфигурации Alembic (читает alembic.ini)
config = context.config

# Настройка логирования из alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ПОДСТАВЛЯЕМ URL ИЗ НАШИХ НАСТРОЕК
# Это позволяет не хранить пароли в файле alembic.ini
config.set_main_option("sqlalchemy.url", settings.database_url)

# Метаданные моделей
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Запуск миграций в 'offline' режиме (генерация SQL скрипта)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    """Синхронная обертка для выполнения миграций."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    """Запуск миграций в 'online' режиме (подключение к живой БД)."""
    # Создаем асинхронный движок из конфига
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Так как алембик синхронный внутри, используем run_sync
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    # Запуск асинхронного цикла
    try:
        asyncio.run(run_migrations_online())
    except (KeyboardInterrupt, SystemExit):
        pass