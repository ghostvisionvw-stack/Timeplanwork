import time
import logging
from collections import defaultdict
from typing import Dict, Tuple
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)

# ── RATE LIMITER EN MÉMOIRE ──
# Pour production avec plusieurs instances → utiliser Redis
class RateLimiter:
    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        self._requests[key] = [
            t for t in self._requests[key]
            if now - t < window_seconds
        ]
        if len(self._requests[key]) >= max_requests:
            return False
        self._requests[key].append(now)
        return True

    def remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        now = time.time()
        recent = [t for t in self._requests[key] if now - t < window_seconds]
        return max(0, max_requests - len(recent))

rate_limiter = RateLimiter()

# ── MIDDLEWARE SÉCURITÉ PRINCIPAL ──
class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware qui ajoute :
    - Headers de sécurité HTTP
    - Rate limiting global
    - Logging des requêtes suspectes
    - Protection contre les attaques communes
    """

    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://js.stripe.com; "
            "style-src 'self' 'unsafe-inline'; "
            "frame-src https://js.stripe.com; "
            "connect-src 'self' https://api.stripe.com;"
        ),
    }

    # Routes avec rate limiting strict (auth)
    AUTH_ROUTES = {"/api/auth/login", "/api/auth/register", "/api/auth/reset-password"}
    # Rate limite globale
    GLOBAL_LIMIT = 100  # 100 req/minute par IP

    async def dispatch(self, request: Request, call_next):
        client_ip = self._get_client_ip(request)
        path = request.url.path

        # ── Rate limit strict sur les routes auth ──
        if path in self.AUTH_ROUTES:
            key = f"auth:{client_ip}"
            if not rate_limiter.is_allowed(key, settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_WINDOW):
                logger.warning(f"Rate limit AUTH dépassé — IP: {client_ip} — Route: {path}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Trop de tentatives. Réessayez dans 5 minutes.",
                        "retry_after": settings.RATE_LIMIT_WINDOW
                    },
                    headers={"Retry-After": str(settings.RATE_LIMIT_WINDOW)}
                )

        # ── Rate limit global ──
        key_global = f"global:{client_ip}"
        if not rate_limiter.is_allowed(key_global, self.GLOBAL_LIMIT, 60):
            logger.warning(f"Rate limit GLOBAL dépassé — IP: {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes. Réessayez dans un moment."},
                headers={"Retry-After": "60"}
            )

        # ── Protection taille de corps ──
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:  # 10 MB max
            return JSONResponse(status_code=413, content={"detail": "Corps de requête trop volumineux."})

        # ── Traitement ──
        response = await call_next(request)

        # ── Ajout des headers de sécurité ──
        for header, value in self.SECURITY_HEADERS.items():
            response.headers[header] = value

        # ── Supprimer les headers qui révèlent la stack ──
        response.headers.pop("server", None)
        response.headers.pop("x-powered-by", None)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Récupère l'IP réelle derrière un proxy/Railway."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
