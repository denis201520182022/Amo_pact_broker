from typing import Annotated, Dict, Any, List, Optional, Union, Literal
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

# --- СОСТОЯНИЕ ГРАФА (LangGraph State) ---

class DialogueState(TypedDict):
    """
    Объект состояния, который проходит через все узлы графа.
    Хранится в истории Dialogue.history и Dialogue.extracted_data в БД.
    """
    # Идентификаторы
    pact_conversation_id: str
    amo_lead_id: Optional[str]
    
    # Управление шагами
    current_step: str  # Технический ID текущего шага (напр. 'STEP_NAME')
    next_step: Optional[str] # Куда планируем перейти после анализа
    
    # История сообщений для LLM (контекст текущей сессии)
    messages: List[Dict[str, str]] 
    
    # Глобальное хранилище извлеченных данных (extracted_data)
    extracted_data: Dict[str, Any]
    
    # Счетчик полученных отчетов БКИ (0-3)
    files_count: int
    
    # Результат работы Анализатора (AI-1) - временный, для текущего хода
    analysis_result: Optional[Dict[str, Any]]
    
    # Текст ответа, подготовленный Генератором (AI-2)
    ai_response: Optional[str]
    
    # Флаги завершения и логики
    is_completed: bool # Если True, граф заканчивает работу
    stop_factors_found: bool # Есть ли хотя бы один стоп-фактор
    final_destination: Optional[str] # Куда перевести сделку в CRM (Курс, Консультация и т.д.)

# --- СХЕМЫ СТРУКТУРНОГО ВЫВОДА ДЛЯ АНАЛИЗАТОРА (AI-1) ---

class BaseAnalysis(BaseModel):
    """Базовые поля для любого ответа анализатора"""
    step_completed: bool = Field(
        description="Удалось ли получить валидную информацию для текущего шага"
    )
    off_topic: bool = Field(
        description="Пользователь явно игнорирует вопрос или пытается сменить тему на постороннюю"
    )

class ConsentSchema(BaseAnalysis):
    """Этап 1: Согласие на ОПД"""
    consent_given: bool = Field(description="Пользователь подтвердил согласие (Да/Ок/Согласен)")

class UserBasicInfoSchema(BaseAnalysis):
    """Этап 2: Имя, Город, Телефон"""
    name: Optional[str] = Field(None, description="Имя пользователя")
    city: Optional[str] = Field(None, description="Город проживания")
    phone: Optional[str] = Field(None, description="Номер телефона в любом формате")

class MenuSelectionSchema(BaseAnalysis):
    """Этап 3: Выбор направления (1-4)"""
    selection: Optional[int] = Field(None, description="Цифра от 1 до 4")
    intent: Optional[Literal["credit", "course", "consult", "pricing"]] = Field(
        None, description="Словесное определение выбранного направления"
    )

class ConsultationConsentSchema(BaseAnalysis):
    """Блок: Платная консультация. Проверка готовности оплатить 10 000 руб."""
    agree_to_pay: bool = Field(description="Пользователь согласен на платную консультацию за 10 000 руб.")

class StopFactorCheckSchema(BaseAnalysis):
    """Этап 4: Проверка стоп-факторов (по одному)"""
    factor_value: Optional[Union[bool, str, int]] = Field(
        None, description="Значение фактора (стаж в мес, наличие просрочек да/нет и т.д.)"
    )
    is_problematic: bool = Field(
        description="Является ли ответ негативным (стоп-фактором) для получения обычного кредита"
    )

class CreditTypeSelectionSchema(BaseAnalysis):
    """Этап 5: Выбор типа кредита"""
    credit_type: Optional[Literal["mortgage", "collateral", "refinance", "car", "consumer"]] = Field(
        None, description="Выбранный тип кредитования"
    )

class CollateralDetailsSchema(BaseAnalysis):
    """Блок: Залоговый кредит"""
    sub_type: Optional[Literal["pledge", "repledge"]] = Field(None, description="Залог или Перезалог")
    is_sole_owner: Optional[bool] = Field(None, description="Является ли единственным собственником")
    has_minors: Optional[bool] = Field(None, description="Есть ли среди собственников несовершеннолетние")

