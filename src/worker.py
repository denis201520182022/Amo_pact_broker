import asyncio
from typing import Any, Dict, List
from sqlalchemy import select, update
from taskiq_redis import RedisAsyncResultBackend, ListQueueBroker
from src.services.telegram.tg import tg_service
from src.core.config import settings
from src.core.logging import setup_logging
from src.core.redis_client import redis_manager
from src.db.database import async_session_maker
from src.db.models import Dialogue, AppSettings, Account
from src.services.pact.pact_api import pact_api # Будет реализован следующим шагом
from src.core.redis_client import scheduler
from decimal import Decimal
from src.logic.graph import app_graph
from src.logic.states import Steps, DialogueState
# Также нам понадобятся настройки для логики CRM
from src.logic.graph import SETTINGS_DATA
from src.services.amocrm.amo_api import amo_api
# Настраиваем логи для воркера
from sqlalchemy import func
from src.services.telegram.tg import tg_service
  # Укажи правильный путь до файла
logger = setup_logging("worker")
import time
from datetime import datetime, timezone, timedelta

def get_msk_time() -> str:
    """Возвращает текущее время по Москве в нужном формате"""
    msk_tz = timezone(timedelta(hours=3))
    return datetime.now(msk_tz).strftime("%Y-%m-%d %H:%M:%S MSK")
# 1. Настройка брокера TaskIQ
result_backend = RedisAsyncResultBackend(redis_url=settings.REDIS_URL)

# Создаем брокер и сразу привязываем бекенд результатов и источник расписания
broker = (
    ListQueueBroker(url=settings.REDIS_URL)
    .with_result_backend(result_backend)
)

# Явно устанавливаем источник расписания для поддержки .schedule_by_delay()
# broker.schedule_source = scheduler 


@broker.on_event("startup")
async def worker_startup(state):
    """Инициализация ресурсов при старте воркера"""
    logger.info("👷 Worker starting up...")
    await redis_manager.connect()
    # Если ты используешь базу данных напрямую в задачах, 
    # убедись, что движок SQLAlchemy тоже готов (у тебя сессия создается в задаче, так что ок)

