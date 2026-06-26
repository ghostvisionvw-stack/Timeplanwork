import secrets
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    # ── APP ──
    APP_NAME: str = "TimePlan.work"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"  # development | production
    DEBUG: bool = False

    # ── SÉCURITÉ JWT ──
    SECRET_KEY: str  # OBLIGATOIRE — générer avec: secrets.token_hex(64)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30       # Token court — 30 min
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7           # Refresh — 7 jours

    # ── BASE DE DONNÉES ──
    DATABASE_URL: str  # OBLIGATOIRE — fourni par Railway

    # ── STRIPE ──
    STRIPE_SECRET_KEY: str       # sk_live_... ou sk_test_...
    STRIPE_WEBHOOK_SECRET: str   # whsec_...
    STRIPE_PRICE_PRO_MONTHLY: str  # price_...

    # ── CORS ──
    FRONTEND_URL: str = "https://timeplan.work"
    ALLOWED_ORIGINS: List[str] = ["https://timeplan.work", "https://www.timeplan.work"]

    # ── EMAIL (pour reset password) ──
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "noreply@timeplan.work"

    # ── ADMIN ──
    ADMIN_EMAIL: str  # OBLIGATOIRE — votre email admin

    # ── RATE LIMITING ──
    RATE_LIMIT_LOGIN: int = 5       # 5 tentatives max par IP
    RATE_LIMIT_WINDOW: int = 300    # sur 5 minutes

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
