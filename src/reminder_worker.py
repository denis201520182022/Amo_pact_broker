import asyncio
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_
from src.core.logging import setup_logging
from src.core.config import settings
from src.db.database import async_session_maker
from src.db.models import Dialogue
from src.services.pact.pact_api import pact_api

logger = setup_logging("reminder_worker")

# Конфигурация напоминаний
REMINDERS = {
    1: {"delay": 5 * 60, "text": "Вы еще здесь? Подскажите, остались ли у вас вопросы по документам?"},
    2: {"delay": 15 * 60, "text": "Мы все еще ждем ваши отчеты, чтобы начать анализ. Это займет всего пару минут!"},
    3: {"delay": 30 * 60, "text": "Если у вас возникли сложности с получением отчетов БКИ — напишите нам, мы поможем."},
    4: {"delay": 2 * 3600, "text": "Напоминаем, что без анализа вашей кредитной истории мы не сможем подобрать выгодные условия."},
    5: {"delay": 12 * 3600, "text": "Здравствуйте! Вы планировали прислать документы. Актуально?"},
    6: {"delay": 24 * 3600, "text": "Прошли сутки, мы все еще на связи и готовы помочь с вашим кредитным вопросом."},
    7: {"delay": 48 * 3600, "text": "Это последнее напоминание. Если вопрос станет актуальным — пишите, будем рады помочь. Всего доброго!"}
}

def get_msk_time() -> str:
    """Московское время в формате проекта"""
    msk_tz = timezone(timedelta(hours=3))
    return datetime.now(msk_tz).strftime("%Y-%m-%d %H:%M:%S MSK")

def is_working_hours() -> bool:
    """Работаем с 09:00 до 20:00 по МСК"""
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    return 9 <= now_msk.hour < 20

async def check_reminders():
    logger.info("🚀 Reminder Worker started")
    
    while True:
        try:
            if not is_working_hours():
                logger.debug("🌙 Ночь в Москве. Спим 10 минут...")
                await asyncio.sleep(600)
                continue

            async with async_session_maker() as session:
                # Ищем активные диалоги (кроме завершенных)
                query = select(Dialogue).where(
                    and_(
                        Dialogue.status == "active",
                        Dialogue.reminder_level < max(REMINDERS.keys()) + 1
                    )
                )
                result = await session.execute(query)
                dialogues = result.scalars().all()

                now_utc = datetime.now(timezone.utc)

                for dialogue in dialogues:
                    next_level = dialogue.reminder_level + 1
                    if next_level not in REMINDERS:
                        dialogue.reminder_level = 8
                        continue

                    config = REMINDERS[next_level]
                    time_passed = (now_utc - dialogue.last_message_at).total_seconds()

                    if time_passed >= config["delay"]:
                        logger.info(f"🔔 Отправка напоминания #{next_level} для {dialogue.pact_conversation_id}")
                        
                        success = await pact_api.send_message(
                            conversation_id=dialogue.pact_conversation_id,
                            message=config["text"]
                        )

                        if success:
                            # 1. Формируем "богатую" запись ассистента
                            # Для напоминания state и new_state совпадают (мы не двигаем граф)
                            new_entry = {
                                "role": "assistant",
                                "state": dialogue.current_state,
                                "new_state": dialogue.current_state,
                                "content": config["text"],
                                "message_id": f"reminder_{next_level}_{int(time.time())}",
                                "timestamp_msk": get_msk_time(),
                                "extracted_data": dict(dialogue.extracted_data or {})
                            }
                            
                            # 2. Обновляем модель
                            dialogue.reminder_level = next_level
                            dialogue.last_message_at = now_utc
                            dialogue.history = list(dialogue.history) + [new_entry]

                await session.commit()

        except Exception as e:
            logger.error(f"🚨 Ошибка в reminder_worker: {e}", exc_info=True)
        
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(check_reminders())