"""Database models for the Telegram Sales Bot."""

from datetime import datetime, date, time
from decimal import Decimal
from enum import Enum
from uuid import uuid4
from typing import Optional, List

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger, Integer, String, Text, Boolean, DateTime, Date, Time,
    Numeric, JSON, SmallInteger, ForeignKey, UniqueConstraint, Index, Float, ARRAY
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

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
    PAID = "paid"
    INACTIVE = "inactive"


class MessageRole(str, Enum):
    """Message role enum."""
    USER = "user"
    BOT = "bot"
    MANAGER = "manager"


class LeadStatus(str, Enum):
    """Lead status enum."""
    DRAFT = "draft"
    INCOMPLETE = "incomplete"
    ASSIGNED = "assigned"
    SCHEDULED = "scheduled"
    NEW = "new"
    TAKEN = "taken"
    DONE = "done"
    PAID = "paid"
    CANCELED = "canceled"


class ProductMediaType(str, Enum):
    """Product media type enum."""
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"


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
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    OBSERVE = "OBSERVE"
    WINNER_PICKED = "WINNER_PICKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @classmethod
    def normalize(cls, value: str) -> "ABTestStatus":
        """Normalize stored string to enum member."""
        if not value:
            return cls.DRAFT
        
        upper_val = value.upper()
        if upper_val == "FINISHED":
            return cls.COMPLETED

        try:
            return cls(upper_val)
        except ValueError:
            # Fallback for old lowercase values
            if upper_val.lower() in ('draft', 'running', 'completed'):
                return cls(upper_val.upper())
            return cls.DRAFT


class ABTestMetric(str, Enum):
    """A/B test metric enum."""
    CTR = "CTR"
    CR = "CR"


