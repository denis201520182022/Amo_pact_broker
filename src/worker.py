import asyncio
from typing import Any, Dict, List
from sqlalchemy import select, update
from taskiq_redis import RedisAsyncResultBackend, ListQueueBroker

from src.core.config import settings
from src.core.logging import setup_logging
from src.core.redis_client import redis_manager
from src.db.database import async_session_maker
from src.db.models import Dialogue, AppSettings, Account
from src.services.pact.pact_api import pact_api # Будет реализован следующим шагом
from src.core.redis_client import scheduler
# Настраиваем логи для воркера
logger = setup_logging("worker")

# 1. Настройка брокера TaskIQ (Правильная цепочка вызовов)
result_backend = RedisAsyncResultBackend(redis_url=settings.REDIS_URL)
broker = ListQueueBroker(url=settings.REDIS_URL)

# Важно: Перезаписываем переменную broker результатом настроек
broker = broker.with_result_backend(result_backend)
broker = broker.with_schedule_source(scheduler) 

# Теперь декоратор @broker.task будет использовать полностью настроенный брокер
@broker.task(
    task_name="process_pact_messages",
    retry_on_error=True,
    max_retry=3
)
async def process_pact_messages_task(conversation_id: str):
    """
    Production-ready задача обработки диалога:
    Биллинг -> Синхронизация ID -> Сохранение сообщений -> Ответ ИИ
    """
    logger.info(f"--- [Worker] Начало обработки диалога {conversation_id} ---")
    
    async with async_session_maker() as session:
        try:
            # 1. Проверяем существование диалога
            query = select(Dialogue).where(Dialogue.pact_conversation_id == conversation_id)
            result = await session.execute(query)
            dialogue = result.scalar_one_or_none()

            # 2. Если диалога нет — проверяем баланс и создаем
            if not dialogue:
                logger.info(f"🆕 Новый клиент. Проверка баланса...")
                
                # Получаем настройки биллинга
                stg_query = select(AppSettings).limit(1)
                stg_result = await session.execute(stg_query)
                app_settings = stg_result.scalar_one_or_none()
                
                if not app_settings:
                    logger.error("❌ Критическая ошибка: Таблица AppSettings пуста!")
                    return

                # Стоимость создания диалога из тарифов (поле tariffs: {"dialog_cost": 10.0})
                dialog_cost = app_settings.tariffs.get("dialog_cost", 0)

                # Атомарное списание: вычитаем только если баланс позволяет
                billing_update = (
                    update(AppSettings)
                    .where(AppSettings.id == app_settings.id)
                    .where(AppSettings.balance >= dialog_cost)
                    .values(balance=AppSettings.balance - dialog_cost)
                )
                billing_res = await session.execute(billing_update)
                
                if billing_res.rowcount == 0:
                    logger.warning(f"⚠️ Недостаточно средств для диалога {conversation_id}. Обработка прервана.")
                    return # Прекращаем, задача считается выполненной (не ретраим)

                # Ищем первый активный аккаунт для привязки
                acc_query = select(Account).where(Account.is_active == True).limit(1)
                acc_result = await session.execute(acc_query)
                account = acc_result.scalar_one_or_none()
                
                if not account:
                    logger.error("❌ Нет активных аккаунтов amoCRM в базе!")
                    raise Exception("Account not found")

                # Создаем диалог
                dialogue = Dialogue(
                    pact_conversation_id=conversation_id,
                    account_id=account.id,
                    current_state="START",
                    history=[]
                )
                session.add(dialogue)
                await session.flush() # Получаем ID диалога без коммита
                logger.info(f"💰 Баланс списан (-{dialog_cost}). Диалог создан.")

            # 3. Синхронизация с amoCRM (ищем ID сделки в Redis)
            # Если в диалоге еще нет amo_lead_id, проверяем 'map' ключ от вебхука
            if not dialogue.amo_lead_id:
                amo_id_from_cache = await redis_manager.redis.get(f"map:{conversation_id}")
                if amo_id_from_cache:
                    dialogue.amo_lead_id = str(amo_id_from_cache)
                    logger.info(f"🔗 Связано с amoCRM Lead ID: {amo_id_from_cache}")

            # 4. Перенос сообщений из Redis Buffer в Postgres
            new_messages = await redis_manager.get_buffer(conversation_id)
            if new_messages:
                formatted = [
                    {
                        "role": "user", 
                        "content": m["text"], 
                        "timestamp": asyncio.get_event_loop().time(),
                        "raw_data": m # Сохраняем метаданные на всякий случай
                    } 
                    for m in new_messages
                ]
                
                # Мутируем историю (SQLAlchemy JSONB требует перезаписи списка)
                updated_history = list(dialogue.history)
                updated_history.extend(formatted)
                dialogue.history = updated_history
                
                # Сохраняем всё в БД
                await session.commit() 
                logger.info(f"💾 {len(new_messages)} сообщений перенесено в Postgres.")
                
                # ТОЛЬКО ПОСЛЕ УСПЕШНОГО COMMIT удаляем из Redis
                await redis_manager.delete_buffer(conversation_id)
            else:
                logger.debug("Буфер Redis пуст, продолжаем работу с историей из БД.")

            # 5. Обработка и генерация ответа
            # Проверяем, нужно ли отвечать (последнее сообщение должно быть от user)
            if dialogue.history and dialogue.history[-1]["role"] == "user":
                await perform_logic_and_reply(dialogue, session)
            else:
                logger.info("⏭️ Последнее сообщение не от пользователя, ответ не требуется.")

            await session.commit()
            logger.info(f"🏁 Диалог {conversation_id} успешно обработан.")

        except Exception as e:
            await session.rollback()
            logger.error(f"🚨 Ошибка воркера [ID: {conversation_id}]: {str(e)}", exc_info=True)
            # Пробрасываем ошибку для TaskIQ Retry
            raise e

async def perform_logic_and_reply(dialogue: Dialogue, session):
    """
    Логика формирования ответа и отправка через Pact API
    """
    logger.info(f"🤖 Формирование ответа для диалога {dialogue.id}...")
    
    # --- ВРЕМЕННАЯ ЗАГЛУШКА ВМЕСТО OPENAI ---
    # В будущем здесь будет вызов LangGraph / LLM
    ai_reply_text = "Здравствуйте! Ваше сообщение получено и обрабатывается. Спасибо за ожидание!"
    # ----------------------------------------

    # Отправляем через Pact API
    # Нам нужен ID компании и провайдер (можем взять из последнего сообщения в истории)
    success = await pact_api.send_message(
        conversation_id=dialogue.pact_conversation_id,
        message=ai_reply_text
    )

    if success:
        # Обновляем историю в БД
        new_entry = {
            "role": "assistant",
            "content": ai_reply_text,
            "timestamp": asyncio.get_event_loop().time()
        }
        updated_history = list(dialogue.history)
        updated_history.append(new_entry)
        dialogue.history = updated_history
        logger.info(f"📤 Ответ отправлен в Пакт и сохранен в историю.")
    else:
        logger.error(f"❌ Не удалось отправить ответ в Пакт для {dialogue.pact_conversation_id}")
        raise Exception("Pact send failed")