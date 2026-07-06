from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    Float, Text, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import relationship, DeclarativeBase
import enum
import json

class Base(DeclarativeBase):
    pass

def ensure_utc(dt):
    """Certaines bases (SQLite en dev) renvoient des datetimes naïfs même pour
    DateTime(timezone=True) — on force UTC pour éviter les TypeError de comparaison."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

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
    address             = Column(String(500), nullable=True)
    is_active           = Column(Boolean, default=True, nullable=False)
    is_admin            = Column(Boolean, default=False, nullable=False)
    is_superadmin       = Column(Boolean, default=False, nullable=False)
    email_verified      = Column(Boolean, default=False, nullable=False)

    # Abonnement
    subscription_status = Column(SAEnum(SubscriptionStatus), default=SubscriptionStatus.FREE)
    stripe_customer_id  = Column(String(255), nullable=True, unique=True)
    stripe_sub_id       = Column(String(255), nullable=True)
    sub_expires_at      = Column(DateTime(timezone=True), nullable=True)

    # Beta
    beta_status         = Column(SAEnum(BetaStatus), default=BetaStatus.NONE)
    beta_message        = Column(Text, nullable=True)
    beta_approved_at    = Column(DateTime(timezone=True), nullable=True)
    beta_approved_by    = Column(Integer, nullable=True)

    # Grade principal (compatibilité)
    grade               = Column(SAEnum(UserGrade), default=UserGrade.USER)
    lifetime_pro        = Column(Boolean, default=False)

    # Multi-grades (JSON list: ["beta", "pro_lifetime", ...])
    grades_json         = Column(Text, nullable=True, default="[]")

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
    def grades_list(self) -> list:
        try:
            return json.loads(self.grades_json or "[]")
        except:
            return []

    def set_grades(self, grades: list):
        self.grades_json = json.dumps(list(set(grades)))
        # Sync grade principal
        if "admin" in grades:
            self.grade = UserGrade.ADMIN
            self.is_admin = True
        elif "pro_lifetime" in grades:
            self.grade = UserGrade.PRO_LIFETIME
            self.lifetime_pro = True
        elif "beta" in grades:
            self.grade = UserGrade.BETA
        else:
            self.grade = UserGrade.USER
        # Sync flags
        if "pro_lifetime" in grades:
            self.lifetime_pro = True
        if "beta" in grades:
            self.beta_status = BetaStatus.APPROVED

    def add_grade(self, grade: str):
        g = self.grades_list
        if grade not in g:
            g.append(grade)
        self.set_grades(g)

    def remove_grade(self, grade: str):
        g = self.grades_list
        if grade in g:
            g.remove(grade)
        self.set_grades(g)

    @property
    def is_pro(self) -> bool:
        if self.lifetime_pro or "pro_lifetime" in self.grades_list:
            return True
        if self.subscription_status != SubscriptionStatus.PRO:
            return False
        expires = ensure_utc(self.sub_expires_at)
        if expires and expires < datetime.now(timezone.utc):
            return False
        return True

    @property
    def has_beta_access(self) -> bool:
        return (self.is_admin or
                self.beta_status == BetaStatus.APPROVED or
                "beta" in self.grades_list or
                self.is_pro)

    @property
    def is_locked(self) -> bool:
        locked = ensure_utc(self.locked_until)
        if locked and locked > datetime.now(timezone.utc):
            return True
        return False


class EmailLog(Base):
    """Historique des emails envoyés depuis le panel admin."""
    __tablename__ = "email_logs"

    id           = Column(Integer, primary_key=True, index=True)
    sender_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recipient_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recipient_email = Column(String(255), nullable=False)
    subject      = Column(String(500), nullable=False)
    body_preview = Column(Text, nullable=True)   # premiers 300 chars
    target_group = Column(String(100), nullable=True)  # "all", "beta", "pro", etc.
    sent_count   = Column(Integer, default=1)
    status       = Column(String(20), default="sent")  # sent / failed
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
