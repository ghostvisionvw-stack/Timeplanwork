import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings

# ── HACHAGE MOTS DE PASSE ──
# bcrypt avec cost factor 12 — sécurité maximale
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12
)

def hash_password(password: str) -> str:
    """Hache un mot de passe avec bcrypt (cost=12)."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie un mot de passe contre son hash bcrypt."""
    return pwd_context.verify(plain_password, hashed_password)

def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    Valide la robustesse du mot de passe.
    Règles : 8+ chars, 1 majuscule, 1 chiffre, 1 caractère spécial.
    """
    if len(password) < 8:
        return False, "Le mot de passe doit contenir au moins 8 caractères."
    if not any(c.isupper() for c in password):
        return False, "Le mot de passe doit contenir au moins une majuscule."
    if not any(c.isdigit() for c in password):
        return False, "Le mot de passe doit contenir au moins un chiffre."
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in password):
        return False, "Le mot de passe doit contenir au moins un caractère spécial."
    return True, "OK"

# ── TOKENS JWT ──
def create_access_token(user_id: int, email: str) -> str:
    """Crée un token JWT d'accès (courte durée — 30 min)."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(16),  # ID unique du token
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(user_id: int) -> str:
    """Crée un refresh token (longue durée — 7 jours)."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    """Décode et valide un token JWT. Retourne None si invalide."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None

def create_reset_token(email: str) -> str:
    """Crée un token de reset password (1 heure)."""
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {
        "sub": email,
        "type": "reset",
        "exp": expire,
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def verify_reset_token(token: str) -> Optional[str]:
    """Vérifie un token de reset. Retourne l'email si valide."""
    payload = decode_token(token)
    if payload and payload.get("type") == "reset":
        return payload.get("sub")
    return None

# ── SÉCURITÉ STRIPE WEBHOOK ──
def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Vérifie la signature HMAC d'un webhook Stripe."""
    import hmac
    try:
        elements = dict(e.split("=", 1) for e in sig_header.split(","))
        timestamp = elements.get("t", "")
        signatures = [v for k, v in elements.items() if k == "v1"]
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, sig) for sig in signatures)
    except Exception:
        return False
