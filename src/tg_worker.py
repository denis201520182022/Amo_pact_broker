import asyncio
from aiogram import Dispatcher, types
from aiogram.filters import CommandStart
from src.services.telegram.tg import tg_service
from src.core.logging import setup_logging

# Настраиваем логи для ТГ-процесса
logger = setup_logging("tg_bot")

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: types.Message):
    """Ответ на /start и логгирование ID пользователя (чтобы узнать его)"""
    user_id = message.from_user.id
    await message.answer(
        f"Привет! Твой Telegram ID: <code>{user_id}</code>\n"
        f"Добавь его в .env файл, чтобы получать уведомления."
    )
    logger.info(f"Бот запущен пользователем {user_id}")

async def main():
    logger.info("🚀 Запуск Telegram Bot процесса...")
    # Удаляем вебхуки, если были, и запускаем лонг-поллинг
    await tg_service.bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(tg_service.bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Бот остановлен")