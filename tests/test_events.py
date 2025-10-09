"""Async tests validating event logging behaviour."""

import pytest
from sqlalchemy import select

from app.models import Event
from app.services.event_service import EventService
from app.services.user_service import UserService


@pytest.mark.asyncio
async def test_event_logging_via_service(db_session):
    """Вставка события через EventService должна сохранять payload и тип."""
    user_service = UserService(db_session)
    user = await user_service.get_or_create_user(telegram_id=987654321, first_name="Tester")

    service = EventService(db_session)
    await service.log_event(
        user_id=user.id,
        event_type="unit_test",
        payload={"source": "pytest", "ok": True},
    )

    result = await db_session.execute(
        select(Event).where(Event.user_id == user.id, Event.type == "unit_test")
    )
    event = result.scalar_one()

    assert event.payload["ok"] is True
    assert event.payload["source"] == "pytest"
