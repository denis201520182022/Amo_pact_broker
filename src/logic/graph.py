import os
import yaml
import json
import copy
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from langgraph.graph import StateGraph, END

from src.logic.states import DialogueState, Steps
from src.logic.prompt_manager import prompt_manager
from src.services.openai.openai_api import openai_service
from src.core.logging import logger
from src.utils.dialogue_logger import DialogueLogger

# --- ЗАГРУЗКА НАСТРОЕК ДЛЯ ПОДСТАНОВКИ В ПРОМПТЫ ---
def load_settings_data() -> Dict[str, Any]:
    path = os.path.join("config", "settings.yaml")
    if not os.path.exists(path):
        return {"links": {}, "pricing": {}, "amocrm_pipelines": {}, "limits": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

SETTINGS_DATA = load_settings_data()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def format_instruction(text: str, state_data: Dict[str, Any] = None) -> str:
    """
    Подставляет ссылки, цены и динамические данные из состояния в текст промпта.
    """
    state_data = state_data or {}
    
    # Подготовка списка стоп-факторов для вставки в текст (если они есть)
    found_factors = state_data.get('found_factors', [])
    factors_str = ", ".join(found_factors) if found_factors else "не указаны"

    try:
        return text.format(
            # Ссылки из settings.yaml
            pd_link=SETTINGS_DATA.get('links', {}).get('privacy_policy', ''),
            course_link=SETTINGS_DATA.get('links', {}).get('author_course', ''),
            tg_link=SETTINGS_DATA.get('links', {}).get('telegram_work_chat', ''),
            # Визитка (берем из ссылок или отдельного поля, если есть)
            vizitka=SETTINGS_DATA.get('links', {}).get('vizitka_text', ''), 
            
            # Ссылки БКИ
            bki_links=f"{SETTINGS_DATA.get('links', {}).get('bki_scoring', '')}, "
                      f"{SETTINGS_DATA.get('links', {}).get('bki_credistory', '')}, "
                      f"{SETTINGS_DATA.get('links', {}).get('bki_nbki', '')}",
            
            # Цены и лимиты
            paid_consult_price=SETTINGS_DATA.get('pricing', {}).get('paid_consultation', 10000),
            max_loan=SETTINGS_DATA.get('limits', {}).get('max_consumer_loan_amount', 1500000),
            
            # Динамические данные из состояния
            found_factors=factors_str,
            files_count="{files_count}"  # Оставляем для финальной подстановки в узле генерации
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
    analyzer_sys, _ = await prompt_manager.get_system_prompts()
    
    # Безопасное получение схемы для логов
    try:
        if schema is BaseModel:
            schema_to_log = {"info": "BaseModel (empty)"}
        else:
            # model_json_schema() - это метод класса в Pydantic v2
            schema_to_log = schema.model_json_schema()
    except Exception as e:
        logger.warning(f"⚠️ Не удалось распарсить схему {schema} для логов: {e}")
        schema_to_log = {"info": "Could not parse schema", "error": str(e), "class": str(schema)}

    # ЛОГИРУЕМ ЗАПРОС К АНАЛИЗАТОРУ
    d_logger.log_section("AI-1 ANALYZER REQUEST", {
        "current_step": state['current_step'],
        "system_prompt": analyzer_sys,
        "step_instruction": instruction,
        "expected_schema": schema_to_log,
        "history_context_sent": state['messages'] 
    })

    # 2. Вызов OpenAI
    analysis = await openai_service.analyze_message(
        messages=state['messages'][-3:], 
        response_model=schema,
        system_prompt=analyzer_sys,
        instruction=instruction
    )
    
    analysis_dict = analysis.model_dump() if analysis else {
        "step_completed": False, 
        "off_topic": True,
        "error": "OpenAI returned None"
    }
    
    # ЛОГИРУЕМ ОТВЕТ АНАЛИЗАТОРА
    d_logger.log_section("AI-1 ANALYZER RESPONSE", analysis_dict)
    
    return {"analysis_result": analysis_dict}

async def logic_node(state: DialogueState) -> Dict[str, Any]:
    """
    Узел 2: Бизнес-логика. 
    Принимает результат анализа (AI-1), обновляет состояние, меняет шаги (current_step) 
    и управляет флагами завершения.
    """
    conv_id = state['pact_conversation_id']
    d_logger = DialogueLogger(conv_id)
    
    logger.info(f"⚙️ [Node: Logic] Processing logic for: {state['current_step']}")
    
    # Сохраняем состояние "ДО" для детального логгирования
    old_step = state['current_step']
    old_data = json.loads(json.dumps(state['extracted_data']))

    result = state.get('analysis_result') or {}
    current = state['current_step']
    extracted = copy.deepcopy(state['extracted_data']) # Глубокая копия для безопасности
    
    next_step = current 
    is_completed = False
    stop_factors_found = state.get('stop_factors_found', False)
    final_destination = state.get('final_destination') or extracted.get('final_destination')
    
    # Синхронизация: если есть направление, но нет метки воронки
    if not final_destination and extracted.get('direction'):
        mapping = {"consult": "consultation", "course": "course"}
        final_destination = mapping.get(extracted['direction'])
    
    # Считаем файлы в реальном времени
    received_files = extracted.get('received_files', [])
    files_count = len(received_files)
    logger.info(f"📂 [Logic] Files in state: {files_count} ({received_files})")

    # --- ПРИОРИТЕТ 1: ПРОВЕРКА СЧЕТЧИКА ФАЙЛОВ ---
    # Если файлов 3 или более, мгновенно переходим к финалу, игнорируя остальную логику
    if files_count >= 3:
        logger.info(f"📚 Собрано {files_count} файла. Переход к завершению.")
        next_step = Steps.FINAL_HANDOVER
        final_destination = "main" # Основная воронка (Кредиты)
        is_completed = True

    # --- ПРИОРИТЕТ 2: ОБРАБОТКА ВАЛИДНОГО ОТВЕТА (step_completed: true) ---
    elif result.get('step_completed'):
        
        # 1. Сбор базовых данных (Анкета)
        if current == Steps.CONSENT:
            if result.get('consent_given'):
                extracted['consent_given'] = True
                next_step = Steps.NAME
            else:
                # Если нет согласия — остаемся на этом же шаге, AI-2 снова попросит согласие
                next_step = Steps.CONSENT

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
            
            if intent == "course": 
                next_step = Steps.COURSE_INFO
                final_destination = "course"
            elif intent == "consult": 
                next_step = Steps.CONSULT_INFO
                final_destination = "consultation"
            elif intent == "pricing": 
                next_step = Steps.PRICING_INFO
            elif intent == "credit": 
                next_step = Steps.SF_SENIORITY

        # 3. Инфо-ветки и Консультации
        elif current == Steps.COURSE_INFO:
            is_completed = True

        elif current == Steps.CONSULT_INFO:
            if result.get('agree_to_pay'):
                extracted['paid_consult_agreed'] = True
                next_step = Steps.CONSULT_FINAL
                is_completed = True
            else:
                # Отказ от оплаты -> возврат в меню
                next_step = Steps.MAIN_MENU

        elif current == Steps.PRICING_INFO:
            # После цен возвращаемся в меню по сценарию
            next_step = Steps.MAIN_MENU

        # 4. Цепочка СТОП-ФАКТОРОВ (SF)
        elif current.startswith("STEP_SF_"):
            factor_key = current.replace("STEP_SF_", "").lower()
            
            if result.get('is_problematic'):
                stop_factors_found = True
                current_factors = extracted.get('found_factors', [])
                
                # Формируем красивое название фактора для анкеты
                label = factor_key
                if result.get('is_active'): # Если просрочка/МФО действующая
                    label = f"действующая {factor_key}"
                
                if label not in current_factors:
                    extracted['found_factors'] = current_factors + [label]
            
            # Навигация по цепочке вопросов
            sf_chain = [
                Steps.SF_SENIORITY, Steps.SF_DELAYS, Steps.SF_FSSP, 
                Steps.SF_MFO, Steps.SF_BANKRUPTCY
            ]
            try:
                curr_idx = sf_chain.index(current)
                if curr_idx < len(sf_chain) - 1:
                    next_step = sf_chain[curr_idx + 1]
                else:
                    # Конец цепочки СФ -> Проверяем, нужна ли квалификация на залог
                    next_step = Steps.QUALIFY_RESULT if stop_factors_found else Steps.SELECT_CREDIT_TYPE
            except ValueError:
                next_step = Steps.SELECT_CREDIT_TYPE

        # 5. Квалификация (Предложение залога при проблемах)
        elif current == Steps.QUALIFY_RESULT:
            if result.get('no_collateral'):
                # Пользователь отказался от залога -> Конец (Генератор выдаст Визитку)
                is_completed = True
            else:
                # Согласен на залог -> Принудительно ставим тип "collateral" и идем в детали
                extracted['credit_type'] = "collateral"
                next_step = Steps.COLLATERAL_DETAILS

        # 6. Выбор типа кредита (для "чистых" клиентов)
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

        # 7. Детализация веток (Специфическая логика)
        
        elif current == Steps.MORTGAGE_DETAILS:
            # Сценарий: Если рефинансирование ипотеки -> идем в БЛОК РЕФИНАНСИРОВАНИЕ
            if result.get('mortgage_type') == "refinance":
                next_step = Steps.REFINANCE_DETAILS
            else:
                next_step = Steps.DOCS_INSTRUCTION
            # Сохраняем все извлеченные данные (категория, рынок и т.д.)
            for k, v in result.items():
                if k not in ['step_completed', 'off_topic']: extracted[k] = v

        elif current == Steps.COLLATERAL_DETAILS:
            if result.get('no_collateral'):
                is_completed = True
            else:
                next_step = Steps.DOCS_INSTRUCTION
            for k, v in result.items():
                if k not in ['step_completed', 'off_topic']: extracted[k] = v

        elif current in [Steps.CAR_DETAILS, Steps.REFINANCE_DETAILS, Steps.CONSUMER_DETAILS]:
            # Универсальный переход к документам для остальных веток
            for k, v in result.items():
                if k not in ['step_completed', 'off_topic']: extracted[k] = v
            next_step = Steps.DOCS_INSTRUCTION

        # 8. Документы
        elif current == Steps.DOCS_INSTRUCTION:
            # Пользователь подтвердил готовность прислать документы
            next_step = Steps.DOCS_WAIT

    # --- ПРИОРИТЕТ 3: ОБРАБОТКА OFF-TOPIC ИЛИ НЕВАЛИДНОГО ОТВЕТА ---
    else:
        # Если Анализатор не понял ответ (step_completed: false), 
        # мы остаемся на текущем шаге. Генератор (AI-2) увидит это и повторит вопрос.
        logger.warning(f"⚠️ Данные для шага {current} не получены или не распознаны. Необходимо повторить вопрос переформулировав. Если собеседник задает вопрос, не игнорируй его")
        next_step = current

    # Финальная проверка завершения (для CRM)
    if next_step in [Steps.COURSE_INFO, Steps.FINAL_HANDOVER, Steps.CONSULT_FINAL]:
        is_completed = True

    # Логируем изменения
    d_logger.log_state_change(
        old_step=old_step,
        new_step=next_step,
        old_data=old_data,
        new_data=extracted
    )

    # Сохраняем final_destination в экстракт для персистентности между сообщениями
    extracted['final_destination'] = final_destination

    return {
        "extracted_data": extracted,
        "current_step": next_step,
        "is_completed": is_completed,
        "stop_factors_found": stop_factors_found,
        "files_count": files_count,
        "final_destination": final_destination
    }

async def generate_node(state: DialogueState) -> Dict[str, Any]:
    """
    Узел 3: Генерация текстового ответа через AI-2 (Татьяна).
    Использует текущий шаг (current_step), установленный в logic_node, 
    чтобы выбрать нужную инструкцию и сформировать ответ.
    """
    conv_id = state['pact_conversation_id']
    d_logger = DialogueLogger(conv_id)
    
    logger.info(f"✍️ [Node: Generate] Writing reply for step: {state['current_step']}")
    
    # 1. Получаем системный промпт и специфическую инструкцию шага
    _, generator_sys = await prompt_manager.get_system_prompts()
    base_instruction = prompt_manager.get_generator_instruction(state['current_step'])
    
    # 2. Формируем контекст извлеченных данных (Ground Truth)
    # Это "отрезвляет" ИИ, показывая, какие данные уже в системе
    data = state.get('extracted_data', {})
    known_info = ", ".join([f"{k}: {v}" for k, v in data.items() if v])
    known_info_context = f"\nТЕКУЩИЕ ДАННЫЕ КЛИЕНТА (УЖЕ СОБРАНО): {known_info if known_info else 'пока пусто'}"

    # 3. Анализируем, был ли успешным переход в logic_node
    analysis = state.get('analysis_result') or {}
    is_step_success = analysis.get('step_completed', False)
    is_off_topic = analysis.get('off_topic', False)

    # Динамическая подсказка по поведению
    if not is_step_success or is_off_topic:
        # Если клиент ушел от темы — требуем настойчивости (Правило №3)
        movement_hint = (
            "\nКРИТИЧЕСКАЯ ПОДСКАЗКА: Пользователь не дал четкого ответа или ушел от темы. "
            "Если собеседник задает вопрос, не игнорируй его, а ответь на него и вернись к вопросу по сценарию"
        )
    else:
        # Если шаг успешно пройден — даем команду на движение вперед
        movement_hint = (
            "\nКРИТИЧЕСКАЯ ПОДСКАЗКА: Пользователь ответил верно, данные сохранены. "
            "ПЕРЕХОДИ К СЛЕДУЮЩЕМУ ВОПРОСУ согласно твоей инструкции ниже. "
            "Не спрашивай то, что уже есть в блоке 'ТЕКУЩИЕ ДАННЫЕ КЛИЕНТА'."
        )

    # 4. Форматируем базовую инструкцию (ссылки, цены, стоп-факторы)
    formatted_instruction = format_instruction(base_instruction, data)
    
    # Подставляем счетчик файлов
    final_step_instruction = formatted_instruction.replace(
        "{files_count}", str(state.get('files_count', 0))
    )
    
    # 5. Собираем финальный "пинок" для OpenAI
    # Объединяем контекст данных, инструкцию шага и подсказку по поведению
    final_extra_instruction = (
        f"{known_info_context}"
        f"\nИНСТРУКЦИЯ НА ЭТОТ ХОД: {final_step_instruction}"
        f"{movement_hint}"
    )
    
    # ЛОГИРУЕМ ЗАПРОС К ГЕНЕРАТОРУ (AI-2)
    d_logger.log_section("AI-2 GENERATOR REQUEST", {
        "target_step": state['current_step'],
        "system_prompt": generator_sys,
        "final_directive": final_extra_instruction,
        "history_sent": state['messages']
    })
    
    # 6. Генерация ответа
    # Берем последние 5 сообщений истории для контекста беседы
    ai_text = await openai_service.generate_response(
        messages=state['messages'][-5:], 
        system_prompt=generator_sys,
        extra_instruction=final_extra_instruction
    )
    
    if not ai_text:
        ai_text = "Прошу прощения, возникла техническая заминка. Повторите, пожалуйста, ваш последний ответ."
        logger.error(f"❌ OpenAI AI-2 returned empty text for {conv_id}")

    # ЛОГИРУЕМ ИТОГОВЫЙ ТЕКСТ
    d_logger.log_section("AI-2 GENERATOR RESPONSE (FINAL TEXT)", ai_text)
    
    return {"ai_response": ai_text}

# --- СБОРКА ГРАФА ---

def create_graph():
    """
    Создает и компилирует StateGraph.
    Цикл работы: Анализ (AI-1) -> Бизнес-логика -> Генерация текста (AI-2).
    """
    workflow = StateGraph(DialogueState)

    # Добавляем узлы
    workflow.add_node("analyze", analyze_node)
    workflow.add_node("logic", logic_node)
    workflow.add_node("generate", generate_node)

    # Устанавливаем точку входа
    workflow.set_entry_point("analyze")

    # Устанавливаем связи (линейная цепочка)
    workflow.add_edge("analyze", "logic")
    workflow.add_edge("logic", "generate")
    workflow.add_edge("generate", END)

    # Компилируем граф
    return workflow.compile()

# Экспортируем готовый объект графа для использования в воркере
app_graph = create_graph()