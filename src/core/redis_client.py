import json
import redis.asyncio as redis
from typing import Optional, List, Dict, Any
from taskiq_redis import RedisScheduleSource
from src.core.config import settings
from src.core.logging import logger

class RedisManager:
    def __init__(self):
        self.redis: Optional[redis.Redis] = None
        self.url = settings.REDIS_URL
        # Источник расписания для TaskIQ Scheduler
        self.scheduler = RedisScheduleSource(self.url)

    async def connect(self):
        """Инициализация подключения к Redis"""
        try:
            self.redis = redis.from_url(
                self.url, 
                encoding="utf-8", 
                decode_responses=True
            )
            await self.redis.ping()
            logger.info("✅ Connected to Redis")
        except Exception as e:
            logger.error(f"❌ Redis connection error: {e}")
            raise

    async def disconnect(self):
        """Закрытие соединения"""
        if self.redis:
            await self.redis.close()
            logger.info("Close Redis connection")

    async def add_message_to_buffer(self, conversation_id: str, payload: Dict[str, Any]) -> bool:
        """
        Добавляет сообщение в список (буфер) конкретного диалога.
        Возвращает True, если это первое сообщение в текущей пачке (нужно запустить таймер).
        """
        key = f"pact_buffer:{conversation_id}"
        try:
            # Превращаем dict в строку JSON
            message_json = json.dumps(payload, ensure_ascii=False)
            
            # LPUSH добавляет в начало списка. 
            # Результат выполнения lpush — это длина списка после вставки.
            list_len = await self.redis.lpush(key, message_json)
            
            # Устанавливаем TTL (1 час), чтобы мусор не копился, если воркер упадет
            await self.redis.expire(key, 3600)

            # Если длина 1, значит до этого списка не было или он был пуст
            return list_len == 1
        except Exception as e:
            logger.error(f"Ошибка при записи в Redis буфер: {e}")
            return False

    async def get_and_clear_buffer(self, conversation_id: str) -> List[Dict[str, Any]]:
        """
        Забирает все накопленные сообщения из буфера и удаляет ключ.
        Используется воркером.
        """
        key = f"pact_buffer:{conversation_id}"
        try:
            # Атомарно забираем все элементы и удаляем ключ
            # Мы использовали LPUSH (добавление в голову), 
            # поэтому забираем через LRANGE и потом разворачиваем, 
            # либо используем RPOP в цикле.
            
            # Способ через pipeline для надежности:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.lrange(key, 0, -1)
                pipe.delete(key)
                results = await pipe.execute()
            
            messages_raw = results[0] # Это список строк JSON
            
            # Десериализуем и разворачиваем (т.к. пушили через LPUSH, 
            # последние сообщения оказались в начале списка)
            messages = [json.loads(m) for m in messages_raw]
            messages.reverse() 
            
            return messages
        except Exception as e:
            logger.error(f"Ошибка при чтении буфера Redis: {e}")
            return []
    async def get_buffer(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Просто читает сообщения из буфера, не удаляя их"""
        key = f"pact_buffer:{conversation_id}"
        try:
            messages_raw = await self.redis.lrange(key, 0, -1)
            messages = [json.loads(m) for m in messages_raw]
            messages.reverse() # Чтобы были в хронологическом порядке
            return messages
        except Exception as e:
            logger.error(f"Ошибка при чтении буфера Redis: {e}")
            return []

    async def delete_buffer(self, conversation_id: str):
        """Удаляет буфер после успешного сохранения в БД"""
        key = f"pact_buffer:{conversation_id}"
        await self.redis.delete(key)

# Создаем глобальный экземпляр менеджера
redis_manager = RedisManager()

# Для docker-compose (taskiq scheduler) выносим объект scheduler отдельно
scheduler = redis_manager.scheduler