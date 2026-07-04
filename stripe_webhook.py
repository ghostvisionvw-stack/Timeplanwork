import hashlib
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from app.core.security import (
    hash_password, verify_password, validate_password_strength,
    create_access_token, create_refresh_token, decode_token,
    create_reset_token, verify_reset_token
)
from app.core.config import settings
from app.models.models import User, RefreshToken, AuditLog, ensure_utc
from app.core.database import get_db
from app.core.email import send_reset_password_email

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer()
logger = logging.getLogger(__name__)

# ── SCHEMAS ──
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str

    @field_validator("password")
    @classmethod
    def password_strong(cls, v):
        ok, msg = validate_password_strength(v)
        if not ok:
            raise ValueError(msg)
        return v

    @field_validator("full_name")
    @classmethod
    def name_valid(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Le nom doit contenir au moins 2 caractères.")
        if len(v) > 255:
            raise ValueError("Le nom est trop long (255 caractères maximum).")
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class ResetRequest(BaseModel):
    email: EmailStr

class NewPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strong(cls, v):
        ok, msg = validate_password_strength(v)
        if not ok:
            raise ValueError(msg)
        return v

class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None
    collective_agreement: Optional[str] = None

# ── HELPERS ──
def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _get_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")[:500]

def log_audit(db: Session, user_id, action: str, request: Request, details: str = None):
    log = AuditLog(
        user_id=user_id, action=action,
        ip_address=_get_ip(request), user_agent=_get_ua(request), details=details
    )
    db.add(log)
    user_info = f"user_id:{user_id}" if user_id else "anonymous"
    logger.info(f"[AUDIT] {action} | {user_info} | ip:{_get_ip(request)}" + (f" | {details}" if details else ""))

# ── DÉPENDANCES AUTH ──
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Non authentifié.")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")
    return user

def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs.")
    return current_user

