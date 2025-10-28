"""Fuzzy product matching based on survey answers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Iterable, Any
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductCriteria, User
from app.repositories.product_repository import ProductRepository
from app.repositories.product_criteria_repository import ProductCriteriaRepository
from app.repositories.product_match_log_repository import ProductMatchLogRepository


@dataclass
class MatchCandidate:
    """Intermediate representation of product match."""

    product: Product
    score: float
    positive_matches: List[Dict[str, Any]]
    negative_matches: List[Dict[str, Any]]
    numerator: float
    denominator: float
    budget_diff: float
    urgency_diff: float
    segment_rank: int
    raw_positive: float
    raw_negative: float


@dataclass
class MatchResult:
    """Final recommendation output."""

    best_product: Optional[Product]
    score: float
    candidates: List[MatchCandidate]
    explanation: str
    threshold: float


class ProductMatchingService:
    """Service that recommends products using survey-based fuzzy matching."""

    BUDGET_WEIGHT_MAP = {
        "under_10": 1,
        "ten_to_hundred": 2,
        "hundred_to_five_hundred": 3,
        "five_hundred_to_thousand": 4,
        "over_thousand": 5,
    }

    URGENCY_LEVELS = {
        "just_learning": 1,
        "one_year": 2,
        "half_year": 3,
        "three_months": 4,
        "one_month": 5,
    }

    DEFAULT_THRESHOLD = 0.4

    def __init__(self, session: AsyncSession, *, threshold: float | None = None):
        self.session = session
        self.logger = structlog.get_logger()
        self.threshold = threshold or self.DEFAULT_THRESHOLD
        self._catalog = {}

    async def match_for_user(
        self,
        user: User,
        *,
        trigger: str,
        log_result: bool = True,
        limit: int = 3,
    ) -> MatchResult:
        """Calculate recommendation for a user and optionally log it."""
        repository = ProductRepository(self.session)
        products = await repository.get_active_with_criteria()
        criteria_repo = ProductCriteriaRepository(self.session)
        criteria_map = await criteria_repo.get_for_products([product.id for product in products])
        answers = []
        candidates = self._evaluate_candidates(products, criteria_map, answers, user, limit=limit)

        best = candidates[0] if candidates else None
        score = best.score if best else 0.0
        explanation = self._build_explanation(best, answers) if best else "Недостаточно совпадений."

        result = MatchResult(
            best_product=best.product if best and score >= self.threshold else None,
            score=score if best else 0.0,
            candidates=candidates,
            explanation=explanation,
            threshold=self.threshold,
        )

        if log_result:
            await self._log_result(user, result, trigger)

        return result

    async def evaluate_for_user_id(
        self,
        user_id: int,
        *,
        trigger: str = "admin_probe",
        limit: int = 3,
        log_result: bool = False,
    ) -> Tuple[Optional[User], MatchResult]:
        """Evaluate matching for arbitrary user id (used by admin tools)."""
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError(f"User {user_id} not found.")
        result = await self.match_for_user(
            user,
            trigger=trigger,
            log_result=log_result,
            limit=limit,
        )
        return user, result

    # Internal helpers -----------------------------------------------------------------


    @staticmethod
    def _normalize_markup(text: str) -> str:
        """Strip simple markdown asterisks for clean display."""
        return text.replace("*", "").strip()

    def _evaluate_candidates(
        self,
        products: Iterable[Product],
        criteria_map: Dict[int, List[ProductCriteria]],
        answers: List,
        user: User,
        *,
        limit: int,
    ) -> List[MatchCandidate]:
        """Score products and return sorted candidate list."""
        if not products:
            return []

        answer_map = self._map_user_answers(answers)
        budget_level = self._extract_budget_level(answers)
        urgency_level = self._extract_urgency_level(answers)

        candidates: List[MatchCandidate] = []

        for product in products:
            criteria_records = list(criteria_map.get(product.id, []))
            if not criteria_records:
                continue

            positive_sum = 0.0
            negative_penalty = 0.0
            denominator = self._max_positive_weight(criteria_records)
            if denominator <= 0:
                denominator = 1.0

            positive_matches: List[Dict[str, Any]] = []
            negative_matches: List[Dict[str, Any]] = []

            for criterion in criteria_records:
                question_code = criterion.question_code or self._question_code_from_id(criterion.question_id)
                answer_code = criterion.answer_code
                if not question_code:
                    continue

                user_answer = answer_map.get(question_code)
                if not user_answer:
                    continue
                if answer_code and user_answer["answer_code"] != answer_code:
                    continue

                if answer_code is None and criterion.answer_id is not None:
                    # Align fallback via answer id
                    if user_answer["answer_id"] != criterion.answer_id:
                        continue

                weight = criterion.weight or 0
                match_info = self._build_match_info(criterion, user_answer)

                if weight >= 0:
                    positive_sum += weight
                    positive_matches.append(match_info)
                else:
                    negative_penalty += abs(weight)
                    negative_matches.append(match_info)

            numerator = positive_sum - negative_penalty
            normalized_score = max(0.0, min(1.0, numerator / denominator))

            budget_diff = self._budget_diff(product, budget_level)
            urgency_diff = self._urgency_diff(product, urgency_level)
            segment_rank = self._segment_rank(product, user.segment)

            candidate = MatchCandidate(
                product=product,
                score=normalized_score,
                positive_matches=positive_matches,
                negative_matches=negative_matches,
                numerator=numerator,
                denominator=denominator,
                budget_diff=budget_diff,
                urgency_diff=urgency_diff,
                segment_rank=segment_rank,
                raw_positive=positive_sum,
                raw_negative=negative_penalty,
            )
            candidates.append(candidate)

        candidates.sort(
            key=lambda c: (
                -c.score,
                c.budget_diff,
                c.urgency_diff,
                c.segment_rank,
                c.product.id,
            )
        )

        return candidates[:limit]

    def _map_user_answers(self, answers: List) -> Dict[str, Dict[str, Any]]:
        """Convert survey answers to mapping keyed by question code."""
        return {}

    def _question_code_from_id(self, question_id: int) -> Optional[str]:
        for code, payload in self._catalog.items():
            if payload["question_id"] == question_id:
                return code
        return None

    @staticmethod
    def _max_positive_weight(criteria: Iterable[ProductCriteria]) -> float:
        return float(sum(max(0, c.weight or 0) for c in criteria))

    def _build_match_info(self, criterion: ProductCriteria, user_answer: Dict[str, Any]) -> Dict[str, Any]:
        note = criterion.note
        return {
            "question_id": user_answer["question_id"],
            "answer_id": user_answer["answer_id"],
            "question": user_answer["question_text"],
            "answer": user_answer["answer_text"],
            "weight": criterion.weight or 0,
            "note": note,
        }

    def _extract_budget_level(self, answers: List) -> Optional[int]:
        return None

    def _extract_urgency_level(self, answers: List) -> Optional[int]:
        return None

    def _price_to_usd(self, product: Product) -> float:
        value = float(product.price or Decimal("0"))
        currency = (product.currency or "RUB").upper()
        if currency == "RUB":
            return value / 90.0
        if currency == "EUR":
            return value * 1.08
        if currency in {"USD", "USDT"}:
            return value
        return value

    def _budget_diff(self, product: Product, user_budget_level: Optional[int]) -> float:
        if user_budget_level is None:
            return 10.0
        product_level = self._product_budget_level(product)
        return abs(product_level - user_budget_level)

    def _product_budget_level(self, product: Product) -> int:
        price_usd = self._price_to_usd(product)
        if price_usd <= 50:
            return 1
        if price_usd <= 200:
            return 2
        if price_usd <= 500:
            return 3
        if price_usd <= 1000:
            return 4
        return 5

    def _urgency_diff(self, product: Product, user_level: Optional[int]) -> float:
        if user_level is None:
            return 10.0
        product_level = self._product_urgency_level(product)
        if product_level is None:
            return 10.0
        return abs(product_level - user_level)

    def _product_urgency_level(self, product: Product) -> Optional[float]:
        urgencies = [
            (crit.answer_id, crit.weight)
            for crit in product.criteria
            if crit.question_id == 3 and (crit.weight or 0) > 0 and crit.answer_id is not None
        ]
        if not urgencies:
            return None
        total_weight = sum(weight for _, weight in urgencies)
        if not total_weight:
            return None
        weighted_sum = sum(answer_id * weight for answer_id, weight in urgencies)
        return weighted_sum / total_weight

    def _segment_rank(self, product: Product, user_segment: Optional[str]) -> int:
        if not user_segment:
            return 1
        target_segments = []
        if product.meta:
            segments = product.meta.get("target_segments") or product.meta.get("segments")
            if isinstance(segments, list):
                target_segments = [str(seg).lower() for seg in segments]
        if not target_segments:
            inferred = self._infer_segments_by_price(product)
            target_segments = inferred

        segment = user_segment.lower()
        return 0 if segment in target_segments else 1

    def _infer_segments_by_price(self, product: Product) -> List[str]:
        level = self._product_budget_level(product)
        if level <= 2:
            return ["cold"]
        if level == 3:
            return ["warm"]
        return ["hot"]

    def _build_explanation(self, candidate: Optional[MatchCandidate], answers: List) -> str:
        if not candidate:
            return "Совпадений с продуктами не найдено."

        lines: List[str] = []
        if candidate.positive_matches:
            positives = ", ".join(match["answer"] for match in candidate.positive_matches)
            lines.append(f"Совпали ответы: {positives}")
        if candidate.negative_matches:
            negatives = ", ".join(match["answer"] for match in candidate.negative_matches)
            lines.append(f"Анти-сигналы: {negatives}")
        if not lines:
            lines.append("Прямых совпадений не найдено.")
        return "; ".join(lines)

    async def _log_result(self, user: User, result: MatchResult, trigger: str) -> None:
        repo = ProductMatchLogRepository(self.session)
        top3_payload = []
        for candidate in result.candidates:
            top3_payload.append(
                {
                    "product_id": candidate.product.id,
                    "name": candidate.product.name,
                    "score": round(candidate.score, 4),
                    "positive": [
                        {
                            "question_id": match["question_id"],
                            "answer_id": match["answer_id"],
                            "weight": match["weight"],
                        }
                        for match in candidate.positive_matches
                    ],
                    "negative": [
                        {
                            "question_id": match["question_id"],
                            "answer_id": match["answer_id"],
                            "weight": match["weight"],
                        }
                        for match in candidate.negative_matches
                    ],
                    "budget_diff": candidate.budget_diff,
                    "urgency_diff": candidate.urgency_diff,
                    "segment_rank": candidate.segment_rank,
                }
            )

        await repo.log_match(
            user_id=user.id,
            product_id=result.best_product.id if result.best_product else None,
            score=result.score,
            top3={"items": top3_payload},
            explanation=result.explanation,
            threshold=result.threshold,
            trigger=trigger,
        )
