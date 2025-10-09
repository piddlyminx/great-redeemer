from __future__ import annotations

import os
import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    create_engine,
    String,
    Integer,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    JSON,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wos.db")

engine = create_engine(
    DATABASE_URL,
    future=True,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class RedemptionStatus(enum.Enum):
    pending = "pending"
    redeemed_new = "redeemed_new"          # This attempt redeemed the code now
    redeemed_already = "redeemed_already"  # User had already redeemed (RECEIVED/SAME TYPE EXCHANGE)
    failed = "failed"


class Alliance(Base):
    __tablename__ = "alliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tag: Mapped[str] = mapped_column(String(3), nullable=False)
    quota: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    users: Mapped[list[User]] = relationship("User", back_populates="alliance")  # type: ignore[name-defined]
    managers: Mapped[list[WebAccount]] = relationship("WebAccount", back_populates="alliance")  # type: ignore[name-defined]

    __table_args__ = (
        UniqueConstraint("tag", name="uq_alliance_tag"),
        Index("ix_alliance_name", "name"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fid: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    alliance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("alliances.id", ondelete="SET NULL"))
    state: Mapped[Optional[str]] = mapped_column(String(50))
    rank: Mapped[Optional[str]] = mapped_column(String(10))  # e.g., R1..R5
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    alliance: Mapped[Optional[Alliance]] = relationship("Alliance", back_populates="users")
    redemptions: Mapped[list[Redemption]] = relationship("Redemption", back_populates="user")  # type: ignore[name-defined]

    __table_args__ = (
        Index("ix_users_fid", "fid"),
        Index("ix_users_alliance", "alliance_id"),
    )


class WebRole(enum.Enum):
    admin = "admin"
    manager = "manager"  # R4/R5


class WebAccount(Base):
    __tablename__ = "web_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=WebRole.manager.value, nullable=False)
    alliance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("alliances.id", ondelete="SET NULL"))
    alliance_rank: Mapped[Optional[str]] = mapped_column(String(2))  # R4 or R5
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    alliance: Mapped[Optional[Alliance]] = relationship("Alliance", back_populates="managers")


class GiftCode(Base):
    __tablename__ = "gift_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    redemptions: Mapped[list[Redemption]] = relationship("Redemption", back_populates="gift_code")  # type: ignore[name-defined]

    __table_args__ = (
        Index("ix_gift_codes_active", "active"),
    )


class Redemption(Base):
    __tablename__ = "redemptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    gift_code_id: Mapped[int] = mapped_column(ForeignKey("gift_codes.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RedemptionStatus.pending.value, nullable=False)
    captcha: Mapped[Optional[str]] = mapped_column(String(8))
    rewards: Mapped[Optional[str]] = mapped_column(Text)
    result_msg: Mapped[Optional[str]] = mapped_column(Text)
    err_code: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="redemptions")  # type: ignore[name-defined]
    gift_code: Mapped[GiftCode] = relationship("GiftCode", back_populates="redemptions")  # type: ignore[name-defined]
    attempts: Mapped[list[RedemptionAttempt]] = relationship("RedemptionAttempt", back_populates="redemption", cascade="all, delete-orphan")  # type: ignore[name-defined]

    __table_args__ = (
        UniqueConstraint("user_id", "gift_code_id", name="uq_user_code"),
        Index("ix_redemptions_status", "status"),
    )


class RedemptionAttempt(Base):
    __tablename__ = "redemption_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    redemption_id: Mapped[int] = mapped_column(ForeignKey("redemptions.id", ondelete="CASCADE"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    captcha: Mapped[Optional[str]] = mapped_column(String(8))
    result_msg: Mapped[Optional[str]] = mapped_column(Text)
    err_code: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    redemption: Mapped[Redemption] = relationship("Redemption", back_populates="attempts")  # type: ignore[name-defined]


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
