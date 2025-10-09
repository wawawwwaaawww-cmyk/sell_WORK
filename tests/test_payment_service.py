"""Tests for payment service logic."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import User, Product, Payment, PaymentStatus
from app.services.payment_service import PaymentService


async def _create_user(db_session, user_id: int) -> User:
    user = User(
        id=user_id,
        telegram_id=user_id,
        username=f"user{user_id}",
        segment="hot",
        lead_score=15,
        funnel_stage="payment",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _create_product(db_session, product_id: int) -> Product:
    product = Product(
        id=product_id,
        code=f"tariff{product_id}",
        name="Test Tariff",
        price=Decimal("99000"),
        is_active=True,
        payment_landing_url="https://landing.example/full",
        meta={"payment_links": {"installment": "https://landing.example/install"}},
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest.mark.asyncio
async def test_create_payment_link_full(db_session):
    user = await _create_user(db_session, user_id=2001)
    product = await _create_product(db_session, product_id=3001)

    service = PaymentService(db_session)

    eligible, _ = await service.check_payment_eligibility(user, product)
    assert eligible

    success, link, message = await service.create_payment_link(
        user_id=user.id,
        product_id=product.id,
        payment_type="full",
    )

    assert success
    assert link == "https://landing.example/full"
    assert "готова" in message.lower()

    result = await db_session.execute(select(Payment))
    payment = result.scalars().one()
    assert payment.payment_type == "full"
    assert payment.manual_link is False
    assert payment.status == PaymentStatus.SENT
    assert payment.landing_url == link


@pytest.mark.asyncio
async def test_create_payment_link_manual(db_session):
    user = await _create_user(db_session, user_id=2002)
    product = await _create_product(db_session, product_id=3002)

    service = PaymentService(db_session)

    success, link, message = await service.create_payment_link(
        user_id=user.id,
        product_id=product.id,
        payment_type="manual",
        manual_link=True,
        conditions_note="Нужна скидка",
    )

    assert success
    assert link is None
    assert "менеджер" in message.lower()

    result = await db_session.execute(select(Payment).order_by(Payment.created_at.desc()))
    payment = result.scalars().first()
    assert payment.manual_link is True
    assert payment.status == PaymentStatus.CREATED
    assert payment.conditions_note == "Нужна скидка"
