"""Repository for managing product criteria."""

from __future__ import annotations

from typing import Iterable, Sequence, Dict, Any, List, Optional

import structlog
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProductCriteria


class ProductCriteriaRepository:
    """Data access for product matching criteria."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()

    async def get_for_product(self, product_id: int) -> list[ProductCriteria]:
        """Return criteria records for a product ordered by question/answer."""
        stmt = (
            select(ProductCriteria)
            .where(ProductCriteria.product_id == product_id)
            .order_by(ProductCriteria.question_id, ProductCriteria.answer_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_products(self, product_ids: Iterable[int]) -> dict[int, list[ProductCriteria]]:
        """Fetch criteria for multiple products grouped by product id."""
        ids = list(product_ids)
        if not ids:
            return {}

        stmt = (
            select(ProductCriteria)
            .where(ProductCriteria.product_id.in_(ids))
            .order_by(ProductCriteria.product_id, ProductCriteria.question_id, ProductCriteria.answer_id)
        )
        result = await self.session.execute(stmt)
        records: Dict[int, List[ProductCriteria]] = {}
        for crit in result.scalars():
            records.setdefault(crit.product_id, []).append(crit)
        return records

    async def replace_for_product(
        self,
        product_id: int,
        entries: Sequence[Dict[str, Any]],
    ) -> list[ProductCriteria]:
        """Replace criteria for a product with provided entries."""
        await self.session.execute(
            delete(ProductCriteria).where(ProductCriteria.product_id == product_id)
        )

        created: List[ProductCriteria] = []
        for entry in entries:
            criterion = ProductCriteria(
                product_id=product_id,
                question_id=int(entry["question_id"]),
                answer_id=int(entry["answer_id"]),
                weight=int(entry.get("weight", 1)),
                note=entry.get("note"),
                question_code=entry.get("question_code"),
                answer_code=entry.get("answer_code"),
            )
            self.session.add(criterion)
            created.append(criterion)

        await self.session.flush()

        self.logger.info(
            "Product criteria replaced",
            product_id=product_id,
            total=len(created),
        )

        return created

    async def delete_for_product(self, product_id: int) -> None:
        """Remove all criteria for a product."""
        await self.session.execute(
            delete(ProductCriteria).where(ProductCriteria.product_id == product_id)
        )
        self.logger.info("Product criteria cleared", product_id=product_id)

