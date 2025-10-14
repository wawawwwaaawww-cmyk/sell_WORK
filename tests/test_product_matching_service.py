"""Tests for fuzzy product matching service."""

import pytest
from decimal import Decimal

from app.models import User
from app.repositories.product_repository import ProductRepository
from app.repositories.product_criteria_repository import ProductCriteriaRepository
from app.services.product_matching_service import ProductMatchingService
from app.services.survey_service import SurveyService


async def _create_user(session, user_id: int = 101) -> User:
    user = User(
        id=user_id,
        telegram_id=10_000 + user_id,
        first_name="Test",
        segment="warm",
    )
    session.add(user)
    await session.flush()
    return user


async def _answer_full_survey(session, user: User) -> None:
    survey_service = SurveyService(session)
    await survey_service.save_answer(user.id, "q1", "trading")
    await survey_service.save_answer(user.id, "q2", "passive_income")
    await survey_service.save_answer(user.id, "q3", "one_month")
    await survey_service.save_answer(user.id, "q4", "hundred_to_five_hundred")
    await survey_service.save_answer(user.id, "q5", "ready_to_learn")


async def _add_criteria(session, product_id: int, entries: list[dict]) -> None:
    repo = ProductCriteriaRepository(session)
    await repo.replace_for_product(product_id, entries)
    await session.flush()


def _criterion(q_id: int, a_id: int, weight: int = 1, note: str | None = None, *, q_code: str | None = None, a_code: str | None = None) -> dict:
    return {
        "question_id": q_id,
        "answer_id": a_id,
        "question_code": q_code,
        "answer_code": a_code,
        "weight": weight,
        "note": note,
    }


@pytest.mark.asyncio
async def test_product_with_more_matches_wins(db_session):
    user = await _create_user(db_session, 201)
    await _answer_full_survey(db_session, user)

    product_repo = ProductRepository(db_session)

    product_a = await product_repo.create_product(
        code="prod_a",
        name="Product A",
        price=Decimal("50000"),
        description="",
        value_props=["A1", "A2"],
        currency="RUB",
    )
    await _add_criteria(
        db_session,
        product_a.id,
        [
            _criterion(1, 1, 1, q_code="q1", a_code="trading"),
            _criterion(2, 1, 1, q_code="q2", a_code="passive_income"),
            _criterion(3, 1, 1, q_code="q3", a_code="one_month"),
        ],
    )

    product_b = await product_repo.create_product(
        code="prod_b",
        name="Product B",
        price=Decimal("40000"),
        description="",
        value_props=["B1"],
        currency="RUB",
    )
    await _add_criteria(
        db_session,
        product_b.id,
        [
            _criterion(1, 1, 1, q_code="q1", a_code="trading"),
            _criterion(2, 1, 1, q_code="q2", a_code="passive_income"),
            _criterion(3, 2, 1, q_code="q3", a_code="three_months"),
        ],
    )

    await db_session.commit()
    reloaded_user = await db_session.get(User, user.id)

    matching_service = ProductMatchingService(db_session)
    result = await matching_service.match_for_user(reloaded_user, trigger="test_more_matches", log_result=False)

    assert result.best_product is not None
    assert result.best_product.id == product_a.id
    assert result.score == pytest.approx(1.0, rel=1e-3)


@pytest.mark.asyncio
async def test_negative_criteria_penalize_product(db_session):
    user = await _create_user(db_session, 202)
    await _answer_full_survey(db_session, user)

    product_repo = ProductRepository(db_session)

    safe_product = await product_repo.create_product(
        code="prod_safe",
        name="Safe Product",
        price=Decimal("60000"),
        currency="RUB",
    )
    await _add_criteria(
        db_session,
        safe_product.id,
        [
            _criterion(1, 1, 1, q_code="q1", a_code="trading"),
            _criterion(2, 1, 1, q_code="q2", a_code="passive_income"),
        ],
    )

    risky_product = await product_repo.create_product(
        code="prod_risky",
        name="Risky Product",
        price=Decimal("65000"),
        currency="RUB",
    )
    await _add_criteria(
        db_session,
        risky_product.id,
        [
            _criterion(1, 1, 1, q_code="q1", a_code="trading"),
            _criterion(2, 1, 1, q_code="q2", a_code="passive_income"),
            _criterion(5, 1, -2, q_code="q5", a_code="ready_to_learn", note="Не готов"),
        ],
    )

    await db_session.commit()
    reloaded_user = await db_session.get(User, user.id)

    result = await ProductMatchingService(db_session).match_for_user(
        reloaded_user,
        trigger="test_negative",
        log_result=False,
    )

    assert result.best_product is not None
    assert result.best_product.id == safe_product.id
    assert result.candidates[0].score > result.candidates[1].score


