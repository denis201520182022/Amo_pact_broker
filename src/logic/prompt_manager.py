import os
import yaml
from typing import Type, Tuple, Dict, Any
from pydantic import BaseModel
from src.logic.states import (
    Steps, 
    ConsentSchema, 
    UserBasicInfoSchema, 
    MenuSelectionSchema, 
    ConsultationConsentSchema,
    StopFactorCheckSchema,
    CreditTypeSelectionSchema,
    CollateralDetailsSchema,
    MortgageDetailsSchema,
    CarLoanDetailsSchema,
    GeneralCreditDetailsSchema,
    DocumentWaitSchema,
    BaseAnalysis
)
from src.services.kb_service import kb_service

class PromptManager:
    def __init__(self):
        self.config_path = os.path.join("config", "prompts.yaml")
        self.prompts = self._load_prompts()
        
        # Маппинг шагов на Pydantic схемы для Анализатора (AI-1)
        self._schema_map: Dict[str, Type[BaseModel]] = {
            Steps.CONSENT: ConsentSchema,
            Steps.NAME: UserBasicInfoSchema,
            Steps.PHONE: UserBasicInfoSchema,
            Steps.CITY: UserBasicInfoSchema,
            Steps.MAIN_MENU: MenuSelectionSchema,
            Steps.CONSULT_INFO: ConsultationConsentSchema,
            
            # Все стоп-факторы используют одну схему (теперь с полем is_active)
            Steps.SF_SENIORITY: StopFactorCheckSchema,
            Steps.SF_DELAYS: StopFactorCheckSchema,
            Steps.SF_FSSP: StopFactorCheckSchema,
            Steps.SF_MFO: StopFactorCheckSchema,
            Steps.SF_BANKRUPTCY: StopFactorCheckSchema,
            
            # Квалификация (предложение залога при стоп-факторах)
            # Используем CollateralDetailsSchema, так как там есть флаг no_collateral
            Steps.QUALIFY_RESULT: CollateralDetailsSchema,
            
            Steps.SELECT_CREDIT_TYPE: CreditTypeSelectionSchema,
            Steps.PRICING_INFO: BaseAnalysis, 
            Steps.COURSE_INFO: BaseAnalysis,
            
            # Детали конкретных веток
            Steps.COLLATERAL_DETAILS: CollateralDetailsSchema,
            Steps.MORTGAGE_DETAILS: MortgageDetailsSchema,
            Steps.CAR_DETAILS: CarLoanDetailsSchema,
            Steps.REFINANCE_DETAILS: GeneralCreditDetailsSchema,
            Steps.CONSUMER_DETAILS: GeneralCreditDetailsSchema,
            
            # Документы
            Steps.DOCS_INSTRUCTION: DocumentWaitSchema, # Чтобы поймать "Готов/Да"
            Steps.DOCS_WAIT: DocumentWaitSchema,        # Чтобы поймать комментарии "скину позже"
        }

    def _load_prompts(self) -> Dict[str, Any]:
        """Загрузка YAML конфигурации"""
        if not os.path.exists(self.config_path):
            return {"steps": {}, "analyzer_system": "", "generator_system": ""}
            
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def get_analyzer_config(self, step_id: str) -> Tuple[Type[BaseModel], str]:
        """
        Возвращает схему и инструкцию для Анализатора (AI-1)
        """
        # Получаем схему из маппинга. Если шага нет в маппинге — базовая схема (BaseAnalysis).
        schema = self._schema_map.get(step_id, BaseAnalysis)
        
        # Получаем инструкцию из YAML
        step_config = self.prompts.get("steps", {}).get(step_id, {})
        instruction = step_config.get("analyzer_instruction", "Проанализируй ответ пользователя.")
        
        return schema, instruction

    async def get_system_prompts(self) -> Tuple[str, str]:
        """
        Возвращает системные промпты для Анализатора и Генератора.
        Соответствует ключам в prompts.yaml
        """
        analyzer_sys = self.prompts.get("analyzer_system", "")
        generator_sys = self.prompts.get("generator_system", "")
        
        # Получаем базу знаний из кеша
        kb_content = await kb_service.get_kb_sync()
        if kb_content:
            kb_block = (
                "\n\n--- БАЗА ЗНАНИЙ ---\n"
                "Используй следующую информацию для ответов на вопросы пользователя:\n"
                f"{kb_content}\n"
                "-------------------\n"
            )
            generator_sys += kb_block
            
        return analyzer_sys, generator_sys

    def get_generator_instruction(self, step_id: str) -> str:
        """
        Возвращает специфическую инструкцию для Генератора (AI-2) 
        на текущем шаге (если она есть)
        """
        return self.prompts.get("steps", {}).get(step_id, {}).get("generator_instruction", "")

# Создаем синглтон
prompt_manager = PromptManager()