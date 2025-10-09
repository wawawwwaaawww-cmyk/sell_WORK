"""API package providing FastAPI routers."""

from fastapi import APIRouter

from app.api.routes.analytics import router as analytics_router

api_router = APIRouter()
api_router.include_router(analytics_router)

__all__ = ["api_router"]