@pytest.mark.asyncio
async def test_budget_tie_breaker(db_session):
    user = await _create_user(db_session, 203)
    await _answer_full_survey(db_session, user)

    product_repo = ProductRepository(db_session)

    near_budget = await product_repo.create_product(
        code="prod_budget",
        name="Budget Fit",
        price=Decimal("450"),
        currency="USD",
    )
    await _add_criteria(
        db_session,
        near_budget.id,
        [
            _criterion(1, 1, 1, q_code="q1", a_code="trading"),
            _criterion(2, 1, 1, q_code="q2", a_code="passive_income"),
        ],
    )

    premium = await product_repo.create_product(
        code="prod_premium",
        name="Premium",
        price=Decimal("2500"),
        currency="USD",
    )
    await _add_criteria(
        db_session,
        premium.id,
        [
            _criterion(1, 1, 1, q_code="q1", a_code="trading"),
            _criterion(2, 1, 1, q_code="q2", a_code="passive_income"),
        ],
    )

    await db_session.commit()
    reloaded_user = await db_session.get(User, user.id)

    result = await ProductMatchingService(db_session).match_for_user(
        reloaded_user,
        trigger="test_budget",
        log_result=False,
    )

    assert result.best_product is not None
    assert result.best_product.id == near_budget.id


@pytest.mark.asyncio
async def test_threshold_triggers_consultation(db_session):
    user = await _create_user(db_session, 204)
    await _answer_full_survey(db_session, user)

    product_repo = ProductRepository(db_session)

    weak_product = await product_repo.create_product(
        code="prod_weak",
        name="Weak Match",
        price=Decimal("30000"),
        currency="RUB",
    )
    await _add_criteria(
        db_session,
        weak_product.id,
        [
            _criterion(1, 5, 1, q_code="q1", a_code="no_experience"),
        ],
    )

    await db_session.commit()
    reloaded_user = await db_session.get(User, user.id)

    result = await ProductMatchingService(db_session).match_for_user(
        reloaded_user,
        trigger="test_threshold",
        log_result=False,
    )

    assert result.best_product is None
    assert result.score < ProductMatchingService.DEFAULT_THRESHOLD


@pytest.mark.asyncio
async def test_inactive_products_excluded(db_session):
    user = await _create_user(db_session, 205)
    await _answer_full_survey(db_session, user)

    product_repo = ProductRepository(db_session)

    active_product = await product_repo.create_product(
        code="prod_active",
        name="Active",
        price=Decimal("55000"),
        currency="RUB",
    )
    await _add_criteria(
        db_session,
        active_product.id,
        [_criterion(1, 1, 1, q_code="q1", a_code="trading")],
    )

    inactive = await product_repo.create_product(
        code="prod_inactive",
        name="Inactive",
        price=Decimal("10000"),
        currency="RUB",
    )
    inactive.is_active = False
    await db_session.flush()
    await _add_criteria(
        db_session,
        inactive.id,
        [_criterion(1, 1, 5, q_code="q1", a_code="trading")],
    )

    await db_session.commit()
    reloaded_user = await db_session.get(User, user.id)

    result = await ProductMatchingService(db_session).match_for_user(
        reloaded_user,
        trigger="test_inactive",
        log_result=False,
    )

    assert result.best_product is not None
    assert result.best_product.id == active_product.id
