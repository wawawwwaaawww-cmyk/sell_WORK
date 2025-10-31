import pytest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.lead_profile_service import LeadProfileService, STAGE_SEQUENCE


@pytest.fixture
def service():
    session = MagicMock(spec=AsyncSession)
    svc = LeadProfileService(session)
    return svc


def test_merge_profile_data_deduplicates_lists(service):
    base = {
        "financial_goals": ["пассивный доход"],
        "diagnostics": {"facts": "ведёт учёт"},
    }
    updates = {
        "financial_goals": ["пассивный доход", "капитал"],
        "diagnostics": {"implications": "разные решения"},
        "name": "Иван",
    }

    merged = service._merge_profile_data(base, updates)

    assert merged["financial_goals"] == ["пассивный доход", "капитал"]
    assert merged["diagnostics"]["facts"] == "ведёт учёт"
    assert merged["diagnostics"]["implications"] == "разные решения"
    assert merged["name"] == "Иван"


def test_resolve_next_stage_does_not_skip(service):
    start = STAGE_SEQUENCE[0]
    requested = STAGE_SEQUENCE[2]

    resolved = service._resolve_next_stage(start, requested)

    assert resolved == STAGE_SEQUENCE[1]


@pytest.mark.asyncio
async def test_sync_user_lead_score_scales_to_legacy_range(service):
    user = MagicMock()
    service.user_service.update_user_segment = AsyncMock()

    await service._sync_user_lead_score(user, 92)

    service.user_service.update_user_segment.assert_awaited_once()
    call = service.user_service.update_user_segment.await_args
    assert call.args[0] is user
    assert call.args[1] == 14  # ceil(92/7)
