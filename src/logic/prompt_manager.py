# src/logic/prompt_manager.py
import yaml
from src.core.config import settings

class PromptManager:
    def __init__(self):
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            self.prompts = yaml.safe_load(f)
        with open("config/settings.yaml", "r", encoding="utf-8") as f:
            self.settings = yaml.safe_load(f)

    def get_system_prompt(self, state: str, extracted_data: dict, stop_factors: list) -> str:
        base = self.prompts["system_base"]
        
        # Подставляем текущую ситуацию
        context = self.prompts["current_context"].format(
            current_state=state,
            extracted_data=extracted_data,
            stop_factors=stop_factors
        )
        
        # Добавляем динамические данные (цены/ссылки)
        settings_str = f"\nСПРАВОЧНИК: {self.settings}"
        
        return f"{base}\n{context}\n{settings_str}\n{self.prompts['instructions']}"

prompt_manager = PromptManager()