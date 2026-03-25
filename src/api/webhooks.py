import json
from fastapi import APIRouter, Request, Response
from src.core.redis_client import redis_manager
from src.core.logging import logger
from src.worker import process_pact_messages_task
from src.core.config import settings
import asyncio
router = APIRouter(prefix="/webhooks", tags=["webhooks"])



async def delayed_trigger(conversation_id: str):
    """
    Фоновый таймер: спит 10 секунд и пинает воркер
    """
    await asyncio.sleep(settings.DEBOUNCE_SECONDS)
    # Запускаем задачу воркера ОБЫЧНЫМ способом (мгновенно)
    await process_pact_messages_task.kiq(conversation_id)
    logger.info(f"🚀 [Debounce] 10 секунд прошло. Задача для {conversation_id} отправлена воркеру.")


@router.post("/pact")
async def pact_webhook(request: Request):
    """
    Принимает вебхук от Pact.im.
    Группирует сообщения по conversation_id и ставит отложенную задачу в TaskIQ.
    """
    try:
        # Читаем тело запроса
        body = await request.body()
        if not body:
            return Response(status_code=200)
            
        data = json.loads(body)
        
        # 1. Фильтрация (согласно твоему логу)
        # Нам нужны только события 'create' и тип 'message'
        event = data.get("event")
        obj_type = data.get("type")
        obj = data.get("object", {})

        # Игнорируем обновления (update) или события диалогов (conversation)
        if event != "create" or obj_type != "message":
            return Response(status_code=200)

        # Проверяем, что сообщение ВХОДЯЩЕЕ
        if not obj.get("income"):
            logger.debug(f"Игнорируем исходящее сообщение id={obj.get('id')}")
            return Response(status_code=200)

        # 2. Извлечение основных данных
        conversation_id = str(obj.get("conversation_id"))
        contact_data = obj.get("contact", {})
        attachments = obj.get("attachments", [])
        # Данные для воркера
        payload = {
            "conversation_id": conversation_id,
            "external_id": str(contact_data.get("external_id")), # ID пользователя в мессенджере
            "text": obj.get("message", "").strip(),
            "attachments": attachments,
            "company_id": obj.get("company_id"),
            "provider": obj.get("conversation", {}).get("provider"),
            "user_name": contact_data.get("name", "Unknown")
        }

        if not conversation_id or not payload["text"]:
            return Response(status_code=200)

         # Оставляем только текст, так как имена в Пакте и Амо часто не совпадают
        text_norm = " ".join(payload['text'].split()).lower()
        match_key = f"match:text:{text_norm}"[:200]
        
        # 1. Сохраняем свой ID (для амо, если он придет позже)
        await redis_manager.redis.setex(f"pact_id:{match_key}", 60, conversation_id)
        
        # 2. Проверяем, нет ли уже в Redis ID от амо
        amo_lead_id = await redis_manager.redis.get(f"amo_id:{match_key}")
        
        if amo_lead_id:
            # Если нашли — сохраняем связь "ID Пакта -> ID Амо" в Redis на час
            # Чтобы воркер мог это подхватить
            await redis_manager.redis.setex(f"map:{conversation_id}", 3600, amo_lead_id)
            logger.info(f"🔗 [Match] Связь найдена мгновенно: Pact {conversation_id} <-> Amo {amo_lead_id}")
        # ---------------------------------------

        # 3. Дебоунс логика через Redis
        # add_message_to_buffer должен возвращать True, если это ПЕРВОЕ сообщение в пачке за 10 сек
        is_first = await redis_manager.add_message_to_buffer(conversation_id, payload)

        if is_first:
            # ЗАПУСКАЕМ ТАЙМЕР "В СТОРОНЕ" (не дожидаясь его завершения)
            asyncio.create_task(delayed_trigger(conversation_id))
            logger.info(f"📥 [Pact] Первое сообщение. Таймер на {settings.DEBOUNCE_SECONDS}с запущен.")
        else:
            logger.info(f"📥 [Pact] Сообщение добавлено в буфер диалога {conversation_id}")

        # ПАКТ ПОЛУЧАЕТ ОТВЕТ МГНОВЕННО
        return {"status": "accepted"}

    except json.JSONDecodeError:
        logger.error("Ошибка декодирования JSON в вебхуке Pact")
        return Response(status_code=400)
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука Pact: {e}", exc_info=True)
        # Возвращаем 200, чтобы Пакт не долбил повторами при ошибках нашей логики
        return Response(status_code=200)

@router.post("/amo")
async def amo_webhook(request: Request):
    """
    Принимает вебхук от amoCRM для сопоставления сделки с чатом Пакта.
    """
    try:
        logger.info(f"📥 [Amo] Получен вебхук: {request.method} {request.url}")
        # Амо присылает данные в формате x-www-form-urlencoded
        form_data = await request.form()
        logger.info(f"📥 [Amo] Получены данные: {form_data}")
        # Извлекаем данные из структуры: message[add][0][...]
        text = form_data.get("message[add][0][text]")
        lead_id = form_data.get("message[add][0][entity_id]")
        author_name = form_data.get("message[add][0][author][name]")
        msg_type = form_data.get("message[add][0][type]")

        # Нам нужны только входящие сообщения и наличие всех данных
        if msg_type != "incoming" or not all([text, lead_id, author_name]):
            return Response(status_code=200)

        # Создаем ключ для сопоставления (Имя + Текст)
        # Ограничим длину текста, чтобы ключ не был гигантским
        text_norm = " ".join(text.split()).lower()
        match_key = f"match:text:{text_norm}"[:200]
        
        # Сохраняем ID сделки в Redis на 60 секунд
        await redis_manager.redis.setex(f"amo_id:{match_key}", 60, str(lead_id))
        
        logger.debug(f"🔍 [Amo] Данные для сопоставления получены: {author_name} -> {lead_id}")
        
        # Зеркальная проверка Пакта ---
        pact_conv_id = await redis_manager.redis.get(f"pact_id:{match_key}")
        
        if pact_conv_id:
            # Если нашли — сразу создаем маппинг для воркера
            await redis_manager.redis.setex(f"map:{pact_conv_id}", 3600, str(lead_id))
            logger.info(f"🔗 [Match] Связь найдена (со стороны Amo): Pact {pact_conv_id} <-> Amo {lead_id}")
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Ошибка в вебхуке Amo: {e}")
        return Response(status_code=200)