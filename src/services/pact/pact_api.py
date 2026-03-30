import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
from src.core.config import settings
from src.core.logging import logger
import logging
class PactAPI:
    def __init__(self):
        self.token = settings.PACT_API_TOKEN
        self.company_id = settings.PACT_COMPANY_ID
        self.base_url = "https://api.pact.im/api/p2"
        
        # Общие заголовки для всех запросов
        self.headers = {
            "X-Private-Api-Token": self.token,
            "Accept": "application/json"
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        # Не ретраим, если получили 403 (запрещено), так как это не ошибка сети
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        before_sleep=lambda retry_state: logger.info(f"♻️ Ретрай отправки в Пакт... Попытка {retry_state.attempt_number}"),
        reraise=True
    )
    async def send_message(self, conversation_id: str, message: str) -> bool:
        """
        Отправка текстового сообщения в существующий диалог.
        В TEST моде игнорирует 403 ошибку (бесплатный тариф).
        """
        url = f"{self.base_url}/conversations/{conversation_id}/messages"
        
        data = {
            "company_id": int(self.company_id),
            "text": message,
            "send_to_crm": "true"
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url, 
                    headers=self.headers, 
                    data=data
                )
                
                # 1. Успешная отправка
                if response.status_code in [200, 201]:
                    logger.info(f"✅ Сообщение успешно отправлено в Пакт (диалог: {conversation_id})")
                    return True
                
                # 2. Обработка 403 (Бесплатный тариф) в тестовом режиме
                if response.status_code == 403 and settings.TEST:
                    logger.warning(
                        f"⚠️ [TEST MODE] Pact API вернул 403 (Запрещено). "
                        f"Вероятно, бесплатный тариф. Симулируем успех."
                    )
                    logger.info(f"📝 Текст который должен был уйти: {message}")
                    return True # Возвращаем True, чтобы воркер продолжил работу
                
                # 3. Во всех остальных случаях — фиксируем ошибку
                logger.error(
                    f"❌ Ошибка Pact API ({response.status_code}): {response.text} | "
                    f"Conv: {conversation_id}"
                )
                response.raise_for_status()

        except httpx.HTTPStatusError as e:
            # Если мы попали сюда, значит это не 403 в тесте, а другая ошибка (например 500)
            raise e
        except Exception as e:
            if settings.TEST:
                from src.utils.dialogue_logger import DialogueLogger
                d_logger = DialogueLogger(conversation_id)
                d_logger.log_event("pact_error", str(e))
                
            logger.error(f"🚨 Ошибка при вызове Pact API: {str(e)}")
            raise

    async def upload_attachment(self, file_content: bytes, file_name: str):
        """
        Задел на будущее: загрузка файлов в Пакт.
        """
        # TODO: Реализовать загрузку файлов через POST /attachments
        pass

# Создаем синглтон для использования в воркере
pact_api = PactAPI()