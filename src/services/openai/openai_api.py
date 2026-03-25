from openai import AsyncOpenAI
from src.core.config import settings
from src.logic.states import AIIntent
from src.core.logging import logger

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def get_model_intent(system_prompt: str, history: list) -> AIIntent:
    """Запрос к OpenAI с получением структурированного намерения"""
    try:
        completion = await client.beta.chat.completions.parse(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                *history
            ],
            response_format=AIIntent, # Наша схема из предыдущего шага
        )
        return completion.choices[0].message.parsed
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        raise e