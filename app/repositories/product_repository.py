"""Product repository for managing educational products and programs."""

from typing import List, Optional, Dict, Any, Iterable
from decimal import Decimal

import structlog
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Product, UserSegment, ProductCriteria


class ProductRepository:
    """Repository for product database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_product(
        self,
        code: str,
        name: str,
        price: Decimal,
        description: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        *,
        slug: Optional[str] = None,
        short_desc: Optional[str] = None,
        value_props: Optional[Iterable[str]] = None,
        currency: Optional[str] = None,
        landing_url: Optional[str] = None,
        payment_landing_url: Optional[str] = None,
        is_active: bool = True,
    ) -> Product:
        """Create a new product."""
        value_props_payload: list[str] | None = None
        if value_props is not None:
            value_props_payload = [str(item).strip() for item in value_props if str(item).strip()]
        else:
            value_props_payload = []

        product = Product(
            code=code,
            name=name,
            slug=slug,
            description=description,
            price=price,
            currency=currency or "RUB",
            meta=meta or {},
            is_active=is_active,
            short_desc=short_desc,
            value_props=value_props_payload,
            landing_url=landing_url,
            payment_landing_url=payment_landing_url or landing_url,
        )
        
        self.session.add(product)
        await self.session.flush()
        await self.session.refresh(product)
        
        self.logger.info(
            "Product created",
            product_id=product.id,
            code=code,
            name=name,
            price=price
        )
        
        return product
    
    async def load_product_with_criteria(self, product_id: int) -> Optional[Product]:
        """Fetch product with criteria eagerly loaded."""
        stmt = (
            select(Product)
            .options(selectinload(Product.criteria))
            .where(Product.id == product_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, product_id: int) -> Optional[Product]:
        """Get product by ID."""
        stmt = select(Product).where(Product.id == product_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_code(self, code: str) -> Optional[Product]:
        """Get product by code."""
        stmt = select(Product).where(Product.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_active_products(self, limit: int = 20) -> List[Product]:
        """Get all active products."""
        stmt = select(Product).where(
            Product.is_active == True
        ).order_by(Product.price).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_active_with_criteria(self) -> List[Product]:
        """Get all active products with criteria records."""
        stmt = (
            select(Product)
            .options(selectinload(Product.criteria))
            .where(Product.is_active == True)
            .order_by(Product.price)
        )
        result = await self.session.execute(stmt)
        return result.scalars().unique().all()
    
    async def get_products_by_segment(
        self,
        segment: UserSegment,
        limit: int = 10
    ) -> List[Product]:
        """Get products suitable for specific user segment."""
        
        # Define product selection strategy by segment
        if segment == UserSegment.COLD:
            # Basic and affordable products for beginners
            max_price = Decimal("50000")  # Up to 50k rubles
            target_codes = ["basic_course", "crypto_fundamentals", "safety_guide"]
        elif segment == UserSegment.WARM:
            # Intermediate products
            max_price = Decimal("150000")  # Up to 150k rubles
            target_codes = ["trading_course", "portfolio_management", "technical_analysis"]
        else:  # HOT segment
            # Premium products
            max_price = Decimal("500000")  # Up to 500k rubles
            target_codes = ["premium_course", "vip_mentorship", "crypto_elite"]
        
        stmt = select(Product).where(
            and_(
                Product.is_active == True,
                Product.price <= max_price,
                or_(*[Product.code.like(f"%{code}%") for code in target_codes])
            )
        ).order_by(Product.price).limit(limit)
        
        result = await self.session.execute(stmt)
        products = result.scalars().all()
        
        # If no segment-specific products found, get general products within price range
        if not products:
            general_stmt = select(Product).where(
                and_(
                    Product.is_active == True,
                    Product.price <= max_price
                )
            ).order_by(Product.price).limit(limit)
            
            general_result = await self.session.execute(general_stmt)
            products = general_result.scalars().all()
        
        return products
    
    async def get_products_by_price_range(
        self,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
        limit: int = 10
    ) -> List[Product]:
        """Get products within specified price range."""
        stmt = select(Product).where(Product.is_active == True)
        
        if min_price is not None:
            stmt = stmt.where(Product.price >= min_price)
        
        if max_price is not None:
            stmt = stmt.where(Product.price <= max_price)
        
        stmt = stmt.order_by(Product.price).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def search_products(
        self,
        query: str,
        limit: int = 10
    ) -> List[Product]:
        """Search products by name or description."""
        search_term = f"%{query.lower()}%"
        
        stmt = select(Product).where(
            and_(
                Product.is_active == True,
                or_(
                    Product.name.ilike(search_term),
                    Product.description.ilike(search_term),
                    Product.code.ilike(search_term)
                )
            )
        ).order_by(Product.price).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_product(
        self,
        product_id: int,
        **updates
    ) -> Optional[Product]:
        """Update product information."""
        product = await self.get_by_id(product_id)
        if not product:
            return None
        if "value_props" in updates and updates["value_props"] is not None:
            updates["value_props"] = [
                str(item).strip()
                for item in updates["value_props"]
                if str(item).strip()
            ]
        
        for field, value in updates.items():
            if hasattr(product, field):
                setattr(product, field, value)
        
        await self.session.flush()
        
        self.logger.info(
            "Product updated",
            product_id=product_id,
            updates=list(updates.keys())
        )
        
        return product
    
    async def deactivate_product(self, product_id: int) -> bool:
        """Deactivate a product."""
        product = await self.get_by_id(product_id)
        if not product:
            return False
        
        product.is_active = False
        await self.session.flush()
        
        self.logger.info("Product deactivated", product_id=product_id)
        return True
    
    async def get_recommended_products(
        self,
        user_segment: UserSegment,
        user_score: int,
        budget_range: Optional[tuple] = None,
        limit: int = 3
    ) -> List[Product]:
        """Get personalized product recommendations."""
        
        # Determine budget range if not provided
        if budget_range is None:
            if user_segment == UserSegment.COLD:
                budget_range = (Decimal("0"), Decimal("50000"))
            elif user_segment == UserSegment.WARM:
                budget_range = (Decimal("25000"), Decimal("150000"))
            else:  # HOT
                budget_range = (Decimal("50000"), Decimal("500000"))
        
        # Get products within budget
        products = await self.get_products_by_price_range(
            min_price=budget_range[0],
            max_price=budget_range[1],
            limit=limit * 2  # Get more to filter
        )
        
        # Score products based on user profile
        scored_products = []
        for product in products:
            score = self._calculate_product_score(product, user_segment, user_score)
            scored_products.append((product, score))
        
        # Sort by score and return top results
        scored_products.sort(key=lambda x: x[1], reverse=True)
        return [product for product, score in scored_products[:limit]]
    
    def _calculate_product_score(
        self,
        product: Product,
        user_segment: UserSegment,
        user_score: int
    ) -> float:
        """Calculate compatibility score between product and user."""
        score = 0.0
        
        # Base score from price appropriateness
        if user_segment == UserSegment.COLD and product.price <= 50000:
            score += 3.0
        elif user_segment == UserSegment.WARM and 25000 <= product.price <= 150000:
            score += 3.0
        elif user_segment == UserSegment.HOT and product.price >= 50000:
            score += 3.0
        
        # Score from product type matching user level
        product_code = product.code.lower()
        if user_segment == UserSegment.COLD:
            if any(keyword in product_code for keyword in ["basic", "fundamental", "beginner"]):
                score += 2.0
        elif user_segment == UserSegment.WARM:
            if any(keyword in product_code for keyword in ["trading", "intermediate", "portfolio"]):
                score += 2.0
        elif user_segment == UserSegment.HOT:
            if any(keyword in product_code for keyword in ["premium", "advanced", "vip", "elite"]):
                score += 2.0
        
        # Score from user engagement level
        if user_score > 12:
            score += 1.0
        elif user_score > 8:
            score += 0.5
        
        return score
    
    async def create_sample_products(self) -> None:
        """Create sample products for testing (to be removed in production)."""
        sample_products = [
            {
                "code": "crypto_basics_course",
                "name": "Основы криптовалют для новичков",
                "description": "Базовый курс по криптовалютам для начинающих инвесторов",
                "price": Decimal("29900"),
                "meta": {
                    "duration_weeks": 4,
                    "level": "beginner",
                    "includes": ["video_lessons", "workbook", "support"]
                }
            },
            {
                "code": "trading_masterclass",
                "name": "Мастер-класс по криптотрейдингу",
                "description": "Интенсивный курс по технической и фундаментальной аналитике",
                "price": Decimal("89900"),
                "meta": {
                    "duration_weeks": 8,
                    "level": "intermediate",
                    "includes": ["live_sessions", "trading_signals", "portfolio_review"]
                }
            },
            {
                "code": "crypto_elite_program",
                "name": "Crypto Elite Investor Program",
                "description": "Премиальная программа для серьезных инвесторов с капиталом от $50,000",
                "price": Decimal("299000"),
                "meta": {
                    "duration_weeks": 52,
                    "level": "advanced",
                    "includes": ["personal_mentor", "exclusive_deals", "networking_events"]
                }
            },
            {
                "code": "safety_guide",
                "name": "Гид по безопасности в криптовалютах",
                "description": "Полное руководство по защите криптоактивов",
                "price": Decimal("9900"),
                "meta": {
                    "format": "ebook",
                    "level": "all",
                    "includes": ["security_checklist", "wallet_setup", "backup_guide"]
                }
            },
            {
                "code": "vip_consultation",
                "name": "VIP консультация с экспертом",
                "description": "Персональная консультация по инвестиционной стратегии",
                "price": Decimal("25000"),
                "meta": {
                    "duration_hours": 2,
                    "format": "online",
                    "includes": ["strategy_plan", "risk_assessment", "follow_up"]
                }
            }
        ]
        
        for product_data in sample_products:
            existing = await self.get_by_code(product_data["code"])
            if not existing:
                await self.create_product(**product_data)
        
        self.logger.info("Sample products created", count=len(sample_products))
