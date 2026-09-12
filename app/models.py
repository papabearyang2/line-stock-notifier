from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    watchlist: Mapped[list["WatchStock"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class WatchStock(Base):
    __tablename__ = "watch_stocks"
    __table_args__ = (UniqueConstraint("user_id", "stock_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user: Mapped[User] = relationship(back_populates="watchlist")


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    delivery_key: Mapped[str] = mapped_column(String(80), unique=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    article_count: Mapped[int] = mapped_column(Integer, default=0)


class ShortLink(Base):
    __tablename__ = "short_links"

    slug: Mapped[str] = mapped_column(String(16), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GoodinfoCache(Base):
    __tablename__ = "goodinfo_cache"

    path: Mapped[str] = mapped_column(String(300), primary_key=True)
    html: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class GoodinfoAutomationState(Base):
    """Persistent retry and calibration state for cautious Goodinfo automation."""

    __tablename__ = "goodinfo_automation_states"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ResearchGoodinfoSnapshot(Base):
    """Persisted Goodinfo supplement for one stock and research section."""

    __tablename__ = "research_goodinfo_snapshots"
    __table_args__ = (UniqueConstraint("stock_code", "section"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    section: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    source_name: Mapped[str] = mapped_column(String(80), default="Goodinfo（補充）")
    source_url: Mapped[str] = mapped_column(Text, default="")
    message: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ResearchSecurity(Base):
    """Current TWSE/TPEx ordinary-share universe used by the research site."""

    __tablename__ = "research_securities"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(12), index=True)
    industry: Mapped[str] = mapped_column(String(80), default="", index=True)
    business: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source_name: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ResearchDailyQuote(Base):
    """One official end-of-day quote; missing fields stay NULL rather than zero."""

    __tablename__ = "research_daily_quotes"
    __table_args__ = (UniqueConstraint("stock_code", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    adjusted_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trade_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    issued_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ResearchInstitutionalFlow(Base):
    """Official net share counts by institution; cash values are derived later."""

    __tablename__ = "research_institutional_flows"
    __table_args__ = (UniqueConstraint("stock_code", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    foreign_net_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trust_net_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dealer_net_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_name: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ResearchIndexPoint(Base):
    __tablename__ = "research_index_points"
    __table_args__ = (UniqueConstraint("index_code", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    index_code: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    value: Mapped[float] = mapped_column(Float)
    source_name: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ResearchRefreshLog(Base):
    __tablename__ = "research_refresh_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    requested_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