@broker.on_event("shutdown")
async def worker_shutdown(state):
    """Закрытие ресурсов при остановке воркера"""
    logger.info("👷 Worker shutting down...")
    await redis_manager.disconnect()
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
    
    if not redis_manager.redis:
        logger.info(f"🔄 [Worker] Реинициализация Redis для задачи {conversation_id}")
        await redis_manager.connect()
    # -----------------------

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
                raw_cost = app_settings.tariffs.get("dialog_cost", 0)
                dialog_cost = Decimal(str(raw_cost)) # Безопасное приведение через строку

                # Атомарное списание
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

                # --- ПРОВЕРКА ПОРОГА БАЛАНСА ДЛЯ АЛЕРТА ---
                # Получаем обновленный баланс из объекта app_settings после списания
                # (SQLAlchemy обновит значение в объекте после execute)
                current_balance = app_settings.balance - dialog_cost
                
                if (current_balance <= app_settings.low_balance_threshold and 
                    not app_settings.is_low_balance_alert_sent):
                    
                    alert_text = (
                        f"⚠️ <b>ВНИМАНИЕ: НИЗКИЙ БАЛАНС</b>\n\n"
                        f"Текущий остаток: <code>{current_balance}</code> руб.\n"
                        f"Пожалуйста, пополните счет для бесперебойной работы бота."
                    )
                    # Отправляем всем пользователям из TELEGRAM_USER_IDS
                    asyncio.create_task(tg_service.send_global_notification(alert_text))
                    
                    # Ставим флаг, чтобы не отправлять при каждом следующем списании
                    app_settings.is_low_balance_alert_sent = True
                # ------------------------------------------
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
                    # Сразу коммитим привязку
                    await session.commit()

            # 3.5. ПРОВЕРКА ВОРОНКИ AMO CRM (РАЗРЕШЕНО ЛИ БОТУ ТУТ РАБОТАТЬ?)
            if dialogue.amo_lead_id and dialogue.status != "completed":
                lead_data = await amo_api.get_lead(dialogue.amo_lead_id)
                
                if lead_data:
                    pipeline_id = lead_data.get("pipeline_id")
                    allowed_pipelines = settings.ALLOWED_PIPELINES
                    
                    # Если список разрешенных задан, и текущей воронки в нем нет
                    if allowed_pipelines and (pipeline_id not in allowed_pipelines):
                        logger.info(f"🛑 Сделка {dialogue.amo_lead_id} находится в неразрешенной воронке {pipeline_id}. Бот отключается.")
                        dialogue.status = "completed"
                        await session.commit()
                        
                        # Очищаем буфер сообщений, чтобы не копился мусор
                        await redis_manager.delete_buffer(conversation_id)
                        return  # Прерываем обработку задачи

            # Блок 4 (Перенос из Redis в Postgres)
            if dialogue and dialogue.status == "completed":
                logger.info("⏭️ Диалог завершен или отключен. Бот больше не отвечает.")
                # Очищаем буфер на случай, если юзер продолжает спамить после завершения
                await redis_manager.delete_buffer(conversation_id)
                return 

            new_messages = await redis_manager.get_buffer(conversation_id)
            if new_messages:
                extracted = dict(dialogue.extracted_data or {})
                if "received_files" not in extracted: extracted["received_files"] = []
                
                formatted = []
                for m in new_messages:
                    # Извлекаем ID из Пакта или генерируем временный, если его нет
                    msg_id = m.get("message_id") or f"user_{int(time.time() * 1000)}"
                    msk_now = get_msk_time()

                    # --- ОБРАБОТКА ТЕКСТА ---
                    if m.get("text"):
                        formatted.append({
                            "role": "user",
                            "content": m["text"],
                            "message_id": msg_id,
                            "timestamp_msk": msk_now
                        })
                    
                    # --- ОБРАБОТКА ФАЙЛОВ ---
                    for att in m.get("attachments", []):
                        file_name = att.get("file_name", "unknown.file").lower()
                        # Проверяем, что это PDF
                        if file_name.endswith(".pdf"):
                            # Добавляем в список полученных (если еще не было такого файла)
                            if file_name not in extracted["received_files"]:
                                extracted["received_files"].append(file_name)
                                current_count = len(extracted["received_files"])
                                
                                # 1. Системное сообщение для ИИ (формат как просил: с временем и ID)
                                formatted.append({
                                    "role": "user", 
                                    "content": f"[SYSTEM COMMAND] Пользователь прислал pdf файл {file_name}. Всего файлов: {current_count}/3",
                                    "message_id": f"sys_{int(time.time() * 1000)}",
                                    "timestamp_msk": get_msk_time()
                                })
                                
                                # 2. ОТПРАВКА УВЕДОМЛЕНИЯ В ТЕЛЕГРАМ (Логика сохранена)
                                client_name = extracted.get("name", "Неизвестный клиент")
                                credit_type = extracted.get("credit_type", "Не определен")
                                
                                amo_link = None
                                if dialogue.amo_lead_id:
                                    amo_link = f"https://{settings.AMO_SUBDOMAIN}.amocrm.ru/leads/detail/{dialogue.amo_lead_id}"
                                
                                asyncio.create_task(tg_service.send_report_card(
                                    title=f"📌 Новый файл от клиента: {client_name}",
                                    fields={
                                        "Имя файла": file_name,
                                        "Тип запроса": credit_type,
                                        "Получено файлов": f"{current_count} из 3"
                                    },
                                    link=amo_link
                                ))
                
                # Синхронизируем и сохраняем историю
                updated_history = list(dialogue.history)
                updated_history.extend(formatted)
                dialogue.history = updated_history
                dialogue.extracted_data = extracted 
                # ОБЯЗАТЕЛЬНО: Обновляем время и сбрасываем уровень напоминаний
                dialogue.reminder_level = 0
                dialogue.last_message_at = func.now()
                await session.commit()
                logger.info(f"💾 {len(new_messages)} сообщений (с ID и MSK временем) перенесено в Postgres.")
                
                # Удаляем из Redis только после успешного сохранения
                await redis_manager.delete_buffer(conversation_id)
            
            else:
                logger.debug("Буфер Redis пуст, продолжаем работу с историей из БД.")
            # 5. Генерация ответа
            # Проверяем историю в БД. Если последнее сообщение от пользователя — отвечаем.
            # Это покрывает и обычный ход, и восстановление после сбоя.
            if dialogue.history and dialogue.history[-1]["role"] == "user":
                logger.info(f"⏳ Генерируем ответ для диалога {conversation_id}...")
                await perform_logic_and_reply(dialogue, session)
                
                # Финальный коммит после отправки ответа
                await session.commit()
            else:
                logger.info("⏭️ Ответ не требуется (последнее сообщение не от пользователя).")

            logger.info(f"🏁 Диалог {conversation_id} успешно обработан.")

            

        except Exception as e:
            await session.rollback()
            logger.error(f"🚨 Ошибка воркера [ID: {conversation_id}]: {str(e)}", exc_info=True)
            # Пробрасываем ошибку для TaskIQ Retry
            # --- ТВОЙ АЛЕРТ ПРИ КРАШЕ ВОРКЕРА ---
            # Отправляем уведомление админу, что задача полностью упала
            alert_msg = (
                f"❌ <b>Критическая ошибка воркера!</b>\n"
                f"Диалог: <code>{conversation_id}</code>\n"
                f"Ошибка: <code>{str(e)}</code>"
            )
            asyncio.create_task(tg_service.send_tech_alert(alert_msg))
            raise e

