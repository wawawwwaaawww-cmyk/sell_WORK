"""Проверки LLM-службы с учётом актуального интерфейса."""

import json
from unittest.mock import AsyncMock

import pytest

from app.models import User, UserSegment
from app.services.llm_service import LLMContext, LLMResponse, LLMService
from app.safety.validator import SafetyIssue
from app.config import settings


@pytest.mark.asyncio
async def test_generate_response_uses_policy_and_returns_structure(monkeypatch):
    """Сервис должен возвращать структурированный ответ при успешном ответе модели."""
    service = LLMService()

    # Подготавливаем контекст с пользователем сегмента COLD
    user = User(telegram_id=1001, segment=UserSegment.COLD, lead_score=2)
    context = LLMContext(user=user, messages_history=[])

    # Подменяем обращение к OpenAI
    payload = {
        "reply_text": "Тестовое сообщение про криптовалюты",
        "buttons": [{"text": "Консультация", "callback": "consult:schedule"}],
        "next_action": "ask",
        "confidence": 0.82,
    }

    monkeypatch.setattr(service, "_use_responses_api", lambda: False)
    monkeypatch.setattr(service, "_call_chat_completion", AsyncMock(return_value=json.dumps(payload)))
    monkeypatch.setattr(service.safety_validator, "validate_response", lambda text: (text, []))
    monkeypatch.setattr(service.safety_validator, "is_safe_for_auto_send", lambda issues: True)
    monkeypatch.setattr(service.safety_validator, "should_escalate_to_manager", lambda confidence, issues: False)

    # Убеждаемся, что тест не пойдёт в сеть, даже если ключ задан
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    response = await service.generate_response(context)

    assert isinstance(response, LLMResponse)
    assert response.reply_text == payload["reply_text"]
    assert response.buttons[0]["callback"] == "consult:schedule"
    assert response.next_action in {"ask", "show_materials", "escalate_to_manager"}
    assert response.is_safe is True


@pytest.mark.asyncio
async def test_generate_response_returns_fallback_without_api_key(monkeypatch):
    """Если API ключ не задан, сервис должен вернуть безопасный фоллбэк."""
    service = LLMService()
    user = User(telegram_id=2002, segment=UserSegment.WARM)
    context = LLMContext(user=user, messages_history=[])

    monkeypatch.setattr(settings, "openai_api_key", "")

    response = await service.generate_response(context)

    assert response.next_action == "fallback_flow"
    assert any("Пройти тест" in btn["text"] for btn in response.buttons)
    assert response.is_safe is True



@pytest.mark.asyncio
async def test_generate_response_low_confidence_escalates(monkeypatch):
    """Низкая уверенность должна переводить диалог на менеджера."""
    service = LLMService()
    user = User(telegram_id=3003, segment=UserSegment.WARM, lead_score=5)
    context = LLMContext(user=user, messages_history=[])

    payload = {
        "reply_text": "Ответ требует уточнения",
        "buttons": [],
        "next_action": "ask",
        "confidence": 0.2,
    }

    monkeypatch.setattr(service, "_use_responses_api", lambda: False)
    monkeypatch.setattr(service, "_call_chat_completion", AsyncMock(return_value=json.dumps(payload)))
    monkeypatch.setattr(service.safety_validator, "validate_response", lambda text: (text, []))
    monkeypatch.setattr(service.safety_validator, "is_safe_for_auto_send", lambda issues: True)
    monkeypatch.setattr(service.safety_validator, "should_escalate_to_manager", lambda confidence, issues: False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    response = await service.generate_response(context)

    assert response.next_action == "escalate_to_manager"
    assert "менедж" in response.reply_text.lower()
    assert response.buttons[0]["callback"] == "manager:request"


@pytest.mark.asyncio
async def test_generate_response_high_risk_safety_triggers_escalation(monkeypatch):
    """При высокорисковых safety-замечаниях должен включаться fallback."""
    service = LLMService()
    user = User(telegram_id=4004, segment=UserSegment.COLD, lead_score=3)
    context = LLMContext(user=user, messages_history=[])

    payload = {
        "reply_text": "Гарантированная прибыль",
        "buttons": [],
        "next_action": "ask",
        "confidence": 0.8,
    }

    issues = [SafetyIssue(type="prohibited", original="гарантированная прибыль", suggestion="", severity="high")]

    monkeypatch.setattr(service, "_use_responses_api", lambda: False)
    monkeypatch.setattr(service, "_call_chat_completion", AsyncMock(return_value=json.dumps(payload)))
    monkeypatch.setattr(service.safety_validator, "validate_response", lambda text: (text, issues))
    monkeypatch.setattr(service.safety_validator, "is_safe_for_auto_send", lambda _: False)
    monkeypatch.setattr(service.safety_validator, "should_escalate_to_manager", lambda confidence, issues: False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    response = await service.generate_response(context)

    assert response.next_action == "escalate_to_manager"
    assert response.safety_issues == issues
    assert any(btn["callback"] == "manager:request" for btn in response.buttons)
