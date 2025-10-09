"""Tests for the analytics FastAPI endpoints."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.analytics import router as analytics_router
from app.db import get_db


@pytest.mark.asyncio
async def test_analytics_report_json(db_session):
    """`/analytics` returns structured report in JSON mode."""
    app = FastAPI()
    app.include_router(analytics_router)

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/analytics", params={"days": 14})

    assert response.status_code == 200
    payload = response.json()

    assert payload["period_days"] == 14
    assert "generated_at" in payload
    assert "users" in payload and isinstance(payload["users"], dict)
    assert "broadcasts" in payload and isinstance(payload["broadcasts"], dict)


@pytest.mark.asyncio
async def test_analytics_report_summary_mode(db_session):
    """`/analytics?view=summary` returns text summary payload."""
    app = FastAPI()
    app.include_router(analytics_router)

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/analytics", params={"view": "summary"})

    assert response.status_code == 200
    payload = response.json()

    assert "summary" in payload
    assert "📊" in payload["summary"]
    assert payload["period_days"] == 30
