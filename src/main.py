from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.api.webhooks import router as webhooks_router
from src.core.redis_client import redis_manager
from src.core.logging import setup_logging
from src.core.config import settings

# Настраиваем логи для API
logger = setup_logging("api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    await redis_manager.connect()
    logger.info("🚀 API Service Started (Redis connected)")
    yield
    # --- Shutdown ---
    await redis_manager.disconnect()
    logger.info("🛑 API Service Stopped (Redis disconnected)")

def get_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="1.0.0",
        debug=settings.DEBUG,
        lifespan=lifespan
    )

    # Подключаем роутеры
    # Вебхук будет доступен по адресу: http://IP:8020/api/v1/webhooks/pact
    app.include_router(webhooks_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "healthy", "service": "api"}

    return app

app = get_app()