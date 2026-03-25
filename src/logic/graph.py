# src/logic/graph.py
from langgraph.graph import StateGraph, END
from src.logic.states import AgentState, AIIntent
from src.services.openai.client import get_model_intent
from src.logic.prompt_manager import prompt_manager
from src.core.logging import logger

# 1. Узел вызова модели
async def call_model_node(state: AgentState):
    """Формирует промпт и получает структурированный ответ от ИИ"""
    sys_prompt = prompt_manager.get_system_prompt(
        state=state.current_state,
        extracted_data=state.extracted_data,
        stop_factors=state.stop_factors
    )
    
    intent_data = await get_model_intent(sys_prompt, state.messages)
    
    # Возвращаем обновленное состояние с намерением
    return {"next_intent": intent_data}

# 2. Узел логики (переключатель состояний)
async def execute_logic_node(state: AgentState):
    """
    Принимает решение о смене стейта и обновлении анкеты.
    Здесь ИИ НЕ управляет логикой напрямую, только кодом.
    """
    intent = state.next_intent
    new_data = state.extracted_data.copy()
    new_state = state.current_state
    
    # Если уверенность модели выше 80%, обновляем данные анкеты
    if intent.confidence > 0.8:
        new_data.update(intent.extracted_fields)
        
        # Логика переключения стейта (FSM)
        # Пример: если мы в START и клиент хочет кредит
        if state.current_state == "START" and intent.intent == "select_credit":
            new_state = "SERVICE_CREDIT"
            
        # Пример: проверка на стоп-факторы в MINI_SURVEY
        if state.current_state == "MINI_SURVEY":
            # Проверяем заполненность критичных полей (стаж, просрочки и т.д.)
            if "employment_months" in new_data and new_data["employment_months"] < 6:
                if "stat_factor_added" not in state.stop_factors:
                    state.stop_factors.append("стаж менее 6 месяцев")
            
            # Если все вопросы пройдены
            if len(state.stop_factors) > 0:
                new_state = "STOP_FACTOR_OFFER"
            elif all_fields_collected(new_data): # Функция проверки из ТЗ
                new_state = "REPORTS_INSTRUCTION"

    return {
        "current_state": new_state,
        "extracted_data": new_data,
        "stop_factors": state.stop_factors
    }

# Вспомогательная функция для проверки анкеты
def all_fields_collected(data):
    required = ["employment_months", "has_long_overdues", "has_microloans"]
    return all(field in data for field in required)

# Собираем граф
workflow = StateGraph(AgentState)

workflow.add_node("llm", call_model_node)
workflow.add_node("logic", execute_logic_node)

workflow.set_entry_point("llm")
workflow.add_edge("llm", "logic")
workflow.add_edge("logic", END)

# Компилируем
app_graph = workflow.compile()