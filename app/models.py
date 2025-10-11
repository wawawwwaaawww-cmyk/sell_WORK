"""Database models for the Telegram Sales Bot."""

from datetime import datetime, date, time
from decimal import Decimal
from enum import Enum
from uuid import uuid4
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Integer, String, Text, Boolean, DateTime, Date, Time, 
    Numeric, JSON, SmallInteger, ARRAY, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.sql import func

from app.db import Base


class UserSegment(str, Enum):
    """User segmentation enum."""
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"


class FunnelStage(str, Enum):
    """User funnel stage enum."""
    NEW = "new"
    WELCOMED = "welcomed"
    SURVEYED = "surveyed"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    CONSULTATION = "consultation"
    PAYMENT = "payment"
    PAID = "paid"
    INACTIVE = "inactive"


class MessageRole(str, Enum):
    """Message role enum."""
    USER = "user"
    BOT = "bot"
    MANAGER = "manager"


class LeadStatus(str, Enum):
    """Lead status enum."""
    NEW = "new"
    TAKEN = "taken"
    DONE = "done"
    PAID = "paid"
    CANCELED = "canceled"


class AppointmentStatus(str, Enum):
    """Appointment status enum."""
    SCHEDULED = "scheduled"
    RESCHEDULED = "rescheduled"
    CANCELED = "canceled"
    COMPLETED = "completed"


class PaymentStatus(str, Enum):
    """Payment status enum."""
    CREATED = "created"
    SENT = "sent"
    PAID = "paid"
    FAILED = "failed"
    CANCELED = "canceled"


class MaterialType(str, Enum):
    """Material type enum."""
    CASE = "case"
    REVIEW = "review"
    ARTICLE = "article"
    ARGUMENT = "argument"
    FAQ = "faq"
    BONUS = "bonus"
    OTHER = "other"


class ABTestStatus(str, Enum):
    """A/B test status enum."""
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"


class ABTestMetric(str, Enum):
    """A/B test metric enum."""
    CTR = "CTR"
    CR = "CR"


class AdminRole(str, Enum):
    """Admin role enum."""
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    MANAGER = "manager"


class User(Base):
    """User model."""
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    segment: Mapped[Optional[UserSegment]] = mapped_column(String(10))
    lead_score: Mapped[int] = mapped_column(default=0)
    funnel_stage: Mapped[FunnelStage] = mapped_column(String(20), default=FunnelStage.NEW)
    source: Mapped[Optional[str]] = mapped_column(String(100))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    events: Mapped[List["Event"]] = relationship("Event", back_populates="user")
    survey_answers: Mapped[List["SurveyAnswer"]] = relationship("SurveyAnswer", back_populates="user")
    messages: Mapped[List["Message"]] = relationship("Message", back_populates="user")
    leads: Mapped[List["Lead"]] = relationship("Lead", back_populates="user")
    appointments: Mapped[List["Appointment"]] = relationship("Appointment", back_populates="user")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="user")
    
    __table_args__ = (
        Index("ix_users_telegram_id", "telegram_id"),
        Index("ix_users_segment", "segment"),
        Index("ix_users_created_at", "created_at"),
    )


class Event(Base):
    """Event model for tracking user actions."""
    __tablename__ = "events"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="events")
    
    __table_args__ = (
        Index("ix_events_user_created", "user_id", "created_at"),
        Index("ix_events_type_created", "type", "created_at"),
    )


class SurveyAnswer(Base):
    """Survey answer model."""
    __tablename__ = "survey_answers"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    question_code: Mapped[str] = mapped_column(String(50), nullable=False)
    answer_code: Mapped[str] = mapped_column(String(50), nullable=False)
    points: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="survey_answers")


class Message(Base):
    """Message model for conversation history."""
    __tablename__ = "messages"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="messages")


class Lead(Base):
    """Lead model."""
    __tablename__ = "leads"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    status: Mapped[LeadStatus] = mapped_column(String(20), default=LeadStatus.NEW)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    assigned_manager_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    handoff_trigger: Mapped[Optional[str]] = mapped_column(String(100))
    handoff_channel: Mapped[str] = mapped_column(String(50), default='bot')
    priority: Mapped[int] = mapped_column(SmallInteger, default=40)
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[Optional[str]] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="leads")
    notes: Mapped[List["LeadNote"]] = relationship("LeadNote", back_populates="lead", cascade="all, delete-orphan")


class LeadNote(Base):
    """Note attached to a lead by a manager or system."""
    __tablename__ = "lead_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    channel: Mapped[Optional[str]] = mapped_column(String(50))
    note_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped["Lead"] = relationship("Lead", back_populates="notes")


