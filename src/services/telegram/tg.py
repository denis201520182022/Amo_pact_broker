# src\services\telegram\tg.py

import asyncio
from typing import List, Optional, Union
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from src.core.config import settings
from src.core.logging import logger
from aiogram.client.session.aiohttp import AiohttpSession
class TelegramService:
    def __init__(self):
        # --- ЛОГИКА ПРОКСИ ---
        session = None
        if settings.proxy_url:
            logger.info(f"🌐 Telegram Bot is using proxy: {settings.PROXY_HOST}")
            session = AiohttpSession(proxy=settings.proxy_url)
        
        self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)
        self.admin_id = settings.TELEGRAM_ADMIN_CHAT_ID
        
        # 1. Список ВСЕХ разрешенных ID (и админы, и пользователи)
        self.user_ids = [
            int(uid.strip()) 
            for uid in (settings.TELEGRAM_USER_IDS or "").split(",") 
            if uid.strip()
        ]
        
        # 2. Список только АДМИНОВ
        self.admin_ids = [
            int(aid.strip()) 
            for aid in (settings.TELEGRAM_ADMIN_IDS or "").split(",") 
            if aid.strip()
        ]
        self.report_chat_id = settings.TELEGRAM_REPORT_CHAT_ID
    def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь администратором"""
        return user_id in self.admin_ids 
        
    async def send_tech_alert(self, message: str):
        """Отправка технического алерта админу (ошибки системы)"""
        if not self.admin_id:
            return
        try:
            text = f"🚨 <b>TECH ALERT</b>\n\n{message}"
            await self.bot.send_message(self.admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка отправки тех. алерта: {e}")

    async def send_global_notification(self, message: str):
        """Рассылка всем пользователям из списка"""
        tasks = []
        for user_id in self.user_ids:
            tasks.append(self.bot.send_message(user_id, message, parse_mode=ParseMode.HTML))
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"Ошибка при массовой рассылке ТГ: {res}")

    async def send_report_card(self, title: str, fields: dict, link: Optional[str] = None):
        """
        Универсальная карточка диалога.
        fields: {'Тип': 'Ипотека', 'Файлы': '2 из 3'}
        """
        if not self.report_chat_id:
            return

        text = f"<b>{title}</b>\n\n"
        for key, value in fields.items():
            text += f"🔹 <b>{key}:</b> {value}\n"
        
        builder = InlineKeyboardBuilder()
        if link:
            builder.button(text="📂 Открыть диалог", url=link)

        try:
            await self.bot.send_message(
                self.report_chat_id, 
                text, 
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup()
            )
        except Exception as e:
            logger.error(f"Ошибка отправки карточки в ТГ: {e}")

# Синглтон для уведомлений
tg_service = TelegramService()