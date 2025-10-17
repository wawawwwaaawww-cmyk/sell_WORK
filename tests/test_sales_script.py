"""Tests for sales script generation service."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Lead, LeadEvent, LeadStatus, User
from app.services.sales_script_service import SalesScriptService


class _DummyChatCompletions:
    def __init__(self, text: str) -> None:
        self._text = text

    async def create(self, **kwargs):  # pragma: no cover - simple stub
        message = SimpleNamespace(content=self._text)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


class DummyLLMClient:
    def __init__(self, text: str) -> None:
        self.chat = SimpleNamespace(completions=_DummyChatCompletions(text))


@pytest.mark.asyncio
async def test_sales_script_generation_and_regeneration(db_session):
    user = User(
        telegram_id=123456,
        username="testuser",
        first_name="Test",
        segment="warm",
        lead_score=5,
    )
    db_session.add(user)
    await db_session.flush()

    lead = Lead(
        user_id=user.id,
        status=LeadStatus.NEW,
        priority=40,
    )
    db_session.add(lead)
    await db_session.flush()

    service = SalesScriptService(db_session, llm_client=DummyLLMClient("Section content"))

    result = await service.ensure_script(lead, user, reason="test_initial")
    assert result.version == 1
    assert lead.sales_script_md
    assert lead.sales_script_inputs_hash

    events = (
        await db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == lead.id)
        )
    ).scalars().all()
    assert any(event.event_type == "sales_script_generated" for event in events)

    # No changes should reuse existing script
    result_again = await service.ensure_script(lead, user, reason="test_repeat")
    assert result_again.version == 1
    assert not result_again.regenerated

    # Update user information to trigger regeneration
    user.phone = "+1234567890"
    await db_session.flush()

    result_regen = await service.ensure_script(lead, user, reason="test_update")
    assert result_regen.version == 2
    assert result_regen.regenerated

    events = (
        await db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == lead.id)
        )
    ).scalars().all()
    assert any(event.event_type == "sales_script_regenerated" for event in events)
