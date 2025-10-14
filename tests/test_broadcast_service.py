"""Tests for broadcast and A/B testing services."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, ANY

import pytest

from app.models import User, UserSegment, ABAssignment, ABEvent, ABEventType, ABVariant
from app.services.broadcast_service import BroadcastService
from app.services.ab_testing_service import ABTestingService, VariantDefinition
from sqlalchemy import select


@pytest.mark.asyncio
async def test_send_simple_broadcast(db_session):
    """BroadcastService.send_simple_broadcast рассылает сообщение всем пользователям."""
    bot_mock = AsyncMock()
    bot_mock.send_message = AsyncMock()
    service = BroadcastService(bot_mock, db_session)

    user1 = User(telegram_id=101, segment=UserSegment.COLD, first_name="Test")
    user2 = User(telegram_id=202, segment=UserSegment.WARM, first_name="Demo")
    db_session.add_all([user1, user2])
    await db_session.flush()

    broadcast = await service.create_simple_broadcast(
        title="Promo",
        body="*Тестовая рассылка*",
        buttons=[{"text": "Подробнее", "callback_data": "cta:more"}],
    )

    result = await service.send_simple_broadcast(broadcast.id, delay_between_messages=0)

    assert result == {"sent": 2, "failed": 0, "total": 2}
    assert bot_mock.send_message.await_count == 2
    bot_mock.send_message.assert_any_await(
        chat_id=101,
        text="*Тестовая рассылка*",
        reply_markup=ANY,
        parse_mode="Markdown",
    )


@pytest.mark.asyncio
async def test_send_simple_broadcast_with_rich_content(db_session):
    """BroadcastService корректно рассылает текст и вложения из content."""
    bot_mock = AsyncMock()
    bot_mock.send_message = AsyncMock()
    bot_mock.send_photo = AsyncMock()
    service = BroadcastService(bot_mock, db_session)

    user = User(telegram_id=303, segment=UserSegment.HOT, first_name="Rich")
    db_session.add(user)
    await db_session.flush()

    content = [
        {"type": "text", "text": "<b>Важная новость</b>", "parse_mode": "HTML"},
        {
            "type": "photo",
            "file_id": "photo123",
            "caption": "<i>Посмотрите вложение</i>",
            "parse_mode": "HTML",
        },
    ]

    broadcast = await service.create_simple_broadcast(
        title="Rich",
        body="",
        content=content,
        buttons=[{"text": "Подробнее", "callback_data": "cta:open"}],
    )

    result = await service.send_simple_broadcast(broadcast.id, delay_between_messages=0)

    assert result == {"sent": 1, "failed": 0, "total": 1}
    bot_mock.send_message.assert_awaited_once_with(
        chat_id=303,
        text="<b>Важная новость</b>",
        parse_mode="HTML",
        reply_markup=ANY,
    )
    bot_mock.send_photo.assert_awaited_once_with(
        chat_id=303,
        photo="photo123",
        caption="<i>Посмотрите вложение</i>",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_ab_test_broadcast_records_results(db_session):
    """A/B рассылка должна фиксировать доставку по вариантам."""
    bot_mock = AsyncMock()
    service = BroadcastService(bot_mock, db_session)

    users = [
        User(telegram_id=1000 + idx, segment=UserSegment.COLD)
        for idx in range(10)
    ]
    db_session.add_all(users)
    await db_session.flush()

    ok, msg, ab_test_id = await service.create_ab_broadcast(
        test_name="CTA Buttons",
        variant_a_title="Вариант A",
        variant_a_body="Текст A",
        variant_b_title="Вариант B",
        variant_b_body="Текст B",
        population=100,
    )
    assert ok, msg
    assert ab_test_id is not None

    result = await service.send_ab_test_broadcast(ab_test_id, delay_between_messages=0)

    assert "sent" in result and result["failed"] == 0
    assert result["sent"] == result["total_population"]
    assert bot_mock.send_message.await_count == result["sent"]

    assignments = (
        await db_session.execute(
            select(ABAssignment).where(ABAssignment.test_id == ab_test_id)
        )
    ).scalars().all()
    assert assignments
    assert len(assignments) == result["sent"]
    assert all(assignment.delivered_at is not None for assignment in assignments)

    ab_service = ABTestingService(db_session)
    analysis = await ab_service.analyze_test_results(ab_test_id)
    total_delivered = sum(variant.get("delivered", 0) for variant in analysis.get("variants", []))
    assert total_delivered == result["sent"]


@pytest.mark.asyncio
async def test_analyze_ab_test_results(db_session):
    """Сервис A/B тестов корректно вычисляет метрики и победителя."""
    service = ABTestingService(db_session)

    user_a = User(telegram_id=5551)
    user_b = User(telegram_id=5552)
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    test = await service.create_test(
        name="CTA",
        creator_user_id=0,
        variants=[
            VariantDefinition(title="Вариант A", body="BODY A"),
            VariantDefinition(title="Вариант B", body="BODY B"),
        ],
        start_immediately=False,
    )

    variants = (
        await db_session.execute(
            select(ABVariant).where(ABVariant.ab_test_id == test.id)
        )
    ).scalars().all()
    variants = {v.variant_code: v for v in variants}

    assignment_a = ABAssignment(
        test_id=test.id,
        variant_id=variants["A"].id,
        user_id=user_a.id,
        chat_id=user_a.telegram_id,
        hash_value=0.1,
        delivered_at=datetime.now(timezone.utc),
    )
    assignment_b = ABAssignment(
        test_id=test.id,
        variant_id=variants["B"].id,
        user_id=user_b.id,
        chat_id=user_b.telegram_id,
        hash_value=0.2,
        delivered_at=datetime.now(timezone.utc),
    )
    db_session.add_all([assignment_a, assignment_b])
    await db_session.flush()

    click_event_a = ABEvent(
        test_id=test.id,
        variant_id=variants["A"].id,
        assignment_id=assignment_a.id,
        user_id=user_a.id,
        event_type=ABEventType.CLICKED,
    )
    click_event_b = ABEvent(
        test_id=test.id,
        variant_id=variants["B"].id,
        assignment_id=assignment_b.id,
        user_id=user_b.id,
        event_type=ABEventType.CLICKED,
    )
    lead_event_b = ABEvent(
        test_id=test.id,
        variant_id=variants["B"].id,
        assignment_id=assignment_b.id,
        user_id=user_b.id,
        event_type=ABEventType.LEAD_CREATED,
    )
    db_session.add_all([click_event_a, click_event_b, lead_event_b])
    await db_session.flush()

    analysis = await service.analyze_test_results(test.id)

    variants_stats = {variant["variant"]: variant for variant in analysis["variants"]}

    assert variants_stats["A"]["ctr"] == pytest.approx(1.0)
    assert variants_stats["B"]["ctr"] == pytest.approx(1.0)
    assert variants_stats["B"]["cr"] == pytest.approx(1.0)
    assert analysis["winner"]["variant"] == "B"
