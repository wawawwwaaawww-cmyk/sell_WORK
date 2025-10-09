"""Tests for analytics formatter helpers."""

from app.services.analytics_formatter import format_broadcast_metrics


def test_format_broadcast_metrics_renders_sections():
    metrics = {
        "total_broadcasts": 5,
        "broadcasts_last_period": 2,
        "deliveries": {
            "total": 120,
            "sent": 110,
            "failed": 5,
            "pending": 5,
            "unique_recipients": 95,
            "avg_recipients_per_broadcast": 55.5,
            "failure_rate": 0.0416,
        },
        "latest": {"title": "Promo", "created_at": "2025-10-03T10:00:00"},
    }

    rendered = format_broadcast_metrics(metrics)

    assert "📢" in rendered
    assert "Всего кампаний: 5" in rendered
    assert "⚠️ Ошибки: 4.2%" in rendered
    assert "Последняя рассылка" in rendered


def test_format_broadcast_metrics_handles_missing_values():
    rendered = format_broadcast_metrics({})

    assert "Всего кампаний: 0" in rendered
    assert "Σ Всего: 0" in rendered