async def perform_logic_and_reply(dialogue: Dialogue, session):
    """
    Интеграция LangGraph: загрузка стейта -> выполнение графа -> сохранение итогов
    """
    logger.info(f"🤖 Запуск ИИ-логики для диалога {dialogue.pact_conversation_id}")

    # 1. Готовим историю сообщений для OpenAI (чистим от лишних метаданных)
    llm_history = [
        {"role": msg["role"], "content": msg["content"]} 
        for msg in dialogue.history
    ]

    # 2. Формируем входное состояние для графа
    # Если шаг еще не задан (новое сообщение), начинаем с CONSENT
    old_state = dialogue.current_state # <--- ЗАПОМИНАЕМ СТАРЫЙ СТЕЙТ
    current_step = dialogue.current_state if dialogue.current_state != "START" else Steps.CONSENT
    
    initial_state: DialogueState = {
        "pact_conversation_id": dialogue.pact_conversation_id,
        "amo_lead_id": dialogue.amo_lead_id,
        "current_step": current_step,
        "messages": llm_history,
        "extracted_data": dialogue.extracted_data or {},
        "files_count": len(dialogue.extracted_data.get("received_files", [])),
        "analysis_result": None,
        "ai_response": None,
        "is_completed": False,
        "stop_factors_found": dialogue.extracted_data.get("stop_factors_found", False)
    }

    try:
        # 3. ЗАПУСК ГРАФА
        final_state = await app_graph.ainvoke(initial_state)
        
        ai_reply_text = final_state.get("ai_response")
        
        if not ai_reply_text:
            error_msg = f"⚠️ Граф не сгенерировал ответ для {dialogue.pact_conversation_id} (ai_response is None)"
            logger.error(error_msg)
            
            # --- АЛЕРТ: ИИ ПРОМОЛЧАЛ ---
            asyncio.create_task(tg_service.send_tech_alert(f"🤖 <b>ИИ не выдал ответ!</b>\nДиалог: {dialogue.pact_conversation_id}"))
            return

        # 4. Отправка ответа пользователю через Pact API
        success = await pact_api.send_message(
            conversation_id=dialogue.pact_conversation_id,
            message=ai_reply_text
        )

        if success:
            # Делаем срез данных, чтобы они не изменились по ссылке
            current_extracted = dict(final_state["extracted_data"])
            new_state = final_state["current_step"]

            # 5. Обновляем модель Dialogue новыми богатыми данными
            new_entry = {
                "role": "assistant",
                "state": old_state,                 # В каком стейте пришло сообщение
                "new_state": new_state,             # В какой стейт перешли
                "content": ai_reply_text,
                "message_id": f"bot_{time.time()}", # Генерируем ID для бота
                "timestamp_msk": get_msk_time(),    # Время МСК
                "extracted_data": current_extracted # Срез извлеченных данных
            }
            
            # Обновляем историю
            updated_history = list(dialogue.history)
            updated_history.append(new_entry)
            dialogue.history = updated_history
            
            # Обновляем стейт и извлеченные данные
            dialogue.current_state = new_state
            dialogue.extracted_data = current_extracted
            
            # 6. ЛОГИКА CRM (Перевод сделки в другую воронку, если диалог завершен)
            if final_state.get("is_completed"):
                dialogue.status = "completed"
                await handle_crm_completion(dialogue, final_state)

            logger.info(f"📤 Ответ отправлен. Шаг изменен на: {dialogue.current_state}")
        else:
            error_text = f"❌ <b>Ошибка отправки в Pact!</b>\nДиалог: {dialogue.pact_conversation_id}\nТекст: {ai_reply_text[:100]}..."
            asyncio.create_task(tg_service.send_tech_alert(error_text))
            
            logger.error(f"❌ Не удалось отправить ответ в Пакт")
            raise Exception("Pact send failed")

    except Exception as e:
        # --- АЛЕРТ: ОШИБКА ВНУТРИ ГРАФА ИЛИ OPENAI ---
        alert_msg = (
            f"🚨 <b>Ошибка в логике ИИ!</b>\n"
            f"Диалог: <code>{dialogue.pact_conversation_id}</code>\n"
            f"Детали: <code>{str(e)}</code>"
        )
        asyncio.create_task(tg_service.send_tech_alert(alert_msg))
        
        logger.error(f"🚨 Ошибка при выполнении графа: {e}", exc_info=True)
        raise e

