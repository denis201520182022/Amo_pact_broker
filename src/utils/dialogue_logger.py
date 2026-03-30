import os
import json
from datetime import datetime
from typing import Any  # <--- ДОБАВЬ ЭТУ СТРОКУ

class DialogueLogger:
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.log_dir = "debug_logs"
        self.file_path = os.path.join(self.log_dir, f"{conversation_id}.txt")
        
        # Создаем папку, если её нет
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def log_section(self, title: str, content: Any):
        """Записывает блок данных в файл с красивым разделителем.
        Для словарей выводит ключи отдельно, чтобы длинные строки (промпты)
        сохраняли свои переносы строк.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*30} {title} ({timestamp}) {'='*30}\n")
            
            if isinstance(content, dict):
                for key, value in content.items():
                    f.write(f"\n>>> {key.upper()}:\n")
                    if isinstance(value, str):
                        f.write(value)
                    elif isinstance(value, (dict, list)):
                        f.write(json.dumps(value, indent=2, ensure_ascii=False))
                    else:
                        f.write(str(value))
                    f.write("\n")
            elif isinstance(content, (dict, list)):
                # Для списков или если dict передан не как основной контейнер
                f.write(json.dumps(content, indent=2, ensure_ascii=False))
            else:
                f.write(str(content))
            
            f.write(f"\n{'-'*80}\n")

    def log_state_change(self, old_step, new_step, old_data, new_data):
        """Специальный метод для логирования изменений стейта"""
        content = {
            "STEP_TRANSITION": f"{old_step} ===> {new_step}",
            "EXTRACTED_DATA_CHANGES": {
                "BEFORE": old_data,
                "AFTER": new_data
            }
        }
        self.log_section("LOGIC & STATE CHANGE", content)

    def log_event(self, event_type: str, data: Any):
        """
        Записывает событие в 'живой' JSON-лог для отладочного скрипта.
        Позволяет внешнему скрипту получать оперативную информацию о работе воркера.
        """
        live_file = os.path.join(self.log_dir, f"{self.conversation_id}.live.json")
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "data": data
        }
        with open(live_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")