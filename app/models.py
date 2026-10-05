"""Relational schema.

users ─ affiliate_providers ─< products ─< product_sources
                                   │ ─< affiliate_links (one per provider)
                                   └─< pins ─< pin_assets, pin_variations
                                        pins ── affiliate_links (NOT NULL: the destination is never lost)
pins ─ publishing_queue ─ published_pins ─< analytics
pin_templates (catalogue of designs, seeded from code)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

PIN_STATUSES = ("draft", "ready_for_review", "approved", "rejected", "scheduled", "publishing", "published", "failed")
PRODUCT_STATUSES = ("discovered", "selected", "archived")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AffiliateProviderRow(Base):
    __tablename__ = "affiliate_providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)  # amazon | earnkaro | demo | ...
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # api | import | demo
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_product_provider_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("affiliate_providers.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(2048))
    local_image_path: Mapped[str | None] = mapped_column(String(512))
    product_url: Mapped[str | None] = mapped_column(String(2048))
    price_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str] = mapped_column(String(8), default="INR")
    price_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    availability: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="discovered", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    provider: Mapped[AffiliateProviderRow] = relationship()
    sources: Mapped[list[ProductSource]] = relationship(back_populates="product", cascade="all, delete-orphan")
    affiliate_links: Mapped[list[AffiliateLink]] = relationship(back_populates="product", cascade="all, delete-orphan")
    pins: Mapped[list[Pin]] = relationship(back_populates="product")

    @property
    def primary_link(self) -> AffiliateLink | None:
        return self.affiliate_links[0] if self.affiliate_links else None


class ProductSource(Base):
    __tablename__ = "product_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    source_type: Mapped[str] = mapped_column(String(16))  # api | import | manual | demo
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True)
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    product: Mapped[Product] = relationship(back_populates="sources")


class AffiliateLink(Base):
    __tablename__ = "affiliate_links"
    __table_args__ = (UniqueConstraint("product_id", "provider_id", name="uq_link_product_provider"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    provider_id: Mapped[int] = mapped_column(ForeignKey("affiliate_providers.id"))
    original_url: Mapped[str | None] = mapped_column(String(2048))
    affiliate_url: Mapped[str] = mapped_column(String(2048))
    link_kind: Mapped[str] = mapped_column(String(16))  # api | constructed | manual | import | demo
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_message: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    product: Mapped[Product] = relationship(back_populates="affiliate_links")
    provider: Mapped[AffiliateProviderRow] = relationship()


class PinTemplate(Base):
    __tablename__ = "pin_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    width: Mapped[int] = mapped_column(Integer, default=1000)
    height: Mapped[int] = mapped_column(Integer, default=1500)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Pin(Base):
    __tablename__ = "pins"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    # Product -> provider -> original URL -> affiliate URL: the link is mandatory.
    affiliate_link_id: Mapped[int] = mapped_column(ForeignKey("affiliate_links.id"))
    destination_url: Mapped[str] = mapped_column(String(2048))  # snapshot, must equal the link's affiliate_url
    concept_key: Mapped[str] = mapped_column(String(32))
    template_key: Mapped[str] = mapped_column(String(32))
    headline: Mapped[str] = mapped_column(String(200))
    supporting_text: Mapped[str] = mapped_column(String(300), default="")
    seo_title: Mapped[str] = mapped_column(String(100))
    seo_description: Mapped[str] = mapped_column(String(500))
    keywords: Mapped[list | None] = mapped_column(JSON)
    cta: Mapped[str] = mapped_column(String(60), default="Shop now")
    show_price: Mapped[bool] = mapped_column(Boolean, default=False)
    show_disclosure: Mapped[bool] = mapped_column(Boolean, default=True)
    collage_product_ids: Mapped[list | None] = mapped_column(JSON)
    ai_source: Mapped[str] = mapped_column(String(32), default="local")
    warnings: Mapped[list | None] = mapped_column(JSON)
    board_id: Mapped[str | None] = mapped_column(String(64))    # chosen Pinterest board (None = default board)
    board_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)

    product: Mapped[Product] = relationship(back_populates="pins")
    affiliate_link: Mapped[AffiliateLink] = relationship()
    assets: Mapped[list[PinAsset]] = relationship(back_populates="pin", cascade="all, delete-orphan",
                                                  order_by="PinAsset.id")
    variations: Mapped[list[PinVariation]] = relationship(back_populates="pin", cascade="all, delete-orphan",
                                                          order_by="PinVariation.version")
    queue_item: Mapped[PublishingQueueItem | None] = relationship(back_populates="pin", uselist=False,
                                                                  cascade="all, delete-orphan")

    @property
    def current_asset(self) -> PinAsset | None:
        return self.assets[-1] if self.assets else None


class PinAsset(Base):
    __tablename__ = "pin_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    pin_id: Mapped[int] = mapped_column(ForeignKey("pins.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="image")
    path: Mapped[str] = mapped_column(String(512))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    template_key: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pin: Mapped[Pin] = relationship(back_populates="assets")


class PinVariation(Base):
    """Every copy version of a pin (AI generated, regenerated or manually edited)."""
    __tablename__ = "pin_variations"
    id: Mapped[int] = mapped_column(primary_key=True)
    pin_id: Mapped[int] = mapped_column(ForeignKey("pins.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))  # local | openai | anthropic | local-fallback | manual
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pin: Mapped[Pin] = relationship(back_populates="variations")


class PublishingQueueItem(Base):
    __tablename__ = "publishing_queue"
    id: Mapped[int] = mapped_column(primary_key=True)
    pin_id: Mapped[int] = mapped_column(ForeignKey("pins.id"), unique=True)
    destination: Mapped[str] = mapped_column(String(16), default="pinterest")  # pinterest | export
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | scheduled | exported | publishing | published | failed
    board_id: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(32))
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)  # last attempt may have reached Pinterest
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pin: Mapped[Pin] = relationship(back_populates="queue_item")


class PublishedPin(Base):
    __tablename__ = "published_pins"
    id: Mapped[int] = mapped_column(primary_key=True)
    pin_id: Mapped[int] = mapped_column(ForeignKey("pins.id"), unique=True)
    pinterest_pin_id: Mapped[str | None] = mapped_column(String(64), index=True)
    pinterest_url: Mapped[str | None] = mapped_column(String(500))  # only if Pinterest's API returned one
    mode: Mapped[str] = mapped_column(String(16))  # api | manual
    provider: Mapped[str] = mapped_column(String(16), default="pinterest")  # pinterest | mock | manual
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)  # True: produced by the mock provider
    board_id: Mapped[str | None] = mapped_column(String(64))
    board_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    destination_url: Mapped[str | None] = mapped_column(String(2048))  # affiliate URL as published
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("pin_assets.id"))
    asset_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pin: Mapped[Pin] = relationship()
    analytics: Mapped[list[AnalyticsRecord]] = relationship(back_populates="published_pin",
                                                            cascade="all, delete-orphan")


class AnalyticsRecord(Base):
    __tablename__ = "analytics"
    id: Mapped[int] = mapped_column(primary_key=True)
    published_pin_id: Mapped[int] = mapped_column(ForeignKey("published_pins.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    pin_clicks: Mapped[int] = mapped_column(Integer, default=0)
    outbound_clicks: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # pinterest (reported by API) | manual
    raw: Mapped[dict | None] = mapped_column(JSON)  # exactly what Pinterest reported
    published_pin: Mapped[PublishedPin] = relationship(back_populates="analytics")


class PinterestConnection(Base):
    """The connected Pinterest account. Tokens are stored encrypted (see pinterest/crypto.py)."""
    __tablename__ = "pinterest_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))  # pinterest | mock
    account_id: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str | None] = mapped_column(String(32))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    scopes: Mapped[list | None] = mapped_column(JSON)
    default_board_id: Mapped[str | None] = mapped_column(String(64))
    default_board_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="connected")  # connected | expired
    last_error: Mapped[str | None] = mapped_column(String(300))
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    boards: Mapped[list[PinterestBoard]] = relationship(back_populates="connection", cascade="all, delete-orphan",
                                                        order_by="PinterestBoard.name")

    def __repr__(self) -> str:  # never include token columns
        return f"<PinterestConnection @{self.username} status={self.status}>"


class PinterestBoard(Base):
    __tablename__ = "pinterest_boards"
    __table_args__ = (UniqueConstraint("connection_id", "board_id", name="uq_board_conn"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("pinterest_connections.id", ondelete="CASCADE"))
    board_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    privacy: Mapped[str | None] = mapped_column(String(24))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    connection: Mapped[PinterestConnection] = relationship(back_populates="boards")


class AppSetting(Base):
    """Small user-editable settings (e.g. the affiliate disclosure text). No secrets here."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