class Appointment(Base):
    """Appointment model."""
    __tablename__ = "appointments"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    slot: Mapped[time] = mapped_column(Time, nullable=False)
    tz: Mapped[str] = mapped_column(String(50), default="Europe/Moscow")
    status: Mapped[AppointmentStatus] = mapped_column(String(20), default=AppointmentStatus.SCHEDULED)
    reminder_job_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="appointments")


class Product(Base):
    """Product model."""
    __tablename__ = "products"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    meta: Mapped[Optional[dict]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    payment_landing_url: Mapped[Optional[str]] = mapped_column(String(500))
    
    # Relationships
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="product")


class Payment(Base):
    """Payment model."""
    __tablename__ = "payments"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), nullable=False)
    order_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(String(20), default=PaymentStatus.CREATED)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    payment_type: Mapped[str] = mapped_column(String(20), default="full")
    tariff_code: Mapped[Optional[str]] = mapped_column(String(40))
    landing_url: Mapped[Optional[str]] = mapped_column(String(500))
    discount_type: Mapped[Optional[str]] = mapped_column(String(20))
    discount_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    manual_link: Mapped[bool] = mapped_column(Boolean, default=False)
    conditions_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="payments")
    product: Mapped["Product"] = relationship("Product", back_populates="payments")


class MaterialContentType(str, Enum):
    """Physical content representation for a material."""
    PDF = "pdf"
    LINK = "link"
    TEXT = "text"
    BONUS = "bonus"
    VIDEO = "video"


class MaterialStatus(str, Enum):
    """Publication status of the material."""
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class MaterialSource(Base):
    """External or manual source of marketing materials."""
    __tablename__ = "material_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    materials: Mapped[List["Material"]] = relationship("Material", back_populates="source")


