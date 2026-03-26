import asyncio
import sys
from aiogram import Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, update

from src.services.telegram.tg import tg_service
from src.core.logging import setup_logging
from src.db.database import async_session_maker
from src.db.models import Dialogue, AppSettings, Account
from src.core.config import settings

logger = setup_logging("tg_bot")

# --- СОСТОЯНИЯ ДЛЯ ВВОДА ДАННЫХ ---
class AdminStates(StatesGroup):
    input_balance = State()
    input_tariff = State()

dp = Dispatcher()

# --- MIDDLEWARE ДЛЯ ПРОВЕРКИ ДОСТУПА ---
@dp.message.outer_middleware()
async def auth_middleware(handler, event, data):
    user_id = event.from_user.id
    if user_id not in tg_service.user_ids:
        logger.warning(f"🚫 Попытка доступа от постороннего: {user_id}")
        return # Просто игнорим
    return await handler(event, data)

# --- КЛАВИАТУРА ГЛАВНОГО МЕНЮ ---
def get_main_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="stats")
    builder.button(text="💰 Пополнить баланс", callback_data="add_balance")
    builder.button(text="⚙️ Изменить тариф", callback_data="set_tariff")
    builder.button(text="🛑 Остановка", callback_data="system_stop")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать в Админ-панель AI Broker!",
        reply_markup=get_main_kb(),
        parse_mode="HTML"
    )

# --- ОБРАБОТКА СТАТИСТИКИ ---
@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    async with async_session_maker() as session:
        # Считаем данные из БД
        total_dialogs = await session.scalar(select(func.count(Dialogue.id)))
        active_dialogs = await session.scalar(select(func.count(Dialogue.id)).where(Dialogue.status == "active"))
        app_settings = await session.scalar(select(AppSettings).limit(1))
        
        text = (
            f"<b>📊 СТАТИСТИКА ПРОЕКТА</b>\n\n"
            f"💰 <b>Текущий баланс:</b> <code>{app_settings.balance}</code> руб.\n"
            f"📑 <b>Всего диалогов:</b> {total_dialogs}\n"
            f"🟢 <b>Активных сейчас:</b> {active_dialogs}\n"
            f"🏷 <b>Стоимость диалога:</b> {app_settings.tariffs.get('dialog_cost', 0)} руб.\n"
            f"🔔 <b>Порог алерта:</b> {app_settings.low_balance_threshold} руб."
        )
        await callback.message.edit_text(text, reply_markup=get_main_kb(), parse_mode="HTML")

# --- ПОПОЛНЕНИЕ БАЛАНСА ---
@dp.callback_query(F.data == "add_balance")
async def start_add_balance(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("💸 Введите сумму пополнения (число):")
    await state.set_state(AdminStates.input_balance)

@dp.message(AdminStates.input_balance)
async def process_balance(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        async with async_session_maker() as session:
            stg = await session.scalar(select(AppSettings).limit(1))
            stg.balance = float(stg.balance) + amount
            # Сбрасываем флаг алерта, если пополнили выше порога
            if stg.balance > stg.low_balance_threshold:
                stg.is_low_balance_alert_sent = False
            await session.commit()
            
        await message.answer(f"✅ Баланс успешно пополнен на {amount} руб.!", reply_markup=get_main_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Ошибка! Введите число (например: 500 или 1000.50)")

# --- ИЗМЕНЕНИЕ ТАРИФА ---
@dp.callback_query(F.data == "set_tariff")
async def start_set_tariff(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🏷 Введите новую стоимость создания диалога (руб):")
    await state.set_state(AdminStates.input_tariff)

@dp.message(AdminStates.input_tariff)
async def process_tariff(message: types.Message, state: FSMContext):
    try:
        new_cost = float(message.text)
        async with async_session_maker() as session:
            stg = await session.scalar(select(AppSettings).limit(1))
            # Обновляем JSON поле tariffs
            new_tariffs = dict(stg.tariffs)
            new_tariffs['dialog_cost'] = new_cost
            stg.tariffs = new_tariffs
            await session.commit()
            
        await message.answer(f"✅ Тариф обновлен: {new_cost} руб. за диалог.", reply_markup=get_main_kb())
        await state.clear()
    except ValueError:
        await message.answer("❌ Ошибка! Введите число.")

# --- ОСТАНОВКА (СИСТЕМНАЯ) ---
@dp.callback_query(F.data == "system_stop")
async def system_stop(callback: types.CallbackQuery):
    await callback.message.answer("🛑 <b>Процесс бота завершается...</b>", parse_mode="HTML")
    logger.critical("!!! БОТ ОСТАНОВЛЕН ЧЕРЕЗ АДМИН-ПАНЕЛЬ !!!")
    sys.exit(0) # Завершает контейнер, docker-compose его перезапустит, если стоит restart: always

async def main():
    logger.info("🚀 Запуск Telegram Admin Bot (с поддержкой прокси)...")
    await tg_service.bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(tg_service.bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Бот остановлен")