def require_pro(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_pro:
        raise HTTPException(status_code=402, detail="Fonctionnalité réservée aux abonnés Pro.")
    return current_user

# ── REGISTER ──
@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        log_audit(db, None, "register_duplicate", request, f"email:{body.email.lower()}")
        db.commit()
        raise HTTPException(status_code=400, detail="Inscription impossible. Vérifiez vos informations.")
    user = User(
        email=body.email.lower(), hashed_password=hash_password(body.password),
        full_name=body.full_name, is_active=True,
    )
    db.add(user); db.commit(); db.refresh(user)
    log_audit(db, user.id, "register_success", request, f"email:{user.email}")
    db.commit()
    logger.info(f"[NEW_USER] {user.email} | ip:{_get_ip(request)}")
    return {"message": "Compte créé. Bienvenue sur TimePlan.work !"}

# ── LOGIN ──
@router.post("/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    ip = _get_ip(request)
    if user and user.is_locked:
        log_audit(db, user.id, "login_locked", request, f"email:{user.email}")
        db.commit()
        raise HTTPException(status_code=423, detail="Compte temporairement verrouillé. Réessayez plus tard.")
    if not user or not verify_password(body.password, user.hashed_password):
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
                log_audit(db, user.id, "account_locked", request, f"attempts:{user.failed_login_count}")
            else:
                log_audit(db, user.id, "login_failed", request, f"attempt:{user.failed_login_count}")
            db.commit()
        else:
            logger.warning(f"[LOGIN_UNKNOWN] email:{body.email.lower()} | ip:{ip}")
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
    if not user.is_active:
        log_audit(db, user.id, "login_inactive", request); db.commit()
        raise HTTPException(status_code=403, detail="Compte désactivé. Contactez le support.")
    user.failed_login_count = 0; user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    access_token = create_access_token(user.id, user.email)
    refresh_token_raw = create_refresh_token(user.id)
    token_hash = hashlib.sha256(refresh_token_raw.encode()).hexdigest()
    db.add(RefreshToken(
        user_id=user.id, token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=ip, user_agent=_get_ua(request)
    ))
    log_audit(db, user.id, "login_success", request, f"email:{user.email}")
    db.commit()
    logger.info(f"[LOGIN_OK] {user.email} | admin:{user.is_admin} | ip:{ip}")
    return {
        "access_token": access_token, "refresh_token": refresh_token_raw, "token_type": "bearer",
        "user": {
            "id": user.id, "email": user.email, "full_name": user.full_name,
            "is_pro": user.is_pro, "is_admin": user.is_admin, "is_superadmin": user.is_superadmin,
            "address": user.address if hasattr(user, 'address') else None,
            "country": user.country, "collective_agreement": user.collective_agreement,
        }
    }

# ── REFRESH TOKEN ──
@router.post("/refresh")
async def refresh_token(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token invalide.")
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    stored = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash, RefreshToken.is_revoked == False
    ).first()
    if not stored or ensure_utc(stored.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expirée. Reconnectez-vous.")
    user = db.query(User).filter(User.id == stored.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")
    stored.is_revoked = True
    new_refresh_raw = create_refresh_token(user.id)
    new_hash = hashlib.sha256(new_refresh_raw.encode()).hexdigest()
    db.add(RefreshToken(
        user_id=user.id, token_hash=new_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=_get_ip(request),
    ))
    log_audit(db, user.id, "token_refresh", request)
    db.commit()
    return {"access_token": create_access_token(user.id, user.email), "refresh_token": new_refresh_raw, "token_type": "bearer"}

# ── LOGOUT ──
@router.post("/logout")
async def logout(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if stored:
        stored.is_revoked = True
        log_audit(db, stored.user_id, "logout", request)
        db.commit()
    return {"message": "Déconnecté avec succès."}

# ── RESET PASSWORD ──
@router.post("/reset-password")
async def request_reset(body: ResetRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user and user.is_active:
        token = create_reset_token(user.email)
        background_tasks.add_task(send_reset_password_email, user.email, token)
        logger.info(f"[RESET_PASSWORD_REQUEST] {user.email}")
    return {"message": "Si cet email existe, un lien de réinitialisation vous a été envoyé."}

@router.post("/reset-password/confirm")
async def confirm_reset(body: NewPasswordRequest, request: Request, db: Session = Depends(get_db)):
    email = verify_reset_token(body.token)
    if not email:
        raise HTTPException(status_code=400, detail="Lien invalide ou expiré.")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    # Usage unique : un token émis avant le dernier changement de mot de passe est refusé
    payload = decode_token(body.token) or {}
    issued_at = payload.get("iat")
    if issued_at and user.password_changed_at:
        token_time = datetime.fromtimestamp(issued_at, tz=timezone.utc)
        if token_time <= ensure_utc(user.password_changed_at):
            raise HTTPException(status_code=400, detail="Lien invalide ou expiré.")
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"is_revoked": True})
    log_audit(db, user.id, "password_reset", request)
    db.commit()
    return {"message": "Mot de passe modifié avec succès."}

# ── MON PROFIL ──
@router.get("/me")
async def get_profile(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id, "email": current_user.email,
        "full_name": current_user.full_name,
        "address": getattr(current_user, 'address', None),
        "country": current_user.country,
        "collective_agreement": current_user.collective_agreement,
        "is_pro": current_user.is_pro, "is_admin": current_user.is_admin,
        "created_at": current_user.created_at.isoformat(),
        "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
    }

@router.patch("/me")
async def update_profile(
    body: UpdateProfileRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    changes = []
    if body.full_name is not None and body.full_name.strip():
        current_user.full_name = body.full_name.strip()
        changes.append("full_name")
    if body.address is not None:
        if hasattr(current_user, 'address'):
            current_user.address = body.address.strip()
        changes.append("address")
    if body.country is not None:
        current_user.country = body.country
        changes.append("country")
    if body.collective_agreement is not None:
        current_user.collective_agreement = body.collective_agreement
        changes.append("collective_agreement")
    if changes:
        log_audit(db, current_user.id, "profile_updated", request, f"fields:{','.join(changes)}")
        db.commit()
        logger.info(f"[PROFILE_UPDATE] {current_user.email} | fields:{changes}")
    return {
        "message": "Profil mis à jour avec succès.",
        "user": {
            "id": current_user.id, "email": current_user.email,
            "full_name": current_user.full_name,
            "address": getattr(current_user, 'address', None),
            "country": current_user.country,
            "collective_agreement": current_user.collective_agreement,
        }
    }