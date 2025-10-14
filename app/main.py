"""Application entry point for the Telegram Sales Bot."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# Setup logging as the absolute first step before any app imports
from app.logging_config import setup_logging
setup_logging()

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from app.api import api_router
from app.config import settings
from app.db import close_db, init_db, get_db
from app.bot import bot, dp, on_startup, on_shutdown
from app.services.scheduler_service import scheduler_service
from app.services.redis_service import redis_service

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    # Startup
    logger.info("Starting Telegram Sales Bot application")
    await init_db()
    await redis_service.initialize()
    await on_startup()
    scheduler_service.start()
    
    yield
    
    # Shutdown
    logger.info("Shutting down Telegram Sales Bot application")
    scheduler_service.stop()
    await on_shutdown()
    await redis_service.close()
    await close_db()


# Create FastAPI application
app = FastAPI(
    title="Telegram Sales Bot",
    description="Intelligent Telegram bot for cryptocurrency education sales",
    version="1.0.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.include_router(api_router)


@app.get("/healthz")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "telegram-sales-bot"}


@app.get("/readyz")
async def ready_check() -> Response:
    """Readiness check endpoint."""
    # Check DB connection
    db_ok = False
    try:
        async for db in get_db():
            await db.execute("SELECT 1")
            db_ok = True
            break
    except Exception as e:
        logger.error("Readiness check failed: DB connection error", error=str(e))
        db_ok = False

    # Check Redis connection
    redis_ok = False
    try:
        redis_client = redis_service.get_client()
        if redis_client:
            await redis_client.ping()
            redis_ok = True
    except Exception as e:
        logger.error("Readiness check failed: Redis connection error", error=str(e))
        redis_ok = False

    if db_ok and redis_ok:
        return JSONResponse({"status": "ready"})
    else:
        return JSONResponse(
            {"status": "not_ready", "checks": {"database": db_ok, "redis": redis_ok}},
            status_code=503
        )


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    """Handle Telegram webhook updates."""
    try:
        # Verify webhook secret
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_header != settings.telegram_webhook_secret:
            logger.warning("Invalid webhook secret", secret=secret_header)
            return JSONResponse({"status": "error"}, status_code=403)
        
        # Get update data
        update_data = await request.json()
        
        # Process update
        await dp.feed_webhook_update(bot, update_data)
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error("Webhook processing error", error=str(e), exc_info=True)
        return JSONResponse({"status": "error"}, status_code=500)


# GetCourse webhook integration removed


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
