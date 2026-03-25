from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from src.logic.states import DialogueState, Steps
from src.logic.prompt_manager import prompt_manager
from src.services.openai.openai_api import openai_service
from src.core.logging import logger
from src.core.config import settings
import yaml
import os

# Загружаем настройки для подстановки в промпты
def load_settings_data() -> Dict[str, Any]:
    path = os.path.join("config", "settings.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

SETTINGS_DATA = load_settings_data()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def format_instruction(text: str) -> str:
    """Подставляет ссылки и цены из settings.yaml в текст промпта"""
    return text.format(
        pd_link=SETTINGS_DATA['links']['privacy_policy'],
        course_link=SETTINGS_DATA['links']['author_course'],
        tg_link=SETTINGS_DATA['links']['telegram_work_chat'],
        bki_links=f"{SETTINGS_DATA['links']['bki_scoring']}, {SETTINGS_DATA['links']['bki_credistory']}, {SETTINGS_DATA['links']['bki_nbki']}",
        paid_consult_price=SETTINGS_DATA['pricing']['paid_consultation'],
        files_count="{files_count}" # Оставляем для динамической подстановки в узле
    )

# --- УЗЛЫ ГРАФА (NODES) ---

async def analyze_node(state: DialogueState) -> Dict[str, Any]:
    """Узел 1: Анализ входящего сообщения через AI-1"""
    logger.info(f"🔍 [Node: Analyze] Step: {state['current_step']}")
    
    # 1. Получаем схему и инструкцию для текущего шага
    schema, instruction = prompt_manager.get_analyzer_config(state['current_step'])
    analyzer_sys, _ = prompt_manager.get_system_prompts()
    
    # 2. Вызываем OpenAI для структурного анализа
    # Берем только последние сообщения (контекст)
    analysis = await openai_service.analyze_message(
        messages=state['messages'][-3:], 
        response_model=schema,
        system_prompt=analyzer_sys,
        instruction=instruction
    )
    
    # Превращаем Pydantic объект в словарь
    analysis_dict = analysis.model_dump() if analysis else {"step_completed": False}
    
    return {"analysis_result": analysis_dict}


async def logic_node(state: DialogueState) -> Dict[str, Any]:
    """
    Узел 2: Бизнес-логика и управление переходами.
    Обрабатывает результаты AI-1 и определяет следующий шаг (current_step).
    """
    logger.info(f"⚙️ [Node: Logic] Processing analysis for step: {state['current_step']}")
    
    result = state.get('analysis_result') or {}
    current = state['current_step']
    extracted = dict(state['extracted_data'])
    
    # Исходные значения из стейта
    next_step = current 
    is_completed = False
    stop_factors_found = state.get('stop_factors_found', False)
    
    # Считаем файлы (из списка имен файлов в БД)
    received_files = extracted.get('received_files', [])
    files_count = len(received_files)

    # --- ПРОВЕРКА СЧЕТЧИКА ФАЙЛОВ (ПРИОРИТЕТ) ---
    if files_count >= 3:
        logger.info(f"📚 Собрано {files_count} файла. Переход к финалу.")
        return {
            "current_step": Steps.FINAL_HANDOVER,
            "is_completed": True,
            "extracted_data": extracted,
            "files_count": files_count
        }

    # --- ОБРАБОТКА УСПЕШНОГО ШАГА ---
    if result.get('step_completed'):
        
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

        # 2. Главное меню (Выбор направления)
        elif current == Steps.MAIN_MENU:
            intent = result.get('intent')
            extracted['direction'] = intent
            
            if intent == "course": next_step = Steps.COURSE_INFO
            elif intent == "consult": next_step = Steps.CONSULT_INFO
            elif intent == "pricing": next_step = Steps.PRICING_INFO
            elif intent == "credit": next_step = Steps.SF_SENIORITY # Начало стоп-факторов

        # 3. Инфо-ветки (Курс, Консультация, Цена)
        elif current == Steps.COURSE_INFO:
            is_completed = True # Выдали ссылку и всё

        elif current == Steps.CONSULT_INFO:
            if result.get('agree_to_pay'):
                extracted['paid_consult_agreed'] = True
                next_step = Steps.DOCS_INSTRUCTION
            else:
                next_step = Steps.MAIN_MENU # Вернули в меню если не согласен

        elif current == Steps.PRICING_INFO:
            # После цен всегда возвращаем в меню для выбора
            next_step = Steps.MAIN_MENU

        # 4. Цепочка СТОП-ФАКТОРОВ
        elif current.startswith("STEP_SF_"):
            factor_key = current.replace("STEP_SF_", "").lower()
            if result.get('is_problematic'):
                stop_factors_found = True
                # ВМЕСТО .append() делаем пересоздание списка:
                current_factors = extracted.get('found_factors', [])
                if factor_key not in current_factors:
                    extracted['found_factors'] = current_factors + [factor_key]
            
            # Навигация по цепочке SF
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

        # 5. Результат квалификации (SF)
        elif current == Steps.QUALIFY_RESULT:
            # Если были SF и пользователь согласен на залог
            if stop_factors_found:
                if result.get('step_completed'): # Трактуем как "Да, согласен на залог"
                    extracted['credit_type'] = "collateral"
                    next_step = Steps.COLLATERAL_DETAILS
                else:
                    is_completed = True # Отказался — финал (визитка)
            else:
                # Если SF нет — переходим к обычному выбору типа кредита
                next_step = Steps.SELECT_CREDIT_TYPE

        # 6. Выбор типа кредита (без SF)
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

        # 7. Детали веток -> Инструкция по документам
        elif current in [
            Steps.MORTGAGE_DETAILS, Steps.COLLATERAL_DETAILS, 
            Steps.CAR_DETAILS, Steps.REFINANCE_DETAILS, Steps.CONSUMER_DETAILS
        ]:
            # Сохраняем все детали из анализатора в extracted_data
            for key, val in result.items():
                if key not in ['step_completed', 'off_topic']:
                    extracted[key] = val
            next_step = Steps.DOCS_INSTRUCTION

        # 8. Документы (Ожидание согласия на сбор)
        elif current == Steps.DOCS_INSTRUCTION:
            next_step = Steps.DOCS_WAIT

    # --- ОБРАБОТКА ЗАВЕРШЕНИЯ ---
    if next_step in [Steps.COURSE_INFO, Steps.FINAL_HANDOVER]:
        is_completed = True

    return {
        "extracted_data": extracted,
        "current_step": next_step,
        "is_completed": is_completed,
        "stop_factors_found": stop_factors_found,
        "files_count": files_count
    }


async def generate_node(state: DialogueState) -> Dict[str, Any]:
    """Узел 3: Генерация ответа через AI-2"""
    logger.info(f"✍️ [Node: Generate] Writing reply for {state['current_step']}")
    
    # 1. Получаем инструкции
    _, generator_sys = prompt_manager.get_system_prompts()
    instruction = prompt_manager.get_generator_instruction(state['current_step'])
    
    # 2. Форматируем инструкцию (вставляем ссылки и данные)
    formatted_instruction = format_instruction(instruction).replace(
        "{files_count}", str(state.get('files_count', 0))
    )
    
    # 3. Генерируем текст
    ai_text = await openai_service.generate_response(
        messages=state['messages'][-5:], # Даем чуть больше истории для стиля
        system_prompt=generator_sys,
        extra_instruction=formatted_instruction
    )
    
    return {"ai_response": ai_text}

# --- СБОРКА ГРАФА ---

def create_graph():
    workflow = StateGraph(DialogueState)

    # Добавляем узлы
    workflow.add_node("analyze", analyze_node)
    workflow.add_node("logic", logic_node)
    workflow.add_node("generate", generate_node)

    # Устанавливаем точку входа
    workflow.set_entry_point("analyze")

    # Связи: Анализ -> Логика -> Генерация -> Конец
    workflow.add_edge("analyze", "logic")
    workflow.add_edge("logic", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()

# Экземпляр графа
app_graph = create_graph()