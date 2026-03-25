from typing import Optional, List
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )
    TEST: bool = False 
    # --- Project ---
    PROJECT_NAME: str = "AI Broker Bot"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    DEBOUNCE_SECONDS: int = 10

    # --- Database (PostgreSQL) ---
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432

    @computed_field
    @property
    def database_url(self) -> str:
        # Используем asyncpg для асинхронной работы SQLAlchemy
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # --- Redis ---
    REDIS_URL: str

    # --- OpenAI ---
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o-mini"

    # --- Pact.im ---
    PACT_API_TOKEN: str
    PACT_COMPANY_ID: str

    # --- amoCRM ---
    AMO_SUBDOMAIN: str
    AMO_LONG_TERM_TOKEN: str
    # Pydantic автоматически распарсит строку вида [1, 2, 3] из .env в список int
    ALLOWED_PIPELINES: List[int] = Field(default_factory=list)

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ADMIN_CHAT_ID: Optional[int] = None
    TELEGRAM_USER_IDS: Optional[str] = None  # Список ID через запятую
    TELEGRAM_REPORT_CHAT_ID: Optional[str] = None  # ID чата для карточек-отчетов

    # --- Proxy ---
    PROXY_HOST: Optional[str] = None
    PROXY_PORT: Optional[int] = None
    PROXY_USER: Optional[str] = None
    PROXY_PASSWORD: Optional[str] = None

    @computed_field
    @property
    def proxy_url(self) -> Optional[str]:
        if self.PROXY_HOST and self.PROXY_PORT:
            if self.PROXY_USER and self.PROXY_PASSWORD:
                return f"http://{self.PROXY_USER}:{self.PROXY_PASSWORD}@{self.PROXY_HOST}:{self.PROXY_PORT}"
            return f"http://{self.PROXY_HOST}:{self.PROXY_PORT}"
        return None

    # --- Security ---
    WEBHOOK_SECRET: str

# Создаем экземпляр настроек
settings = Settings()