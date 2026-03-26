from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import BigInteger, Column, String, DateTime, Boolean, Numeric, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, Mapped, mapped_column
from src.db.database import Base

class AppSettings(Base):
    """Глобальный биллинг и лимиты проекта"""
    __tablename__ = 'app_settings'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    low_balance_threshold: Mapped[float] = mapped_column(Numeric(12, 2), default=100.00)
    is_low_balance_alert_sent: Mapped[bool] = mapped_column(default=False)
    tariffs: Mapped[dict] = mapped_column(JSONB, server_default='{}')
    stats: Mapped[dict] = mapped_column(JSONB, server_default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Account(Base):
    """Аккаунт amoCRM (интеграция)"""
    __tablename__ = 'accounts'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # {client_id, client_secret, access_token, refresh_token, subdomain}
    auth_data: Mapped[dict] = mapped_column(JSONB, server_default='{}')
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dialogues: Mapped[List["Dialogue"]] = relationship(back_populates="account")


class Dialogue(Base):
    """Основная сессия диалога, связывающая Pact и amoCRM"""
    __tablename__ = 'dialogues'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    
    # Идентификаторы внешних систем
    pact_conversation_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    # amo_lead_id — привязка к конкретной сделке
    amo_lead_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True, index=True)
    amo_contact_id: Mapped[Optional[str]] = mapped_column(String(50))
    
    # Логика диалога
    current_state: Mapped[str] = mapped_column(String(100), default="START")
    status: Mapped[str] = mapped_column(String(50), default="active") # active, paused, completed, error
    
    # Данные анкеты
    extracted_data: Mapped[dict] = mapped_column(JSONB, server_default='{}')
    
    # История сообщений
    history: Mapped[list] = mapped_column(JSONB, server_default='[]')
    
    # Статистика и метаданные
    usage_stats: Mapped[dict] = mapped_column(JSONB, server_default='{"tokens": 0, "cost": 0}')
    is_active: Mapped[bool] = mapped_column(default=True)
    reminder_level: Mapped[int] = mapped_column(default=0)
    # Временные метки
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Связи
    account: Mapped["Account"] = relationship(back_populates="dialogues")
    llm_logs: Mapped[List["LlmLog"]] = relationship(back_populates="dialogue")

class LlmLog(Base):
    """Логирование запросов к OpenAI"""
    __tablename__ = 'llm_logs'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    dialogue_id: Mapped[int] = mapped_column(ForeignKey('dialogues.id'))
    
    model: Mapped[str] = mapped_column(String(50)) 
    prompt_type: Mapped[str] = mapped_column(String(50)) 
    
    full_response: Mapped[dict] = mapped_column(JSONB)
    
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[float] = mapped_column(Numeric(10, 6), default=0.0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dialogue: Mapped["Dialogue"] = relationship(back_populates="llm_logs")