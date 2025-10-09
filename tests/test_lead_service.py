"""Tests for lead management service."""

import pytest

from app.models import User, LeadStatus, LeadNote
from app.services.lead_service import LeadService


async def _create_user(
    db_session,
    *,
    telegram_id: int,
    segment: str = "warm",
    lead_score: int = 10,
) -> User:
    user = User(
        id=telegram_id,
        telegram_id=telegram_id,
        username="lead_tester",
        first_name="Lead",
        last_name="Tester",
        segment=segment,
        lead_score=lead_score,
        funnel_stage="consultation",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_create_lead_priority_hot_payment(db_session):
    """Hot сегмент должен давать максимальный приоритет."""
    user = await _create_user(db_session, telegram_id=1001, segment="hot", lead_score=15)
    service = LeadService(db_session)

    lead = await service.create_lead_from_user(
        user=user,
        trigger_event="payment_initiated",
        conversation_summary="summary",
    )

    assert lead.priority == 100


@pytest.mark.asyncio
async def test_return_lead_to_queue(db_session):
    """Менеджер может вернуть лид в очередь и добавляется заметка."""
    user = await _create_user(db_session, telegram_id=1002)
    service = LeadService(db_session)

    lead = await service.create_lead_from_user(user=user, trigger_event="manager_requested", conversation_summary="summary")
    await service.repository.assign_lead_to_manager(lead, manager_id=12345)

    success, message = await service.return_lead_to_queue(lead.id, 12345)
    await db_session.refresh(lead)

    assert success, message
    assert lead.status == LeadStatus.NEW
    assert lead.assigned_manager_id is None

    notes = await db_session.execute(
        LeadNote.__table__.select().where(LeadNote.lead_id == lead.id)
    )
    note_rows = notes.fetchall()
    assert note_rows, "Ожидаем заметку о возврате"


@pytest.mark.asyncio
async def test_get_lead_statistics(db_session):
    """Статистика учитывает новые и взятые лиды."""
    user = await _create_user(db_session, telegram_id=1003)
    service = LeadService(db_session)

    lead_new = await service.create_lead_from_user(user=user, trigger_event="manager_requested", conversation_summary="summary")
    lead_taken = await service.create_lead_from_user(user=user, trigger_event="payment_initiated", conversation_summary="pay")
    await service.repository.assign_lead_to_manager(lead_taken, manager_id=555)

    stats = await service.get_lead_statistics()

    assert stats["total_active"] == 2
    assert stats["new_leads"] == 1
    assert stats["taken_leads"] == 1
    assert stats["leads_today"] == 2
