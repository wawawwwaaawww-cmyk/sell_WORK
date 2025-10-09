"""Tests for the user service reflecting the current domain logic."""

import asyncio
import pytest

from app.models import FunnelStage, UserSegment
from app.services.user_service import UserService


@pytest.mark.asyncio
async def test_get_or_create_user_creates_and_updates(db_session):
    """`get_or_create_user` создаёт запись и обновляет данные при повторном вызове."""
    service = UserService(db_session)

    created = await service.get_or_create_user(
        telegram_id=123456789,
        username="testuser",
        first_name="Test",
        last_name="User",
    )

    # В новой записи выставляются базовые поля и стадия воронки
    assert created.telegram_id == 123456789
    assert created.username == "testuser"
    assert created.first_name == "Test"
    assert created.last_name == "User"
    assert created.funnel_stage == FunnelStage.NEW

    # Повторный вызов должен вернуть существующего пользователя и обновить переданные поля
    updated = await service.get_or_create_user(
        telegram_id=123456789,
        username="updated_user",
        first_name="Updated",
    )

    assert updated.id == created.id
    assert updated.username == "updated_user"
    assert updated.first_name == "Updated"
    # last_name не передавали — должно сохраниться прежнее значение
    assert updated.last_name == "User"


@pytest.mark.asyncio
async def test_update_user_segment_sets_score_and_segment(db_session):
    """Сегмент и скоринг обновляются в соответствии с вычисленным сегментом."""
    service = UserService(db_session)
    user = await service.get_or_create_user(telegram_id=111)

    updated = await service.update_user_segment(user, lead_score=11)

    assert updated.lead_score == 11
    assert updated.segment == UserSegment.HOT


@pytest.mark.asyncio
async def test_get_conversation_history_returns_latest_messages(db_session):
    """История должна возвращать сообщения в хронологическом порядке."""
    service = UserService(db_session)
    user = await service.get_or_create_user(telegram_id=222)

    await service.save_message(user_id=user.id, role="user", text="Привет")
    await asyncio.sleep(0.01)
    await service.save_message(user_id=user.id, role="bot", text="Здравствуйте")
    await asyncio.sleep(0.01)
    await service.save_message(user_id=user.id, role="user", text="Мне нужен продукт")

    history = await service.get_conversation_history(user.id, limit=5)

    assert len(history) == 3

    texts = {entry["text"] for entry in history}
    assert texts == {"Привет", "Здравствуйте", "Мне нужен продукт"}
    assert any(entry["text"] == "Мне нужен продукт" and entry["role"] == "user" for entry in history)


@pytest.mark.asyncio
async def test_save_message_handles_unknown_roles(db_session):
    """Неизвестная роль должна сохраняться как пользовательская запись без исключений."""
    service = UserService(db_session)
    user = await service.get_or_create_user(telegram_id=333)

    result = await service.save_message(user_id=user.id, role="assistant", text="Ответ бота")
    assert result is True

    history = await service.get_conversation_history(user.id, limit=1)
    assert history[0]["role"] == "bot"
    assert history[0]["text"] == "Ответ бота"
