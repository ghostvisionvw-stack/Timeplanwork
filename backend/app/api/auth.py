import hashlib
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, field_validator
from app.core.security import (
    hash_password, verify_password, validate_password_strength,
    create_access_token, create_refresh_token, decode_token,
    create_reset_token, verify_reset_token
)
from app.core.config import settings
from app.models.models import User, RefreshToken, AuditLog
from app.core.database import get_db

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
        if len(v.strip()) < 2:
            raise ValueError("Le nom doit contenir au moins 2 caractères.")
        return v.strip()

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

# ── HELPER AUDIT ──
def log_audit(db: Session, user_id: int | None, action: str, request: Request, details: str = None):
    log = AuditLog(
        user_id=user_id,
        action=action,
        ip_address=request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "unknown"),
        user_agent=request.headers.get("user-agent", "")[:500],
        details=details
    )
    db.add(log)

# ── REGISTER ──
@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    # Vérifier email existant (message générique pour éviter l'énumération d'emails)
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        # On renvoie la même réponse pour ne pas révéler si l'email existe
        raise HTTPException(
            status_code=400,
            detail="Inscription impossible. Vérifiez vos informations."
        )

    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_audit(db, user.id, "register", request)
    db.commit()

    logger.info(f"Nouveau compte créé: {user.email}")
    return {"message": "Compte créé. Bienvenue sur TimePlan.work !"}

# ── LOGIN ──
@router.post("/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()

    # ── Compte verrouillé ──
    if user and user.is_locked:
        log_audit(db, user.id, "login_locked", request)
        db.commit()
        raise HTTPException(status_code=423, detail="Compte temporairement verrouillé. Réessayez plus tard.")

    # ── Vérification (toujours même durée pour éviter timing attack) ──
    if not user or not verify_password(body.password, user.hashed_password):
        if user:
            user.failed_login_count += 1
            # Verrouillage après 5 échecs — 15 minutes
            if user.failed_login_count >= 5:
                from datetime import timedelta
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
                logger.warning(f"Compte verrouillé après échecs: {user.email}")
            log_audit(db, user.id, "login_failed", request)
            db.commit()
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")

    # ── Compte inactif ──
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Compte désactivé. Contactez le support.")

    # ── Succès — reset compteur échecs ──
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)

    # ── Génération tokens ──
    access_token = create_access_token(user.id, user.email)
    refresh_token_raw = create_refresh_token(user.id)

    # Stocker le HASH du refresh token (jamais le token brut)
    token_hash = hashlib.sha256(refresh_token_raw.encode()).hexdigest()
    from datetime import timedelta
    rt = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=request.headers.get("x-forwarded-for", "").split(",")[0].strip(),
        user_agent=request.headers.get("user-agent", "")[:500]
    )
    db.add(rt)
    log_audit(db, user.id, "login_success", request)
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_raw,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_pro": user.is_pro,
            "is_admin": user.is_admin,
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
        RefreshToken.token_hash == token_hash,
        RefreshToken.is_revoked == False
    ).first()

    if not stored or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expirée. Reconnectez-vous.")

    user = db.query(User).filter(User.id == stored.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")

    # Rotation du refresh token (sécurité — invalide l'ancien)
    stored.is_revoked = True
    new_refresh_raw = create_refresh_token(user.id)
    new_hash = hashlib.sha256(new_refresh_raw.encode()).hexdigest()
    from datetime import timedelta
    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=new_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=request.headers.get("x-forwarded-for", "").split(",")[0].strip(),
    )
    db.add(new_rt)
    db.commit()

    return {
        "access_token": create_access_token(user.id, user.email),
        "refresh_token": new_refresh_raw,
        "token_type": "bearer"
    }

# ── LOGOUT ──
@router.post("/logout")
async def logout(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if stored:
        stored.is_revoked = True
        user_id = stored.user_id
        log_audit(db, user_id, "logout", request)
        db.commit()
    return {"message": "Déconnecté avec succès."}

# ── RESET PASSWORD ──
@router.post("/reset-password")
async def request_reset(body: ResetRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    # Toujours répondre pareil (pas d'énumération d'emails)
    if user and user.is_active:
        token = create_reset_token(user.email)
        # TODO: envoyer email avec token
        # background_tasks.add_task(send_reset_email, user.email, token)
        logger.info(f"Reset password demandé pour: {user.email}")
    return {"message": "Si cet email existe, un lien de réinitialisation vous a été envoyé."}

@router.post("/reset-password/confirm")
async def confirm_reset(body: NewPasswordRequest, db: Session = Depends(get_db)):
    email = verify_reset_token(body.token)
    if not email:
        raise HTTPException(status_code=400, detail="Lien invalide ou expiré.")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    # Révoquer tous les refresh tokens existants
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"is_revoked": True})
    db.commit()
    return {"message": "Mot de passe modifié avec succès."}

# ── DÉPENDANCE AUTH ──
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
