import os
import yaml
import json
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END

from src.logic.states import DialogueState, Steps
from src.logic.prompt_manager import prompt_manager
from src.services.openai.openai_api import openai_service
from src.core.logging import logger
from src.utils.dialogue_logger import DialogueLogger  # Наш новый утилитарный логгер

# --- ЗАГРУЗКА НАСТРОЕК ДЛЯ ПОДСТАНОВКИ В ПРОМПТЫ ---
def load_settings_data() -> Dict[str, Any]:
    path = os.path.join("config", "settings.yaml")
    if not os.path.exists(path):
        return {"links": {}, "pricing": {}, "amocrm_pipelines": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

SETTINGS_DATA = load_settings_data()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def format_instruction(text: str) -> str:
    """Подставляет ссылки и цены из settings.yaml в текст промпта"""
    try:
        return text.format(
            pd_link=SETTINGS_DATA.get('links', {}).get('privacy_policy', ''),
            course_link=SETTINGS_DATA.get('links', {}).get('author_course', ''),
            tg_link=SETTINGS_DATA.get('links', {}).get('telegram_work_chat', ''),
            bki_links=f"{SETTINGS_DATA.get('links', {}).get('bki_scoring', '')}, "
                      f"{SETTINGS_DATA.get('links', {}).get('bki_credistory', '')}, "
                      f"{SETTINGS_DATA.get('links', {}).get('bki_nbki', '')}",
            paid_consult_price=SETTINGS_DATA.get('pricing', {}).get('paid_consultation', 10000),
            files_count="{files_count}"  # Оставляем для динамической подстановки в узле генерации
        )
    except Exception as e:
        logger.error(f"Ошибка форматирования инструкции: {e}")
        return text

# --- УЗЛЫ ГРАФА (NODES) ---

async def analyze_node(state: DialogueState) -> Dict[str, Any]:
    """Узел 1: Анализ входящего сообщения через AI-1 (Structured Output)"""
    conv_id = state['pact_conversation_id']
    d_logger = DialogueLogger(conv_id)
    
    logger.info(f"🔍 [Node: Analyze] Step: {state['current_step']}")
    
    # 1. Получаем конфигурацию для анализатора
    schema, instruction = prompt_manager.get_analyzer_config(state['current_step'])
    analyzer_sys, _ = prompt_manager.get_system_prompts()
    
    # ЛОГИРУЕМ ЗАПРОС К АНАЛИЗАТОРУ
    d_logger.log_section("AI-1 ANALYZER REQUEST", {
        "current_step": state['current_step'],
        "system_prompt": analyzer_sys,
        "step_instruction": instruction,
        "expected_pydantic_schema": schema.schema(),
        "history_context_sent": state['messages'][-3:] # Последние 3 для понимания контекста
    })

    # 2. Вызов OpenAI
    analysis = await openai_service.analyze_message(
        messages=state['messages'][-3:], 
        response_model=schema,
        system_prompt=analyzer_sys,
        instruction=instruction
    )
    
    analysis_dict = analysis.model_dump() if analysis else {"step_completed": False, "error": "OpenAI returned None"}
    
    # ЛОГИРУЕМ ОТВЕТ АНАЛИЗАТОРА
    d_logger.log_section("AI-1 ANALYZER RESPONSE", analysis_dict)
    
    return {"analysis_result": analysis_dict}


async def logic_node(state: DialogueState) -> Dict[str, Any]:
    """Узел 2: Бизнес-логика, переключение стейтов и управление extracted_data"""
    conv_id = state['pact_conversation_id']
    d_logger = DialogueLogger(conv_id)
    
    logger.info(f"⚙️ [Node: Logic] Processing analysis for step: {state['current_step']}")
    
    # Сохраняем состояние "ДО" для логов
    old_step = state['current_step']
    old_data = json.loads(json.dumps(state['extracted_data'])) # Глубокая копия для лога

    result = state.get('analysis_result') or {}
    current = state['current_step']
    extracted = dict(state['extracted_data']) # Поверхностная копия для мутации
    
    next_step = current 
    is_completed = False
    stop_factors_found = state.get('stop_factors_found', False)
    
    # Считаем файлы
    received_files = extracted.get('received_files', [])
    files_count = len(received_files)

    # --- ПРОВЕРКА СЧЕТЧИКА ФАЙЛОВ (ПРИОРИТЕТНАЯ ЛОГИКА) ---
    if files_count >= 3:
        logger.info(f"📚 Собрано {files_count} файла. Переход к финалу.")
        next_step = Steps.FINAL_HANDOVER
        is_completed = True
    
    # --- ОБРАБОТКА ЛОГИКИ ПЕРЕХОДОВ ---
    elif result.get('step_completed'):
        
        # 1. Базовый сбор: Согласие -> Имя -> Телефон -> Город
        if current == Steps.CONSENT:
            extracted['consent_given'] = result.get('consent_given')
            next_step = Steps.NAME if extracted['consent_given'] else Steps.CONSENT

        elif current == Steps.NAME:
            extracted['name'] = result.get('name')
            next_step = Steps.PHONE

        elif current == Steps.PHONE:
            extracted['phone'] = result.get('phone')
            next_step = Steps.CITY

        elif current == Steps.CITY:
            extracted['city'] = result.get('city')
            next_step = Steps.MAIN_MENU

        # 2. Главное меню
        elif current == Steps.MAIN_MENU:
            intent = result.get('intent')
            extracted['direction'] = intent
            
            if intent == "course": next_step = Steps.COURSE_INFO
            elif intent == "consult": next_step = Steps.CONSULT_INFO
            elif intent == "pricing": next_step = Steps.PRICING_INFO
            elif intent == "credit": next_step = Steps.SF_SENIORITY

        # 3. Инфо-ветки
        elif current == Steps.COURSE_INFO:
            is_completed = True

        elif current == Steps.CONSULT_INFO:
            if result.get('agree_to_pay'):
                extracted['paid_consult_agreed'] = True
                next_step = Steps.DOCS_INSTRUCTION
            else:
                next_step = Steps.MAIN_MENU

        elif current == Steps.PRICING_INFO:
            next_step = Steps.MAIN_MENU

        # 4. Цепочка СТОП-ФАКТОРОВ
        elif current.startswith("STEP_SF_"):
            factor_key = current.replace("STEP_SF_", "").lower()
            if result.get('is_problematic'):
                stop_factors_found = True
                current_factors = extracted.get('found_factors', [])
                if factor_key not in current_factors:
                    extracted['found_factors'] = current_factors + [factor_key]
            
            sf_chain = [
                Steps.SF_SENIORITY, Steps.SF_DELAYS, Steps.SF_FSSP, 
                Steps.SF_MFO, Steps.SF_BANKRUPTCY
            ]
            try:
                curr_idx = sf_chain.index(current)
                if curr_idx < len(sf_chain) - 1:
                    next_step = sf_chain[curr_idx + 1]
                else:
                    next_step = Steps.QUALIFY_RESULT
            except ValueError:
                next_step = Steps.QUALIFY_RESULT

        # 5. Квалификация (Залог vs Кредит)
        elif current == Steps.QUALIFY_RESULT:
            if stop_factors_found:
                if result.get('step_completed'):
                    extracted['credit_type'] = "collateral"
                    next_step = Steps.COLLATERAL_DETAILS
                else:
                    is_completed = True
            else:
                next_step = Steps.SELECT_CREDIT_TYPE

        # 6. Выбор типа кредита
        elif current == Steps.SELECT_CREDIT_TYPE:
            c_type = result.get('credit_type')
            extracted['credit_type'] = c_type
            mapping = {
                "mortgage": Steps.MORTGAGE_DETAILS,
                "collateral": Steps.COLLATERAL_DETAILS,
                "car": Steps.CAR_DETAILS,
                "refinance": Steps.REFINANCE_DETAILS,
                "consumer": Steps.CONSUMER_DETAILS
            }
            next_step = mapping.get(c_type, Steps.DOCS_INSTRUCTION)

        # 7. Детали веток
        elif current in [
            Steps.MORTGAGE_DETAILS, Steps.COLLATERAL_DETAILS, 
            Steps.CAR_DETAILS, Steps.REFINANCE_DETAILS, Steps.CONSUMER_DETAILS
        ]:
            for key, val in result.items():
                if key not in ['step_completed', 'off_topic']:
                    extracted[key] = val
            next_step = Steps.DOCS_INSTRUCTION

        # 8. Документы
        elif current == Steps.DOCS_INSTRUCTION:
            next_step = Steps.DOCS_WAIT

    # Проверка завершения для финальных узлов
    if next_step in [Steps.COURSE_INFO, Steps.FINAL_HANDOVER]:
        is_completed = True

    # ЛОГИРУЕМ ПЕРЕХОД И ИЗМЕНЕНИЯ В ДАННЫХ
    d_logger.log_state_change(
        old_step=old_step,
        new_step=next_step,
        old_data=old_data,
        new_data=extracted
    )

    return {
        "extracted_data": extracted,
        "current_step": next_step,
        "is_completed": is_completed,
        "stop_factors_found": stop_factors_found,
        "files_count": files_count
    }


async def generate_node(state: DialogueState) -> Dict[str, Any]:
    """Узел 3: Генерация текстового ответа через AI-2"""
    conv_id = state['pact_conversation_id']
    d_logger = DialogueLogger(conv_id)
    
    logger.info(f"✍️ [Node: Generate] Writing reply for {state['current_step']}")
    
    # 1. Сбор промптов
    _, generator_sys = prompt_manager.get_system_prompts()
    instruction = prompt_manager.get_generator_instruction(state['current_step'])
    
    # 2. Форматирование промпта
    formatted_instruction = format_instruction(instruction).replace(
        "{files_count}", str(state.get('files_count', 0))
    )
    
    # ЛОГИРУЕМ ЗАПРОС К ГЕНЕРАТОРУ
    d_logger.log_section("AI-2 GENERATOR REQUEST", {
        "target_step": state['current_step'],
        "system_prompt": generator_sys,
        "formatted_extra_instruction": formatted_instruction,
        "history_sent": state['messages'][-5:]
    })
    
    # 3. Генерация
    ai_text = await openai_service.generate_response(
        messages=state['messages'][-5:], 
        system_prompt=generator_sys,
        extra_instruction=formatted_instruction
    )
    
    # ЛОГИРУЕМ ИТОГОВЫЙ ТЕКСТ
    d_logger.log_section("AI-2 GENERATOR RESPONSE (FINAL TEXT)", ai_text)
    
    return {"ai_response": ai_text}

# --- СБОРКА ГРАФА ---

def create_graph():
    workflow = StateGraph(DialogueState)

    workflow.add_node("analyze", analyze_node)
    workflow.add_node("logic", logic_node)
    workflow.add_node("generate", generate_node)

    workflow.set_entry_point("analyze")

    workflow.add_edge("analyze", "logic")
    workflow.add_edge("logic", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()

app_graph = create_graph()