class ABEventType(str, Enum):
    """Event types tracked for A/B testing."""
    DELIVERED = "delivered"
    CLICKED = "clicked"
    REPLIED = "replied"
    LEAD_CREATED = "lead_created"
    UNSUBSCRIBED = "unsubscribed"
    BLOCKED = "blocked"


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
    counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pos_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    neu_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    neg_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scored_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lead_level_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    lead_level_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Follow-up related fields
    last_user_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_followup_24_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_followup_72_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    mute_followups_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    followups_opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    events: Mapped[List["Event"]] = relationship("Event", back_populates="user")
    messages: Mapped[List["Message"]] = relationship("Message", back_populates="user")
    leads: Mapped[List["Lead"]] = relationship("Lead", back_populates="user")
    sentiment_scores: Mapped[List["UserMessageScore"]] = relationship(
        "UserMessageScore",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
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


class UserMessageScore(Base):
    """Audit record for user message sentiment classification."""
    __tablename__ = "user_message_scores"
    __table_args__ = (
        UniqueConstraint("hash", name="uq_user_message_scores_hash"),
        UniqueConstraint("user_id", "message_id", name="uq_user_message_scores_message"),
        Index("ix_user_message_scores_user_id", "user_id"),
        Index("ix_user_message_scores_evaluated_at", "evaluated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="gpt-4o-mini")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hash: Mapped[str] = mapped_column(String(128), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="sentiment_scores")


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
    incomplete_job_id: Mapped[Optional[str]] = mapped_column(String(255))
    assignee_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    sales_script_md: Mapped[Optional[str]] = mapped_column(Text)
    sales_script_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sales_script_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sales_script_model: Mapped[Optional[str]] = mapped_column(String(120))
    sales_script_inputs_hash: Mapped[Optional[str]] = mapped_column(String(128))
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="leads")
    notes: Mapped[List["LeadNote"]] = relationship("LeadNote", back_populates="lead", cascade="all, delete-orphan")
    events: Mapped[List["LeadEvent"]] = relationship("LeadEvent", back_populates="lead", cascade="all, delete-orphan")


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


class LeadEvent(Base):
    """Timeline event for a lead."""
    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="events")


class Product(Base):
    """Product model."""
    __tablename__ = "products"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    short_desc: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    meta: Mapped[Optional[dict]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    value_props: Mapped[Optional[List[str]]] = mapped_column(JSON, default=list)
    payment_landing_url: Mapped[Optional[str]] = mapped_column(String(500))
    landing_url: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    criteria: Mapped[List["ProductCriteria"]] = relationship(
        "ProductCriteria",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    match_logs: Mapped[List["ProductMatchLog"]] = relationship(
        "ProductMatchLog",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    media: Mapped[List["ProductMedia"]] = relationship(
        "ProductMedia",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ProductMedia(Base):
    """Product media model."""
    __tablename__ = "product_media"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[ProductMediaType] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    product: Mapped["Product"] = relationship("Product", back_populates="media")


class ProductCriteria(Base):
    """Mapping between products and survey answers."""
    __tablename__ = "product_criteria"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    question_code: Mapped[Optional[str]] = mapped_column(String(50))
    answer_code: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="criteria")

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "question_id",
            "answer_id",
            name="uq_product_criteria_unique_answer",
        ),
        Index(
            "ix_product_criteria_product_question",
            "product_id",
            "question_id",
        ),
    )


class ProductMatchLog(Base):
    """Audit log for fuzzy product matching."""
    __tablename__ = "product_match_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="SET NULL"))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    top3: Mapped[dict] = mapped_column(JSON, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    threshold_used: Mapped[Optional[float]] = mapped_column(Float)
    trigger: Mapped[Optional[str]] = mapped_column(String(50))

    product: Mapped[Optional["Product"]] = relationship("Product", back_populates="match_logs")
    user: Mapped["User"] = relationship("User")


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
    __table_args__ = (
        Index("ix_ab_tests_status", "status"),
        Index("ix_ab_tests_send_at", "send_at"),
        Index("ix_ab_tests_winner_variant_id", "winner_variant_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    sample_ratio: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, server_default=sa.text("'0.1'"))
    metric: Mapped[ABTestMetric] = mapped_column(String(10), nullable=False, server_default=sa.text("'CTR'"))
    observation_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("'24'"))
    segment_filter: Mapped[dict] = mapped_column(JSON, nullable=False, server_default=sa.text("'{}'::jsonb"))
    status: Mapped[ABTestStatus] = mapped_column(String(20), default=ABTestStatus.DRAFT, server_default=ABTestStatus.DRAFT.value, nullable=False)
    winner_variant_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    delivered_group_id: Mapped[Optional[uuid4]] = mapped_column(sa.UUID, nullable=True)
    send_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa.text("'0'"))

    # Old fields for compatibility
    population: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Percentage of users, now nullable
    creator_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    variants_count: Mapped[int] = mapped_column(SmallInteger, default=2)
    audience_size: Mapped[Optional[int]] = mapped_column(Integer)
    test_size: Mapped[Optional[int]] = mapped_column(Integer)
    notification_job_id: Mapped[Optional[str]] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    variants: Mapped[List["ABVariant"]] = relationship("ABVariant", back_populates="ab_test", foreign_keys="ABVariant.ab_test_id")
    results: Mapped[List["ABResult"]] = relationship("ABResult", back_populates="ab_test")
    assignments: Mapped[List["ABAssignment"]] = relationship("ABAssignment", back_populates="ab_test")
    events: Mapped[List["ABEvent"]] = relationship("ABEvent", back_populates="ab_test")

    @property
    def status_enum(self) -> ABTestStatus:
        """Return status as normalized enum value."""
        if isinstance(self.status, ABTestStatus):
            return self.status
        return ABTestStatus.normalize(str(self.status))


class ABVariant(Base):
    """A/B test variant model."""
    __tablename__ = "ab_variants"
    __table_args__ = (
        UniqueConstraint("ab_test_id", "variant_code", name="uq_ab_variant_code"),
    )
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ab_test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id"), nullable=False)
    variant_code: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    buttons: Mapped[dict] = mapped_column(JSON, nullable=False, server_default=sa.text("'[]'::jsonb"))
    media: Mapped[List[dict]] = mapped_column(JSON, nullable=False, server_default=sa.text("'[]'::jsonb"))
    parse_mode: Mapped[str] = mapped_column(String(10), nullable=False, server_default="HTML")
    content: Mapped[Optional[list]] = mapped_column(JSON)
    weight: Mapped[int] = mapped_column(default=50)  # Percentage weight
    order_index: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    ab_test: Mapped["ABTest"] = relationship("ABTest", back_populates="variants", foreign_keys=[ab_test_id])
    assignments: Mapped[List["ABAssignment"]] = relationship("ABAssignment", back_populates="variant")
    events: Mapped[List["ABEvent"]] = relationship("ABEvent", back_populates="variant")
    result_snapshot: Mapped[Optional["ABResult"]] = relationship("ABResult", back_populates="variant", uselist=False)


class ABAssignment(Base):
    """Assignment of user to specific A/B test variant."""
    __tablename__ = "ab_assignments"
    __table_args__ = (
        UniqueConstraint("test_id", "user_id", name="uq_ab_assignment_user"),
        Index("ix_ab_assignments_test", "test_id"),
        Index("ix_ab_assignments_variant", "variant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_variants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hash_value: Mapped[Optional[float]] = mapped_column(Numeric(precision=12, scale=6))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(10), server_default="PENDING", nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivery_error: Mapped[Optional[str]] = mapped_column(Text)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Relationships
    ab_test: Mapped["ABTest"] = relationship("ABTest", back_populates="assignments")
    variant: Mapped["ABVariant"] = relationship("ABVariant", back_populates="assignments")
    user: Mapped["User"] = relationship("User")
    events: Mapped[List["ABEvent"]] = relationship("ABEvent", back_populates="assignment", cascade="all, delete-orphan")


class ABEvent(Base):
    """Event captured for a specific A/B assignment."""
    __tablename__ = "ab_events"
    __table_args__ = (
        Index("ix_ab_events_test_type", "test_id", "event_type"),
        Index("ix_ab_events_assignment_type", "assignment_id", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_variants.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[int] = mapped_column(Integer, ForeignKey("ab_assignments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[ABEventType] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    ab_test: Mapped["ABTest"] = relationship("ABTest", back_populates="events")
    variant: Mapped["ABVariant"] = relationship("ABVariant", back_populates="events")
    assignment: Mapped["ABAssignment"] = relationship("ABAssignment", back_populates="events")
    user: Mapped["User"] = relationship("User")


class ABResult(Base):
    """A/B test result model."""
    __tablename__ = "ab_results"
    __table_args__ = (
        UniqueConstraint("ab_test_id", "variant_code", name="uq_ab_result_variant"),
    )
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ab_test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id"), nullable=False)
    variant_code: Mapped[str] = mapped_column(String(10), nullable=False)
    variant_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("ab_variants.id"))
    delivered: Mapped[int] = mapped_column(default=0)
    clicks: Mapped[int] = mapped_column(default=0)
    conversions: Mapped[int] = mapped_column(default=0)
    responses: Mapped[int] = mapped_column(default=0)
    unsub: Mapped[int] = mapped_column(default=0)
    blocked: Mapped[int] = mapped_column(default=0)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    ab_test: Mapped["ABTest"] = relationship("ABTest", back_populates="results")
    variant: Mapped[Optional["ABVariant"]] = relationship("ABVariant", back_populates="result_snapshot")


class ABMetricDaily(Base):
    """Daily aggregated metrics for A/B test performance."""
    __tablename__ = "ab_metrics_daily"
    __table_args__ = (
        UniqueConstraint("test_id", "variant_id", "metric_date", name="uq_ab_metrics_daily_unique"),
        Index("ix_ab_metrics_daily_test_date", "test_id", "metric_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    test_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ab_variants.id", ondelete="CASCADE"), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    responded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    converted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unsubscribed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    test: Mapped["ABTest"] = relationship("ABTest")
    variant: Mapped["ABVariant"] = relationship("ABVariant")


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


class SystemSetting(Base):
    """Simple key-value storage for application-wide settings."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AdminOutboundStatus(str, Enum):
    """Status of a message sent via /sendto."""
    SENT = "sent"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"


class AdminOutboundMessage(Base):
    """Log of a message sent by an admin via /sendto."""
    __tablename__ = "admin_outbound_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("admins.telegram_id"), nullable=False)
    recipients: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False)
    content_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    text_snippet: Mapped[Optional[str]] = mapped_column(String(500))
    media_ids: Mapped[Optional[List[str]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    results: Mapped[List["AdminOutboundResult"]] = relationship("AdminOutboundResult", back_populates="outbound_message")


class AdminOutboundResult(Base):
    """Result of a single message delivery from an AdminOutboundMessage."""
    __tablename__ = "admin_outbound_results"
    __table_args__ = (
        Index("ix_admin_outbound_results_outbound_id", "outbound_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    outbound_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("admin_outbound_messages.id", ondelete="CASCADE"), nullable=False)
    recipient_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    status: Mapped[AdminOutboundStatus] = mapped_column(String(20), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(255))
    delivered_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column("ts", DateTime(timezone=True), server_default=func.now())

    outbound_message: Mapped["AdminOutboundMessage"] = relationship("AdminOutboundMessage", back_populates="results")
    recipient: Mapped["User"] = relationship("User")


class SellScript(Base):
    """Sell scripts for vector search."""
    __tablename__ = "sell_scripts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sheet: Mapped[str] = mapped_column(String(100), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Vector] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_sell_scripts_updated_at", "updated_at"),
    )


class FollowupTemplate(Base):
    """Follow-up templates for re-engaging users."""
    __tablename__ = "followup_templates"
    __table_args__ = (
        UniqueConstraint("kind", name="followup_templates_kind_uq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # '24h' or '72h'
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text)
    media: Mapped[List[dict]] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OpenQuestionStatus(str, Enum):
    """Status of an open question."""
    ASKED = "asked"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class OpenQuestionLog(Base):
    """Log of open questions that were not immediately answered."""
    __tablename__ = "open_question_log"
    __table_args__ = (
        Index("ix_open_question_log_user_id_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OpenQuestionStatus] = mapped_column(String(20), default=OpenQuestionStatus.ASKED, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reasked_at: Mapped[Optional[List[datetime]]] = mapped_column(ARRAY(DateTime(timezone=True)))
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    skipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship("User")