async def handle_crm_completion(dialogue: Dialogue, final_state: Dict):
    """
    Финальная обработка: перевод в воронки и создание анкеты в amoCRM
    """
    lead_id = dialogue.amo_lead_id
    if not lead_id:
        logger.warning(f"⚠️ Нет amo_lead_id для диалога {dialogue.pact_conversation_id}. Пропускаем CRM-логику.")
        return

    data = final_state.get('extracted_data', {})
    current_step = final_state.get('current_step')
    
    # 1. ОПРЕДЕЛЯЕМ ЦЕЛЕВУЮ ВОРОНКУ
    target_pipeline = None
    
    if current_step == Steps.COURSE_INFO:
        target_pipeline = SETTINGS_DATA['amocrm_pipelines']['course_id']
        logger.info(f"🎯 Направление: КУРС. Перевод сделки {lead_id}...")

    elif current_step == Steps.CONSULT_INFO:
        # Проверяем, согласился ли на оплату (из анализатора)
        analysis = final_state.get('analysis_result', {})
        if analysis.get('agree_to_pay'):
            target_pipeline = SETTINGS_DATA['amocrm_pipelines']['consultation_id']
            logger.info(f"🎯 Направление: КОНСУЛЬТАЦИЯ (оплачено). Перевод сделки {lead_id}...")

    elif current_step == Steps.FINAL_HANDOVER:
        target_pipeline = SETTINGS_DATA['amocrm_pipelines']['main_id']
        logger.info(f"🎯 Направление: ОСНОВНОЙ КРЕДИТ. Перевод сделки {lead_id}...")

    # 2. ПЕРЕВОДИМ СДЕЛКУ
    if target_pipeline:
        await amo_api.update_lead(lead_id=lead_id, pipeline_id=target_pipeline)

    # 3. ФОРМИРУЕМ ИТОГОВУЮ АНКЕТУ (для основного сценария и консультаций)
    if current_step in [Steps.FINAL_HANDOVER, Steps.CONSULT_INFO]:
        # Собираем данные по списку из твоего сценария
        # Используем .get(key, '—') чтобы не было пустых мест
        summary = (
            "📋 АНКЕТА ИЗ БОТА:\n"
            f"👤 Имя: {data.get('name', '—')}\n"
            f"📍 Город: {data.get('city', '—')}\n"
            f"📞 Телефон: {data.get('phone', '—')}\n"
            f"💰 Требуемая сумма: {data.get('required_amount') or data.get('car_cost') or '—'}\n"
            f"🏦 Вид кредитования: {data.get('credit_type', '—')}\n"
            f"🏠 Вид залога: {data.get('sub_type', 'Нет залога')}\n"
            f"📑 Количество собственников: {'Один' if data.get('is_sole_owner') else 'Несколько'}\n"
            f"⚠️ Стоп-факторы: {', '.join(data.get('found_factors', [])) if data.get('found_factors') else 'Не обнаружены'}"
        )
        
        # Отправляем как примечание (тип service_message, чтобы выделялось)
        await amo_api.add_note(lead_id=lead_id, text=summary, note_type="service_message")