class Material(Base):
    """Aggregated marketing material with versioning support."""
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("material_sources.id"))
    external_id: Mapped[Optional[str]] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    content_type: Mapped[MaterialContentType] = mapped_column(String(20), default=MaterialContentType.TEXT)
    category: Mapped[Optional[MaterialType]] = mapped_column(String(30))
    status: Mapped[MaterialStatus] = mapped_column(String(20), default=MaterialStatus.DRAFT)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    source: Mapped[Optional[MaterialSource]] = relationship("MaterialSource", back_populates="materials")
    versions: Mapped[List["MaterialVersion"]] = relationship(
        "MaterialVersion",
        back_populates="material",
        cascade="all, delete-orphan",
        order_by="MaterialVersion.version.desc()",
    )
    tags_rel: Mapped[List["MaterialTag"]] = relationship(
        "MaterialTag",
        back_populates="material",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    segments_rel: Mapped[List["MaterialSegment"]] = relationship(
        "MaterialSegment",
        back_populates="material",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    stages_rel: Mapped[List["MaterialStage"]] = relationship(
        "MaterialStage",
        back_populates="material",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    metrics: Mapped[List["MaterialMetric"]] = relationship(
        "MaterialMetric",
        back_populates="material",
        cascade="all, delete-orphan",
    )

    tags = association_proxy("tags_rel", "tag")
    segments = association_proxy("segments_rel", "segment")
    stages = association_proxy("stages_rel", "stage")

    @property
    def active_version(self) -> Optional["MaterialVersion"]:
        """Return the currently active version for the material."""
        for version in self.versions:
            if version.is_active:
                return version
        return None


class MaterialVersion(Base):
    """Versioned payload for a marketing material."""
    __tablename__ = "material_versions"
    __table_args__ = (
        UniqueConstraint("material_id", "version", name="uq_material_version"),
        Index("ix_material_versions_active", "material_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    material_id: Mapped[str] = mapped_column(String(36), ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(128))
    extracted_text: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column('metadata', JSON, default=dict)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    external_url: Mapped[Optional[str]] = mapped_column(String(500))

    material: Mapped["Material"] = relationship("Material", back_populates="versions")
    assets: Mapped[List["MaterialAsset"]] = relationship(
        "MaterialAsset",
        back_populates="version",
        cascade="all, delete-orphan",
    )

    @property
    def primary_asset_url(self) -> Optional[str]:
        for asset in self.assets:
            if asset.storage_url:
                return asset.storage_url
        return self.external_url


class MaterialAsset(Base):
    """Binary asset linked to material version (PDF, video, etc.)."""
    __tablename__ = "material_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    material_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("material_versions.id", ondelete="CASCADE"), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_url: Mapped[Optional[str]] = mapped_column(String(1000))
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    checksum: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    version: Mapped["MaterialVersion"] = relationship("MaterialVersion", back_populates="assets")


class MaterialTag(Base):
    """Association of materials with semantic tags."""
    __tablename__ = "material_tags"

    material_id: Mapped[str] = mapped_column(String(36), ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True)
    tag: Mapped[str] = mapped_column(String(100), primary_key=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)

    material: Mapped["Material"] = relationship("Material", back_populates="tags_rel")


class MaterialSegment(Base):
    """Association of materials with user segments."""
    __tablename__ = "material_segments"

    material_id: Mapped[str] = mapped_column(String(36), ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True)
    segment: Mapped[str] = mapped_column(String(20), primary_key=True)

    material: Mapped["Material"] = relationship("Material", back_populates="segments_rel")


class MaterialStage(Base):
    """Association of materials with funnel stages."""
    __tablename__ = "material_stages"

    material_id: Mapped[str] = mapped_column(String(36), ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(50), primary_key=True)

    material: Mapped["Material"] = relationship("Material", back_populates="stages_rel")


class MaterialMetric(Base):
    """Daily aggregated metrics for material performance."""
    __tablename__ = "material_metrics"
    __table_args__ = (
        Index("ix_material_metrics_material_date", "material_id", "metric_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    material_id: Mapped[str] = mapped_column(String(36), ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    completions: Mapped[int] = mapped_column(Integer, default=0)
    segment: Mapped[Optional[str]] = mapped_column(String(20))
    funnel_stage: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    material: Mapped["Material"] = relationship("Material", back_populates="metrics")



class Broadcast(Base):
    """Broadcast model."""
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    buttons: Mapped[Optional[dict]] = mapped_column(JSON)
    segment_filter: Mapped[Optional[dict]] = mapped_column(JSON)
    content: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ABTest(Base):
    """A/B test model."""
    __tablename__ = "ab_tests"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    population: Mapped[int] = mapped_column(nullable=False)  # Percentage of users
    metric: Mapped[ABTestMetric] = mapped_column(String(10), nullable=False)
    status: Mapped[ABTestStatus] = mapped_column(String(20), default=ABTestStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    variants: Mapped[List["ABVariant"]] = relationship("ABVariant", back_populates="ab_test")
    results: Mapped[List["ABResult"]] = relationship("ABResult", back_populates="ab_test")


class ABVariant(Base):
    """A/B test variant model."""
    __tablename__ = "ab_variants"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ab_test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id"), nullable=False)
    variant_code: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    buttons: Mapped[Optional[dict]] = mapped_column(JSON)
    weight: Mapped[int] = mapped_column(default=50)  # Percentage weight
    
    # Relationships
    ab_test: Mapped["ABTest"] = relationship("ABTest", back_populates="variants")


class ABResult(Base):
    """A/B test result model."""
    __tablename__ = "ab_results"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ab_test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id"), nullable=False)
    variant_code: Mapped[str] = mapped_column(String(10), nullable=False)
    delivered: Mapped[int] = mapped_column(default=0)
    clicks: Mapped[int] = mapped_column(default=0)
    conversions: Mapped[int] = mapped_column(default=0)
    responses: Mapped[int] = mapped_column(default=0)
    unsub: Mapped[int] = mapped_column(default=0)
    
    # Relationships
    ab_test: Mapped["ABTest"] = relationship("ABTest", back_populates="results")


class Admin(Base):
    """Admin model."""
    __tablename__ = "admins"
    
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[AdminRole] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Funnel(Base):
    """Funnel configuration model."""
    __tablename__ = "funnels"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(50), default="v1")
    
    # Relationships
    user_states: Mapped[List["UserFunnelState"]] = relationship("UserFunnelState", back_populates="funnel")


class UserFunnelState(Base):
    """User funnel state model."""
    __tablename__ = "user_funnel_state"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    funnel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("funnels.id"), nullable=False)
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    context: Mapped[Optional[dict]] = mapped_column(JSON)
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    managed_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    managed_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    funnel: Mapped["Funnel"] = relationship("Funnel", back_populates="user_states")
    
    __table_args__ = (
        UniqueConstraint("user_id", "funnel_id", name="uq_user_funnel"),
    )

class BroadcastDelivery(Base):
    """Broadcast delivery tracking model."""
    __tablename__ = "broadcast_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    broadcast_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("broadcasts.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # sent, failed, pending
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_user_delivery"),
        Index("ix_broadcast_deliveries_status", "status"),
        Index("ix_broadcast_deliveries_broadcast_id", "broadcast_id"),
    )
