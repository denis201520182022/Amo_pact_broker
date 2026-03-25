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
        stop=stop_after_attempt(3),  # 3 попытки
        wait=wait_exponential(multiplier=1, min=2, max=10), # Паузы 2с, 4с, 8с...
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, "WARNING"),
        reraise=True
    )
    async def send_message(self, conversation_id: str, message: str) -> bool:
        """
        Отправка текстового сообщения в существующий диалог.
        Использует формат multipart/form-data согласно документации.
        """
        url = f"{self.base_url}/conversations/{conversation_id}/messages"
        
        # Формируем тело запроса (form-data)
        data = {
            "company_id": int(self.company_id),
            "text": message,
            "send_to_crm": "true"  # Чтобы ответ бота отобразился в amoCRM
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url, 
                    headers=self.headers, 
                    data=data
                )
                
                # Проверяем статус ответа
                if response.status_code in [200, 201]:
                    logger.info(f"✅ Сообщение успешно отправлено в Пакт (диалог: {conversation_id})")
                    return True
                
                # Логируем специфические ошибки API
                logger.error(
                    f"❌ Ошибка Pact API ({response.status_code}): {response.text} | "
                    f"Conv: {conversation_id}"
                )
                response.raise_for_status()

        except httpx.TimeoutException:
            logger.error(f"⏱ Тайм-аут при отправке сообщения в Пакт (ID: {conversation_id})")
            raise
        except Exception as e:
            logger.error(f"🚨 Непредвиденная ошибка при вызове Pact API: {str(e)}")
            raise

    async def upload_attachment(self, file_content: bytes, file_name: str):
        """
        Задел на будущее: загрузка файлов в Пакт.
        """
        # TODO: Реализовать загрузку файлов через POST /attachments
        pass

# Создаем синглтон для использования в воркере
pact_api = PactAPI()