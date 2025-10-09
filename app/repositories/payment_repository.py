"""Payment repository for managing payment transactions."""

from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, PaymentStatus, User, Product


class PaymentRepository:
    """Repository for payment database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_payment(
        self,
        user_id: int,
        product_id: int,
        order_id: str,
        amount: Decimal,
        payload: Optional[Dict[str, Any]] = None
    ) -> Payment:
        """Create a new payment record."""
        payment = Payment(
            user_id=user_id,
            product_id=product_id,
            order_id=order_id,
            amount=amount,
            status=PaymentStatus.CREATED,
            payload=payload or {}
        )
        
        self.session.add(payment)
        await self.session.flush()
        await self.session.refresh(payment)
        
        self.logger.info(
            "Payment created",
            payment_id=payment.id,
            user_id=user_id,
            product_id=product_id,
            order_id=order_id,
            amount=amount
        )
        
        return payment
    
    async def get_by_id(self, payment_id: int) -> Optional[Payment]:
        """Get payment by ID."""
        stmt = select(Payment).where(Payment.id == payment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_order_id(self, order_id: str) -> Optional[Payment]:
        """Get payment by order ID."""
        stmt = select(Payment).where(Payment.order_id == order_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_user_payments(
        self,
        user_id: int,
        status: Optional[PaymentStatus] = None,
        limit: int = 20
    ) -> List[Payment]:
        """Get payments for a specific user."""
        stmt = select(Payment).where(Payment.user_id == user_id)
        
        if status:
            stmt = stmt.where(Payment.status == status)
        
        stmt = stmt.order_by(Payment.created_at.desc()).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_payment_status(
        self,
        payment_id: int,
        new_status: PaymentStatus,
        payload_update: Optional[Dict[str, Any]] = None
    ) -> Optional[Payment]:
        """Update payment status and payload."""
        payment = await self.get_by_id(payment_id)
        if not payment:
            return None
        
        old_status = payment.status
        payment.status = new_status
        
        if payload_update:
            if payment.payload:
                payment.payload.update(payload_update)
            else:
                payment.payload = payload_update
        
        await self.session.flush()
        
        self.logger.info(
            "Payment status updated",
            payment_id=payment_id,
            old_status=old_status,
            new_status=new_status
        )
        
        return payment
    
    async def mark_as_sent(self, payment_id: int) -> bool:
        """Mark payment as sent to user."""
        return await self.update_payment_status(payment_id, PaymentStatus.SENT) is not None
    
    async def mark_as_paid(
        self,
        order_id: str,
        payment_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Payment]:
        """Mark payment as completed by order ID."""
        payment = await self.get_by_order_id(order_id)
        if not payment:
            return None
        
        return await self.update_payment_status(
            payment.id,
            PaymentStatus.PAID,
            payment_data
        )
    
    async def mark_as_failed(
        self,
        payment_id: int,
        failure_reason: Optional[str] = None
    ) -> bool:
        """Mark payment as failed."""
        payload_update = {}
        if failure_reason:
            payload_update["failure_reason"] = failure_reason
            payload_update["failed_at"] = datetime.utcnow().isoformat()
        
        return await self.update_payment_status(
            payment_id,
            PaymentStatus.FAILED,
            payload_update
        ) is not None
    
    async def get_pending_payments(
        self,
        older_than_minutes: int = 30
    ) -> List[Payment]:
        """Get payments that are pending for too long."""
        cutoff_time = datetime.utcnow() - timedelta(minutes=older_than_minutes)
        
        stmt = select(Payment).where(
            and_(
                Payment.status.in_([PaymentStatus.CREATED, PaymentStatus.SENT]),
                Payment.created_at < cutoff_time
            )
        ).order_by(Payment.created_at)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_recent_successful_payments(
        self,
        hours: int = 24,
        limit: int = 50
    ) -> List[Payment]:
        """Get recent successful payments."""
        since_time = datetime.utcnow() - timedelta(hours=hours)
        
        stmt = select(Payment).where(
            and_(
                Payment.status == PaymentStatus.PAID,
                Payment.updated_at >= since_time
            )
        ).order_by(Payment.updated_at.desc()).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_payment_statistics(self, days: int = 30) -> Dict[str, Any]:
        """Get payment statistics for the last N days."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Total payments and amounts
        total_stmt = select(
            func.count(Payment.id),
            func.sum(Payment.amount)
        ).where(Payment.created_at >= start_date)
        
        total_result = await self.session.execute(total_stmt)
        total_count, total_amount = total_result.fetchone()
        
        # Successful payments and amounts
        success_stmt = select(
            func.count(Payment.id),
            func.sum(Payment.amount)
        ).where(
            and_(
                Payment.created_at >= start_date,
                Payment.status == PaymentStatus.PAID
            )
        )
        
        success_result = await self.session.execute(success_stmt)
        success_count, success_amount = success_result.fetchone()
        
        # Payments by status
        status_stmt = select(
            Payment.status,
            func.count(Payment.id),
            func.sum(Payment.amount)
        ).where(
            Payment.created_at >= start_date
        ).group_by(Payment.status)
        
        status_result = await self.session.execute(status_stmt)
        status_stats = {
            row[0]: {"count": row[1], "amount": float(row[2] or 0)}
            for row in status_result.fetchall()
        }
        
        # Average payment amount
        avg_amount = float(total_amount or 0) / max(total_count or 1, 1)
        
        # Conversion rate
        conversion_rate = (success_count or 0) / max(total_count or 1, 1) * 100
        
        return {
            "period_days": days,
            "total_payments": total_count or 0,
            "total_amount": float(total_amount or 0),
            "successful_payments": success_count or 0,
            "successful_amount": float(success_amount or 0),
            "average_payment": avg_amount,
            "conversion_rate": conversion_rate,
            "by_status": status_stats
        }
    
    async def get_top_products_by_revenue(
        self,
        days: int = 30,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top products by revenue in the last N days."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        stmt = select(
            Product.name,
            Product.code,
            func.count(Payment.id).label('payment_count'),
            func.sum(Payment.amount).label('total_revenue')
        ).join(
            Product, Payment.product_id == Product.id
        ).where(
            and_(
                Payment.created_at >= start_date,
                Payment.status == PaymentStatus.PAID
            )
        ).group_by(
            Product.id, Product.name, Product.code
        ).order_by(
            func.sum(Payment.amount).desc()
        ).limit(limit)
        
        result = await self.session.execute(stmt)
        
        return [
            {
                "product_name": row.name,
                "product_code": row.code,
                "payment_count": row.payment_count,
                "total_revenue": float(row.total_revenue)
            }
            for row in result.fetchall()
        ]
    
    async def process_webhook_payment(
        self,
        order_id: str,
        webhook_data: Dict[str, Any]
    ) -> Optional[Payment]:
        """Process payment webhook from external payment provider."""
        
        payment = await self.get_by_order_id(order_id)
        if not payment:
            self.logger.warning(
                "Payment not found for webhook",
                order_id=order_id,
                webhook_data=webhook_data
            )
            return None
        
        # Extract status from webhook data
        webhook_status = webhook_data.get("status", "").lower()
        
        if webhook_status in ["paid", "success", "completed"]:
            new_status = PaymentStatus.PAID
        elif webhook_status in ["failed", "error", "declined"]:
            new_status = PaymentStatus.FAILED
        elif webhook_status in ["cancelled", "canceled"]:
            new_status = PaymentStatus.CANCELED
        else:
            self.logger.warning(
                "Unknown webhook status",
                order_id=order_id,
                status=webhook_status
            )
            return payment
        
        # Update payment with webhook data
        updated_payment = await self.update_payment_status(
            payment.id,
            new_status,
            {
                "webhook_data": webhook_data,
                "webhook_processed_at": datetime.utcnow().isoformat(),
                "payment_method": webhook_data.get("payment_method"),
                "transaction_id": webhook_data.get("transaction_id")
            }
        )
        
        self.logger.info(
            "Webhook payment processed",
            order_id=order_id,
            payment_id=payment.id,
            new_status=new_status
        )
        
        return updated_payment