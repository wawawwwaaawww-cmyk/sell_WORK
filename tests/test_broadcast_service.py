"""Tests for broadcast and A/B testing services."""

import pytest
from unittest.mock import AsyncMock, ANY

from app.models import User, UserSegment
from app.services.broadcast_service import BroadcastService
from app.services.ab_testing_service import ABTestingService
from app.models import ABResult
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

    assert "sent" in result and result["sent"] == 10
    assert result["failed"] == 0
    assert bot_mock.send_message.await_count == 10

    ab_results = (await db_session.execute(
        select(ABResult).where(ABResult.ab_test_id == ab_test_id)
    )).scalars().all()
    assert ab_results
    delivered_sum = sum(r.delivered for r in ab_results)
    assert delivered_sum == 10

    # Должно быть не больше двух результатов (по одному на вариант)
    assert {r.variant_code for r in ab_results} <= {"A", "B"}


@pytest.mark.asyncio
async def test_analyze_ab_test_results(db_session):
    """Сервис A/B тестов корректно вычисляет метрики и победителя."""
    service = ABTestingService(db_session)
    ab_test = await service.create_ab_test(
        name="CTA", 
        variant_a_title="A", variant_a_body="BODY A",
        variant_b_title="B", variant_b_body="BODY B"
    )
    await service.repository.create_or_update_result(ab_test.id, "A", delivered=100, clicks=30)
    await service.repository.create_or_update_result(ab_test.id, "B", delivered=90, clicks=45)

    analysis = await service.analyze_test_results(ab_test.id)

    assert analysis["test_id"] == ab_test.id
    assert analysis["variants"]["A"]["ctr"] == 0.3
    assert analysis["variants"]["B"]["ctr"] == 0.5
    assert analysis["winner"] == "B"
