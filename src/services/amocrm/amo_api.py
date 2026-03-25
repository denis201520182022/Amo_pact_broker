import httpx
from typing import Optional, Dict, Any, List
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from src.core.config import settings
from src.core.logging import logger

class AmoCRMAPI:
    def __init__(self):
        self.subdomain = settings.AMO_SUBDOMAIN
        self.token = settings.AMO_LONG_TERM_TOKEN
        self.base_url = f"https://{self.subdomain}.amocrm.ru/api/v4"
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "AI-Broker-Bot/1.0"
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True
    )
    async def update_lead(
        self, 
        lead_id: str, 
        status_id: Optional[int] = None, 
        pipeline_id: Optional[int] = None,
        custom_fields: Optional[Dict[int, Any]] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """
        Универсальный метод обновления сделки.
        status_id: ID этапа
        custom_fields: словарь {ID_поля: значение}
        """
        url = f"{self.base_url}/leads/{lead_id}"
        
        payload: Dict[str, Any] = {"updated_by": 0} # 0 означает "изменено роботом"
        
        if status_id:
            payload["status_id"] = status_id
        if pipeline_id:
            payload["pipeline_id"] = pipeline_id
            
        # Формируем custom_fields_values
        if custom_fields:
            cf_values = []
            for field_id, value in custom_fields.items():
                cf_values.append({
                    "field_id": field_id,
                    "values": [{"value": value}]
                })
            payload["custom_fields_values"] = cf_values

        # Добавление тегов
        if tags:
            payload["_embedded"] = {
                "tags": [{"name": tag} for tag in tags]
            }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.patch(url, headers=self.headers, json=payload)
                
                if response.status_code == 200:
                    logger.info(f"✅ Сделка {lead_id} успешно обновлена в amoCRM")
                    return True
                
                logger.error(f"❌ Ошибка обновления сделки {lead_id} в амо: {response.text}")
                response.raise_for_status()
        except Exception as e:
            logger.error(f"🚨 Сбой при PATCH /leads/{lead_id}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True
    )
    async def add_note(
        self, 
        lead_id: str, 
        text: str, 
        note_type: str = "service_message"
    ) -> bool:
        """
        Добавление примечания в карточку сделки.
        note_type: common (текст), service_message (системное)
        """
        url = f"{self.base_url}/leads/{lead_id}/notes"
        
        # Структура зависит от типа примечания согласно доке
        params = {"text": text}
        if note_type in ["service_message", "extended_service_message"]:
            params["service"] = settings.PROJECT_NAME

        payload = [{
            "note_type": note_type,
            "params": params
        }]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, headers=self.headers, json=payload)
                
                if response.status_code in [200, 201]:
                    logger.info(f"📝 Примечание добавлено в сделку {lead_id}")
                    return True
                
                logger.error(f"❌ Ошибка добавления примечания в амо: {response.text}")
                response.raise_for_status()
        except Exception as e:
            logger.error(f"🚨 Сбой при POST /notes для сделки {lead_id}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True
    )
    async def get_lead(self, lead_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение данных конкретной сделки по ID.
        """
        url = f"{self.base_url}/leads/{lead_id}"
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 204:
                    logger.warning(f"⚠️ Сделка {lead_id} не найдена в amoCRM (вернулся код 204).")
                    return None
                
                logger.error(f"❌ Ошибка получения сделки {lead_id} в амо: {response.text}")
                response.raise_for_status()
        except Exception as e:
            logger.error(f"🚨 Сбой при GET /leads/{lead_id}: {e}")
            raise
    
# Создаем синглтон
amo_api = AmoCRMAPI()