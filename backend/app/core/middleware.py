import time
import logging
from collections import defaultdict
from typing import Dict, Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        self._requests[key] = [t for t in self._requests[key] if now - t < window_seconds]
        if len(self._requests[key]) >= max_requests:
            return False
        self._requests[key].append(now)
        return True

rate_limiter = RateLimiter()

# Routes à ne pas logger (trop de bruit)
SKIP_LOG_PATHS = {"/health", "/static", "/favicon.ico"}

# Routes sensibles à logger en priorité
SENSITIVE_PATHS = {
    "/api/auth/login", "/api/auth/register", "/api/auth/logout",
    "/api/auth/reset-password", "/api/admin", "/api/beta",
}

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _get_user_from_token(request: Request) -> Optional[str]:
    """Extrait l'email depuis le JWT sans vérification complète (juste pour le log)."""
    try:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        from jose import jwt
        token = auth[7:]
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("email")
    except Exception:
        return None

def _should_log(path: str) -> bool:
    for skip in SKIP_LOG_PATHS:
        if path.startswith(skip):
            return False
    return True

def _log_level(path: str, status_code: int) -> str:
    if status_code >= 500:
        return "error"
    if status_code >= 400:
        return "warning"
    for s in SENSITIVE_PATHS:
        if path.startswith(s):
            return "info"
    return "debug"

class SecurityMiddleware(BaseHTTPMiddleware):

    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }

    AUTH_ROUTES = {"/api/auth/login", "/api/auth/register", "/api/auth/reset-password"}
    GLOBAL_LIMIT = 100

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        client_ip = _get_client_ip(request)
        path = request.url.path
        method = request.method

        # ── RATE LIMITING ──
        if path in self.AUTH_ROUTES:
            key = f"auth:{client_ip}"
            if not rate_limiter.is_allowed(key, settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_WINDOW):
                logger.warning(f"[RATE_LIMIT] AUTH — IP:{client_ip} PATH:{path}")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Trop de tentatives. Réessayez dans 5 minutes."},
                    headers={"Retry-After": str(settings.RATE_LIMIT_WINDOW)}
                )

        if not rate_limiter.is_allowed(f"global:{client_ip}", self.GLOBAL_LIMIT, 60):
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes."},
                headers={"Retry-After": "60"}
            )

        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Corps trop volumineux."})

        # ── TRAITEMENT ──
        response = await call_next(request)

        # ── LOGGING ──
        if _should_log(path):
            duration_ms = round((time.time() - start) * 1000)
            user_email = _get_user_from_token(request)
            user_info = f"user:{user_email}" if user_email else f"ip:{client_ip}"

            log_msg = (
                f"[HTTP] {method} {path} "
                f"→ {response.status_code} "
                f"| {user_info} "
                f"| {duration_ms}ms "
                f"| ip:{client_ip}"
            )

            level = _log_level(path, response.status_code)
            if level == "error":
                logger.error(log_msg)
            elif level == "warning":
                logger.warning(log_msg)
            elif level == "info":
                logger.info(log_msg)
            else:
                logger.debug(log_msg)

        # ── SECURITY HEADERS ──
        for header, value in self.SECURITY_HEADERS.items():
            response.headers[header] = value

        return response