# src/logic/states.py
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# Добавь в src/logic/states.py или создай новый файл logic_map.py
STATE_TRANSITIONS = {
    "START": {
        "select_credit": "SERVICE_CREDIT",
        "select_course": "SERVICE_COURSE",
        "select_consult": "SERVICE_PAID_CONSULT",
        "select_price": "SERVICE_PRICE"
    },
    "SERVICE_CREDIT": {
        "select_mortgage": "MORTGAGE",
        "select_secured": "SECURED",
        "select_other": "MINI_SURVEY"
    },
    "MINI_SURVEY": {
        "answered_all": "REPORTS_INSTRUCTION",
        "has_stop_factors": "STOP_FACTOR_OFFER"
    }
    # ... и так далее по всем пунктам ТЗ
}

class ExtractedData(BaseModel):
    """Данные, которые бот вытаскивает из диалога (анкета)"""
    client_name: Optional[str] = None
    client_city: Optional[str] = None
    credit_goal: Optional[str] = None
    employment_months: Optional[int] = None
    has_long_overdues: Optional[bool] = None
    has_microloans: Optional[bool] = None
    has_fssp: Optional[bool] = None
    had_bankruptcy: Optional[bool] = None
    # ... остальные поля из ТЗ (можно добавлять по мере роста)

class AIIntent(BaseModel):
    """Структурированный ответ от LLM"""
    monologue: str = Field(description="Внутренние рассуждения модели о том, что сказал клиент")
    intent: str = Field(description="Намерение клиента (например: select_service, answer_question, skip_step)")
    confidence: float = Field(description="Уверенность в намерении от 0.0 до 1.0")
    extracted_fields: Dict[str, Any] = Field(default_factory=dict, description="Поля анкеты, которые удалось найти в последней фразе")
    answer: str = Field(description="Текст ответа клиенту")

class AgentState(BaseModel):
    """Состояние графа LangGraph"""
    # История сообщений
    messages: List[Dict[str, str]] 
    # Текущее состояние из БД (LeadSession.current_state)
    current_state: str
    # Накопленные данные анкеты
    extracted_data: Dict[str, Any]
    # Список стоп-факторов
    stop_factors: List[str]
    # Намерение, определенное моделью на текущем шаге
    next_intent: Optional[AIIntent] = None