import time
import logging
from collections import defaultdict
from typing import Dict
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
        self._requests[key] = [
            t for t in self._requests[key]
            if now - t < window_seconds
        ]
        if len(self._requests[key]) >= max_requests:
            return False
        self._requests[key].append(now)
        return True

rate_limiter = RateLimiter()

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
        client_ip = self._get_client_ip(request)
        path = request.url.path

        if path in self.AUTH_ROUTES:
            key = f"auth:{client_ip}"
            if not rate_limiter.is_allowed(key, settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_WINDOW):
                logger.warning(f"Rate limit AUTH — IP: {client_ip}")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Trop de tentatives. Réessayez dans 5 minutes."},
                    headers={"Retry-After": str(settings.RATE_LIMIT_WINDOW)}
                )

        key_global = f"global:{client_ip}"
        if not rate_limiter.is_allowed(key_global, self.GLOBAL_LIMIT, 60):
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes."},
                headers={"Retry-After": "60"}
            )

        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Corps trop volumineux."})

        response = await call_next(request)

        for header, value in self.SECURITY_HEADERS.items():
            response.headers[header] = value

        return response

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"