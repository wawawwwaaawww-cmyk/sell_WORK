"""Payment service for handling payment processing and related operations."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any
from enum import Enum

import structlog
from sqlalchemy import select, and_, cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import JSONB

from app.models import User, Product, Payment, PaymentStatus


logger = structlog.get_logger()


class ProductRepository:
    """Repository for product database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_by_id(self, product_id: int) -> Optional[Product]:
        """Get product by ID."""
        stmt = select(Product).where(Product.id == product_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_active_products(self) -> List[Product]:
        """Get all active products."""
        stmt = select(Product).where(Product.is_active == True)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_products_for_segment(self, segment: str) -> List[Product]:
        """Get products suitable for user segment."""
        meta_jsonb = cast(Product.meta, JSONB)

        stmt = select(Product).where(
            and_(
                Product.is_active.is_(True),
                Product.meta.isnot(None),
                meta_jsonb["segments"].contains([segment])
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class PaymentRepository:
    """Repository for payment database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_payment(
        self,
        user_id: int,
        product_id: int,
        amount: Decimal,
        order_id: Optional[str] = None,
        *,
        status: PaymentStatus = PaymentStatus.CREATED,
        payment_type: str = "full",
        manual_link: bool = False,
        tariff_code: Optional[str] = None,
        landing_url: Optional[str] = None,
        discount_type: Optional[str] = None,
        discount_value: Optional[Decimal] = None,
        conditions_note: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Payment:
        """Create a new payment record."""
        if not order_id:
            order_id = str(uuid.uuid4())

        payment = Payment(
            user_id=user_id,
            product_id=product_id,
            order_id=order_id,
            amount=amount,
            status=status,
            payment_type=payment_type,
            manual_link=manual_link,
            tariff_code=tariff_code,
            landing_url=landing_url,
            discount_type=discount_type,
            discount_value=discount_value,
            conditions_note=conditions_note,
            payload=payload or {},
        )
        self.session.add(payment)
        await self.session.flush()
        await self.session.refresh(payment)
        
        self.logger.info(
            "Payment created",
            payment_id=payment.id,
            user_id=user_id,
            amount=float(amount),
            order_id=order_id
        )
        
        return payment
    
    async def get_by_order_id(self, order_id: str) -> Optional[Payment]:
        """Get payment by order ID."""
        stmt = select(Payment).where(Payment.order_id == order_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_user_payments(self, user_id: int) -> List[Payment]:
        """Get all payments for a user."""
        stmt = select(Payment).where(
            Payment.user_id == user_id
        ).order_by(Payment.created_at.desc())
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_payment_status(
        self,
        payment: Payment,
        status: PaymentStatus,
        payload: Optional[Dict[str, Any]] = None
    ) -> Payment:
        """Update payment status."""
        payment.status = status
        if payload:
            merged_payload = dict(payment.payload or {})
            merged_payload.update(payload)
            payment.payload = merged_payload

        await self.session.flush()
        await self.session.refresh(payment)
        
        self.logger.info(
            "Payment status updated",
            payment_id=payment.id,
            order_id=payment.order_id,
            status=status
        )
        
        return payment


class PaymentService:
    """Service for payment processing logic."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.product_repo = ProductRepository(session)
        self.payment_repo = PaymentRepository(session)
        self.logger = structlog.get_logger()
    
    async def get_suitable_products(self, user: User) -> List[Product]:
        """Get products suitable for user based on segment and profile."""
        
        # Try to get segment-specific products first
        if user.segment:
            products = await self.product_repo.get_products_for_segment(user.segment)
            if products:
                return products
        
        # Fallback to all active products
        return await self.product_repo.get_active_products()
    
    async def check_payment_eligibility(
        self,
        user: User,
        product: Product,
        payment_type: str = "full",
    ) -> tuple[bool, str]:
        """Ensure тариф готов к выдаче ссылки."""

        if not product.is_active:
            return False, "Продукт временно недоступен"

        payment_link = self._resolve_payment_link(product, payment_type)
        if not payment_link:
            return False, "Для этого тарифа пока не настроена ссылка на оплату"

        return True, "Готовы выдать ссылку"

    def _resolve_payment_link(self, product: Product, payment_type: str) -> Optional[str]:
        """Return configured landing URL for нужный тип оплаты."""

        meta_links = {}
        if isinstance(product.meta, dict):
            meta_links = product.meta.get("payment_links") or {}

        if isinstance(meta_links, dict):
            link = meta_links.get(payment_type)
            if link:
                return link

        if payment_type == "full":
            return product.payment_landing_url

        return None

    def get_payment_offer_text(
        self,
        user: User,
        product: Product,
        payment_link: str,
        *,
        payment_type: str = "full",
        custom_amount: Optional[Decimal] = None,
    ) -> str:
        """Render текст предложения оплаты."""

        amount_value = float(custom_amount or product.price)
        type_text = "Полная оплата" if payment_type == "full" else "Рассрочка"

        return (
            f"💳 **Оплата программы \"{product.name}\"**\n\n"
            f"Тип: {type_text}\n"
            f"Сумма к оплате: {amount_value:,.0f} ₽\n\n"
            f"Перейдите по ссылке ниже, чтобы завершить оформление:\n{payment_link}"
        )

    async def create_payment_link(
        self,
        user_id: int,
        product_id: int,
        *,
        payment_type: str = "full",
        custom_amount: Optional[Decimal] = None,
        manual_link: bool = False,
        discount_type: Optional[str] = None,
        discount_value: Optional[Decimal] = None,
        conditions_note: Optional[str] = None,
    ) -> tuple[bool, Optional[str], str]:
        """Create payment record and resolve landing link when available."""
        try:
            product = await self.product_repo.get_by_id(product_id)
            if not product:
                return False, None, "Продукт не найден"

            amount = custom_amount or product.price
            payment_link = None

            if not manual_link:
                payment_link = self._resolve_payment_link(product, payment_type)
                if not payment_link:
                    return False, None, "Для тарифа не настроена ссылка на оплату"

            payload: Dict[str, Any] = {}
            if payment_link:
                payload["payment_link"] = payment_link
            payload["payment_type"] = payment_type
            if manual_link:
                payload["manual_link"] = True
            if discount_type:
                payload["discount_type"] = discount_type
            if discount_value is not None:
                payload["discount_value"] = float(discount_value)
            if conditions_note:
                payload["conditions_note"] = conditions_note

            status = PaymentStatus.SENT if payment_link else PaymentStatus.CREATED

            payment = await self.payment_repo.create_payment(
                user_id=user_id,
                product_id=product_id,
                amount=amount,
                status=status,
                payment_type=payment_type,
                manual_link=manual_link,
                tariff_code=product.code,
                landing_url=payment_link,
                discount_type=discount_type,
                discount_value=discount_value,
                conditions_note=conditions_note,
                payload=payload or None,
            )

            if payment_link:
                message = "Ссылка на оплату готова"
            else:
                message = "Менеджер свяжется для выдачи ссылки"

            return True, payment_link, message

        except Exception as e:
            self.logger.error("Error creating payment link", error=str(e), user_id=user_id)
            return False, None, "Ошибка при создании записи оплаты"
    
    async def process_payment_webhook(
        self,
        order_id: str,
        status: str,
        webhook_data: Dict[str, Any]
    ) -> tuple[bool, Optional[Payment], str]:
        """Process payment webhook from external payment provider."""
        try:
            # Get payment by order ID
            payment = await self.payment_repo.get_by_order_id(order_id)
            if not payment:
                return False, None, "Payment not found"
            
            # Map webhook status to our payment status
            status_mapping = {
                "paid": PaymentStatus.PAID,
                "failed": PaymentStatus.FAILED,
                "canceled": PaymentStatus.CANCELED,
                "pending": PaymentStatus.SENT
            }
            
            payment_status = status_mapping.get(status.lower(), PaymentStatus.FAILED)
            
            # Update payment
            payment = await self.payment_repo.update_payment_status(
                payment,
                payment_status,
                webhook_data
            )
            
            return True, payment, "Payment updated successfully"
            
        except Exception as e:
            self.logger.error("Error processing payment webhook", error=str(e), order_id=order_id)
            return False, None, "Error processing webhook"
