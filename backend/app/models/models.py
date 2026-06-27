from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    Float, Text, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import relationship, DeclarativeBase
import enum

class Base(DeclarativeBase):
    pass

class SubscriptionStatus(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    CANCELLED = "cancelled"

class DayType(str, enum.Enum):
    WORK = "work"
    SATURDAY = "saturday"
    ECONOMIC_LEAVE = "economic_leave"

class BetaStatus(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class UserGrade(str, enum.Enum):
    USER = "user"
    BETA = "beta"
    PRO_LIFETIME = "pro_lifetime"
    ADMIN = "admin"

class User(Base):
    __tablename__ = "users"

    id                  = Column(Integer, primary_key=True, index=True)
    email               = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password     = Column(String(255), nullable=False)
    full_name           = Column(String(255), nullable=True)
    is_active           = Column(Boolean, default=True, nullable=False)
    is_admin            = Column(Boolean, default=False, nullable=False)
    is_superadmin       = Column(Boolean, default=False, nullable=False)
    email_verified      = Column(Boolean, default=False, nullable=False)

    # Abonnement
    subscription_status = Column(SAEnum(SubscriptionStatus), default=SubscriptionStatus.FREE)
    stripe_customer_id  = Column(String(255), nullable=True, unique=True)
    stripe_sub_id       = Column(String(255), nullable=True)
    sub_expires_at      = Column(DateTime(timezone=True), nullable=True)

    # Bêta
    beta_status         = Column(SAEnum(BetaStatus), default=BetaStatus.NONE)
    beta_message        = Column(Text, nullable=True)
    beta_approved_at    = Column(DateTime(timezone=True), nullable=True)
    beta_approved_by    = Column(Integer, nullable=True)

    # Grade
    grade               = Column(SAEnum(UserGrade), default=UserGrade.USER)
    lifetime_pro        = Column(Boolean, default=False)

    # Paramètres travail
    hourly_rate         = Column(Float, default=0.0)
    contract_hours      = Column(Float, default=8.0)
    country             = Column(String(10), default="BE")
    collective_agreement= Column(String(100), nullable=True)

    # Sécurité
    failed_login_count  = Column(Integer, default=0)
    locked_until        = Column(DateTime(timezone=True), nullable=True)
    last_login_at       = Column(DateTime(timezone=True), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relations
    work_days      = relationship("WorkDay", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    audit_logs     = relationship("AuditLog", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_pro(self) -> bool:
        if self.lifetime_pro:
            return True
        if self.subscription_status != SubscriptionStatus.PRO:
            return False
        if self.sub_expires_at and self.sub_expires_at < datetime.now(timezone.utc):
            return False
        return True

    @property
    def has_beta_access(self) -> bool:
        return self.is_admin or self.beta_status == BetaStatus.APPROVED or self.is_pro

    @property
    def is_locked(self) -> bool:
        if self.locked_until and self.locked_until > datetime.now(timezone.utc):
            return True
        return False


class WorkDay(Base):
    __tablename__ = "work_days"

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date         = Column(String(10), nullable=False)
    day_type     = Column(SAEnum(DayType), default=DayType.WORK)
    start_time   = Column(String(5), nullable=True)
    end_time     = Column(String(5), nullable=True)
    break_taken  = Column(Boolean, default=False)
    real_minutes = Column(Integer, default=0)
    paid_minutes = Column(Integer, default=0)
    gap_minutes  = Column(Integer, default=0)
    note         = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="work_days")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(255), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_revoked = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)

    user = relationship("User", back_populates="refresh_tokens")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action     = Column(String(100), nullable=False)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    details    = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="audit_logs")
