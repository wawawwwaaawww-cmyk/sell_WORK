"""Helpers for rendering analytics reports for dashboards and UIs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

SEGMENT_LABELS = {
    "cold": "❄️ Холодные",
    "warm": "🔥 Тёплые",
    "hot": "🌶️ Горячие",
    "unknown": "❔ Неопределено",
}

LEAD_STATUS_LABELS = {
    "new": "🆕 Новые",
    "taken": "🔄 В работе",
    "done": "✅ Завершённые",
    "cancelled": "⛔ Отменённые",
    "unknown": "❔ Неопределено",
}

DELIVERY_STATUS_LABELS = {
    "sent": "📤 Отправлено",
    "failed": "⚠️ Ошибки",
    "pending": "⏳ В очереди",
    "unknown": "❔ Неопределено",
}

AB_STATUS_LABELS = {
    "draft": "📝 Черновик",
    "running": "🟢 В работе",
    "completed": "✅ Завершён",
    "paused": "⏸️ Приостановлен",
    "cancelled": "⛔ Отменён",
    "unknown": "❔ Неопределено",
}


Line = Tuple[str, bool]


def clean_enum_value(raw: Optional[str]) -> str:
    """Normalize enum values to lowercase keys."""
    if not raw:
        return "unknown"
    return str(raw).split(".")[-1].lower()


def format_percent(value: Optional[float]) -> str:
    """Format decimal ratio as percentage string."""
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        numeric = 0.0
    return f"{numeric * 100:.1f}%"


def _append(lines: List[Line], text: str, *, bold: bool = False) -> None:
    lines.append((text, bold))


def _build_report_lines(report: Dict[str, Any]) -> List[Line]:
    lines: List[Line] = []

    period = report.get("period_days", 30)
    generated_at = report.get("generated_at") or ""

    users = report.get("users", {})
    leads = report.get("leads", {})
    sales = report.get("sales", {})
    broadcasts = report.get("broadcasts", {})
    ab_tests = report.get("ab_tests", {})

    _append(lines, f"📊 Аналитика за {period} дней", bold=True)
    _append(lines, "")
    _append(lines, "👥 Пользователи", bold=True)
    _append(lines, f"• Всего: {users.get('total_users', 0)}")
    _append(lines, f"• Новые: {users.get('new_users', 0)}")
    _append(lines, f"• Активные 7д: {users.get('active_users', 0)}")

    segments = users.get("segments", {})
    if segments:
        _append(lines, "• Сегменты:")
        for key, count in sorted(segments.items()):
            label = SEGMENT_LABELS.get(clean_enum_value(key), key)
            _append(lines, f"   {label}: {count}")

    _append(lines, "")
    _append(lines, "🧲 Лиды", bold=True)
    _append(lines, f"• Всего: {leads.get('total_leads', 0)}")
    _append(lines, f"• Новые: {leads.get('new_leads', 0)}")

    lead_statuses = leads.get("lead_statuses", {})
    if lead_statuses:
        _append(lines, "• Статусы:")
        for key, count in sorted(lead_statuses.items()):
            label = LEAD_STATUS_LABELS.get(clean_enum_value(key), key)
            _append(lines, f"   {label}: {count}")

    _append(lines, "")
    _append(lines, "💳 Продажи", bold=True)
    _append(lines, f"• Выручка: {sales.get('total_revenue', 0):,.0f} ₽")
    _append(lines, f"• Успешные платежи: {sales.get('successful_payments', 0)}")
    _append(lines, f"• Средний чек: {sales.get('avg_order_value', 0):,.0f} ₽")

    deliveries = (broadcasts.get("deliveries") or {})
    _append(lines, "")
    _append(lines, "📢 Рассылки", bold=True)
    _append(lines, f"• Всего кампаний: {broadcasts.get('total_broadcasts', 0)}")
    _append(lines, f"• За период: {broadcasts.get('broadcasts_last_period', 0)}")
    _append(lines, "• Доставки:")
    if deliveries:
        _append(lines, f"   Σ Всего: {deliveries.get('total', 0)}")
        _append(lines, f"   {DELIVERY_STATUS_LABELS.get('sent')}: {deliveries.get('sent', 0)}")
        _append(lines, f"   {DELIVERY_STATUS_LABELS.get('failed')}: {deliveries.get('failed', 0)}")
        _append(lines, f"   {DELIVERY_STATUS_LABELS.get('pending')}: {deliveries.get('pending', 0)}")
        _append(lines, f"   👥 Уникальные: {deliveries.get('unique_recipients', 0)}")
        avg_reach = deliveries.get("avg_recipients_per_broadcast", 0)
        if isinstance(avg_reach, (int, float)):
            _append(lines, f"   👥 Ср. охват: {avg_reach:.1f}")
        else:
            _append(lines, f"   👥 Ср. охват: {avg_reach}")
        _append(lines, f"   ⚠️ Ошибки: {format_percent(deliveries.get('failure_rate'))}")

    latest = broadcasts.get("latest")
    if latest:
        title = latest.get("title") or "—"
        created_at = latest.get("created_at") or "—"
        _append(lines, f"• Последняя: {title} ({created_at})")

    summary = ab_tests.get("summary") or {}
    _append(lines, "")
    _append(lines, "🧪 A/B тесты", bold=True)
    _append(lines, f"• Всего: {summary.get('total', 0)}")
    _append(lines, f"• Активные: {summary.get('running', 0)}")
    _append(lines, f"• Завершённые: {summary.get('completed', 0)}")

    tests = ab_tests.get("tests") or []
    for test in tests:
        status_value = test.get("status", "unknown")
        status_label = AB_STATUS_LABELS.get(clean_enum_value(status_value), status_value)
        metric_value = str(test.get("metric", "CTR")).upper()
        _append(lines, "")
        _append(lines, f"#{test.get('id')} {test.get('name', 'Без названия')}", bold=True)
        _append(
            lines,
            f"• Статус: {status_label} | Метрика: {metric_value} | Популяция: {test.get('population', 0)}%",
        )

        winner = test.get("winner")
        if winner:
            metric = str(winner.get("metric", "ctr")).upper()
            _append(
                lines,
                f"• Лидер: вариант {winner.get('variant')} ({metric} {format_percent(winner.get('score'))})",
            )

        for variant in test.get("variants", []):
            _append(
                lines,
                "   "
                + f"{variant.get('variant')}: доставлено {variant.get('delivered', 0)}, "
                + f"CTR {format_percent(variant.get('ctr'))}, CR {format_percent(variant.get('cr'))}, "
                + f"конверсии {variant.get('conversions', 0)}",
            )

    if generated_at:
        _append(lines, "")
        _append(lines, f"🕒 Обновлено: {generated_at}")

    return lines


def format_report_for_telegram(report: Dict[str, Any]) -> str:
    """Format analytics report with Telegram-friendly markup."""
    parts = []
    for text, bold in _build_report_lines(report):
        if not text:
            parts.append("")
            continue
        parts.append(f"<b>{text}</b>" if bold else text)
    return "\n".join(parts)


def format_report_as_text(report: Dict[str, Any]) -> str:
    """Format analytics report as plain text."""
    return "\n".join(text for text, _ in _build_report_lines(report))


def format_broadcast_metrics(metrics: Dict[str, Any]) -> str:
    """Render broadcast metrics section as HTML-ready text."""
    deliveries = metrics.get("deliveries") or {}
    latest = metrics.get("latest") or {}

    lines = [
        "📢 <b>Статус рассылок</b>",
        f"• Всего кампаний: {metrics.get('total_broadcasts', 0)}",
        f"• За период: {metrics.get('broadcasts_last_period', 0)}",
        "",
        "🗂 <b>Доставки</b>",
        f"• Σ Всего: {deliveries.get('total', 0)}",
        f"• 📤 Отправлено: {deliveries.get('sent', 0)}",
        f"• ⚠️ Ошибки: {deliveries.get('failed', 0)}",
        f"• ⏳ В очереди: {deliveries.get('pending', 0)}",
        f"• 👥 Уникальные: {deliveries.get('unique_recipients', 0)}",
    ]

    avg_reach = deliveries.get("avg_recipients_per_broadcast")
    if isinstance(avg_reach, (int, float)):
        lines.append(f"• 👥 Ср. охват: {avg_reach:.1f}")
    elif avg_reach is not None:
        lines.append(f"• 👥 Ср. охват: {avg_reach}")

    failure_rate = deliveries.get("failure_rate")
    if isinstance(failure_rate, (int, float)):
        lines.append(f"• ⚠️ Ошибки: {format_percent(failure_rate)}")

    if latest:
        title = latest.get("title") or "—"
        created_at = latest.get("created_at") or "—"
        lines.extend([
            "",
            "🕒 <b>Последняя рассылка</b>",
            f"• Название: {title}",
            f"• Дата: {created_at}",
        ])

    return "\n".join(lines)


__all__ = [
    "AB_STATUS_LABELS",
    "DELIVERY_STATUS_LABELS",
    "LEAD_STATUS_LABELS",
    "SEGMENT_LABELS",
    "clean_enum_value",
    "format_percent",
    "format_report_as_text",
    "format_report_for_telegram",
    "format_broadcast_metrics",
]