class MortgageDetailsSchema(BaseAnalysis):
    """Блок: Ипотека"""
    category: Optional[Literal["apartment", "house", "commercial"]] = Field(None, description="Тип недвижимости")
    market: Optional[Literal["primary", "secondary"]] = Field(None, description="Рынок: первичка или вторичка")
    has_down_payment: Optional[bool] = Field(None, description="Наличие первоначального взноса")
    is_sole_borrower: Optional[bool] = Field(None, description="Планирует ли быть единственным заемщиком")
    # Для рефинансирования ипотеки:
    current_rate: Optional[float] = Field(None, description="Текущая процентная ставка")
    loan_remainder: Optional[int] = Field(None, description="Остаток долга в рублях")

class CarLoanDetailsSchema(BaseAnalysis):
    """Блок: Автокредит"""
    condition: Optional[Literal["new", "used"]] = Field(None, description="Новый или подержаный")
    car_cost: Optional[int] = Field(None, description="Стоимость автомобиля")
    has_down_payment: Optional[bool] = Field(None, description="Наличие первоначального взноса")

class GeneralCreditDetailsSchema(BaseAnalysis):
    """Блок: Рефинансирование / Потребительский"""
    required_amount: Optional[int] = Field(None, description="Сумма кредита, которая нужна")
    total_debt: Optional[int] = Field(None, description="Общая задолженность по всем кредитам")

class DocumentWaitSchema(BaseAnalysis):
    """Этап: Ожидание документов"""
    user_sent_file: bool = Field(description="Пользователь утверждает, что отправил файл или мы видим вложение")
    ready_to_proceed: bool = Field(description="Пользователь подтвердил, что готов предоставить отчеты")

# --- СЛОВАРЬ ШАГОВ (Константы для навигации) ---

class Steps:
    # База
    CONSENT = "STEP_CONSENT"
    NAME = "STEP_NAME"
    PHONE = "STEP_PHONE"
    CITY = "STEP_CITY"
    
    # Меню
    MAIN_MENU = "STEP_MAIN_MENU"
    
    # Ветки из Меню (Курс, Консультация, Стоимость)
    COURSE_INFO = "STEP_COURSE_INFO"       # Выдача ссылки и завершение
    CONSULT_INFO = "STEP_CONSULT_INFO"     # Рассказ про 10к и ожидание согласия
    PRICING_INFO = "STEP_PRICING_INFO"     # Выдача цен и возврат в начало/меню
    
    # Стоп-факторы (Ветка Кредит/Ипотека)
    SF_SENIORITY = "STEP_SF_SENIORITY"
    SF_DELAYS = "STEP_SF_DELAYS"
    SF_FSSP = "STEP_SF_FSSP"
    SF_MFO = "STEP_SF_MFO"
    SF_BANKRUPTCY = "STEP_SF_BANKRUPTCY"
    
    # Результат квалификации (если есть SF -> предлагаем залог)
    QUALIFY_RESULT = "STEP_QUALIFY_RESULT"
    
    # Выбор типа кредита (если SF нет или согласились на залог)
    SELECT_CREDIT_TYPE = "STEP_SELECT_CREDIT_TYPE"
    
    # Детали конкретных веток
    COLLATERAL_DETAILS = "STEP_COLLATERAL_DETAILS"
    MORTGAGE_DETAILS = "STEP_MORTGAGE_DETAILS"
    CAR_DETAILS = "STEP_CAR_DETAILS"
    REFINANCE_DETAILS = "STEP_REFINANCE_DETAILS"
    CONSUMER_DETAILS = "STEP_CONSUMER_DETAILS"
    
    # Документы
    DOCS_INSTRUCTION = "STEP_DOCS_INSTRUCTION"
    DOCS_WAIT = "STEP_DOCS_WAIT"
    
    # Финал
    FINAL_HANDOVER = "STEP_FINAL_HANDOVER"