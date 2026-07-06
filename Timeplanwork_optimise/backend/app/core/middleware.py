"""
═══════════════════════════════════════════════════════════════
 TimePlan.work — Middleware de sécurité (version fusionnée)
 Remplace app/core/middleware.py
═══════════════════════════════════════════════════════════════

Conserve TON système existant :
  • Logging intelligent avec extraction de l'email depuis le JWT
  • Niveaux de log adaptés (sensible / erreur / warning)
  • Rate limiting auth + global

Ajoute les protections renforcées :
  • Bannissement progressif des IP (1min → 1h)
  • Blocage des scans malveillants (wp-admin, .env, .git…)
  • Blocage des scanners (sqlmap, nikto, nmap…)
  • Détection d'injections SQL / path traversal
  • En-têtes de sécurité complets (+ CSP)
  • Détection de la vraie IP derrière Cloudflare
"""

import time
import logging
import re
from collections import defaultdict, deque
from typing import Dict, Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# RATE LIMITER (ton système existant, conservé)
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    # Purge des clés inactives toutes les 10 min pour éviter une croissance
    # mémoire illimitée (une clé par IP vue, jamais supprimée sinon).
    PRUNE_INTERVAL = 600

    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)
        self._last_prune = time.time()

    def _prune(self, now: float):
        if now - self._last_prune < self.PRUNE_INTERVAL:
            return
        self._last_prune = now
        stale = [k for k, ts in self._requests.items() if not ts or now - ts[-1] > self.PRUNE_INTERVAL]
        for k in stale:
            del self._requests[k]

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        self._prune(now)
        self._requests[key] = [t for t in self._requests[key] if now - t < window_seconds]
        if len(self._requests[key]) >= max_requests:
            return False
        self._requests[key].append(now)
        return True

rate_limiter = RateLimiter()


# ═══════════════════════════════════════════════════════════════
# BANNISSEMENT PROGRESSIF (nouveau)
# ═══════════════════════════════════════════════════════════════

_banned_ips: Dict[str, float] = {}
_violations: Dict[str, int] = defaultdict(int)
BAN_DURATIONS = [60, 300, 900, 3600]  # 1min, 5min, 15min, 1h

def _is_banned(ip: str) -> bool:
    until = _banned_ips.get(ip)
    if until is None:
        return False
    if time.time() > until:
        del _banned_ips[ip]
        return False
    return True

def _ban_ip(ip: str, raison: str = ""):
    # Purge les bans expirés pour limiter la croissance mémoire
    now = time.time()
    expired = [k for k, until in _banned_ips.items() if now > until]
    for k in expired:
        del _banned_ips[k]
        _violations.pop(k, None)
    _violations[ip] += 1
    level = min(_violations[ip] - 1, len(BAN_DURATIONS) - 1)
    duration = BAN_DURATIONS[level]
    _banned_ips[ip] = time.time() + duration
    logger.warning(f"[SECURITY] IP bannie {ip} pour {duration}s (infraction #{_violations[ip]}) {raison}")


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION DES PROTECTIONS (nouveau)
# ═══════════════════════════════════════════════════════════════

# Chemins malveillants connus → ban immédiat
BLOCKED_PATHS = [
    "wp-admin", "wp-login", "wp-content", "wp-includes", "xmlrpc.php",
    ".env", ".git", ".aws", ".ssh", "id_rsa",
    "phpmyadmin", "phpinfo", "admin.php", "shell.php", "config.php",
    "vendor/phpunit", "eval-stdin", "/actuator", "/console", "/solr/", "/cgi-bin/",
]
ALLOWED_WELL_KNOWN = ["/.well-known/acme-challenge"]

# Patterns d'attaque (SQLi, path traversal, XSS, exécution)
ATTACK_PATTERNS = re.compile(
    r"(\.\./)|(<script)|(union\s+select)|(;\s*drop\s+table)|"
    r"(/etc/passwd)|(base64_decode)|(\bexec\s*\()",
    re.IGNORECASE
)

# Scanners de vulnérabilités connus
BLOCKED_USER_AGENTS = re.compile(
    r"(sqlmap)|(nikto)|(nmap)|(masscan)|(zgrab)|(nessus)|"
    r"(acunetix)|(dirbuster)|(gobuster)|(wpscan)",
    re.IGNORECASE
)


# ═══════════════════════════════════════════════════════════════
# LOGGING (ton système existant, conservé)
# ═══════════════════════════════════════════════════════════════

SKIP_LOG_PATHS = {"/health", "/static", "/favicon.ico"}
SENSITIVE_PATHS = {
    "/api/auth/login", "/api/auth/register", "/api/auth/logout",
    "/api/auth/reset-password", "/api/admin", "/api/beta",
}

def _get_client_ip(request: Request) -> str:
    # Cloudflare en priorité (vraie IP du visiteur)
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
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


# ═══════════════════════════════════════════════════════════════
# MIDDLEWARE PRINCIPAL
# ═══════════════════════════════════════════════════════════════

class SecurityMiddleware(BaseHTTPMiddleware):

    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(self)",
        "X-Powered-By": "TimePlan.work",
    }

    # Content-Security-Policy (autorise Stripe + polices)
    CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://js.stripe.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self' https://api.stripe.com; "
        "frame-src https://js.stripe.com https://hooks.stripe.com; "
        "object-src 'none'; base-uri 'self'; form-action 'self'"
    )

    AUTH_ROUTES = {"/api/auth/login", "/api/auth/register", "/api/auth/reset-password"}
    GLOBAL_LIMIT = 100

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        client_ip = _get_client_ip(request)
        path = request.url.path
        method = request.method
        ua = request.headers.get("user-agent", "")

        # ── 1. IP déjà bannie ? ──
        if _is_banned(client_ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes. Réessayez plus tard."},
                headers={"Retry-After": "60"}
            )

        # ── 2. Scanner malveillant (User-Agent) ? ──
        if ua and BLOCKED_USER_AGENTS.search(ua):
            _ban_ip(client_ip, f"scanner:{ua[:40]}")
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # ── 3. Chemin malveillant connu ? ──
        path_lower = path.lower()
        if not any(path_lower.startswith(w) for w in ALLOWED_WELL_KNOWN):
            for blocked in BLOCKED_PATHS:
                if blocked in path_lower:
                    _ban_ip(client_ip, f"scan:{path}")
                    return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # ── 4. Pattern d'attaque dans l'URL ? ──
        if ATTACK_PATTERNS.search(str(request.url)):
            _ban_ip(client_ip, f"injection:{path}")
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # ── 5. RATE LIMITING (ton système) ──
        if path in self.AUTH_ROUTES:
            key = f"auth:{client_ip}"
            if not rate_limiter.is_allowed(key, settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_WINDOW):
                logger.warning(f"[RATE_LIMIT] AUTH — IP:{client_ip} PATH:{path}")
                _ban_ip(client_ip, "brute-force auth")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Trop de tentatives. Réessayez dans 5 minutes."},
                    headers={"Retry-After": str(settings.RATE_LIMIT_WINDOW)}
                )

        if not rate_limiter.is_allowed(f"global:{client_ip}", self.GLOBAL_LIMIT, 60):
            _ban_ip(client_ip, "flood global")
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes."},
                headers={"Retry-After": "60"}
            )

        # ── 6. Taille du corps (anti-flood) ──
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 10 * 1024 * 1024:
                    return JSONResponse(status_code=413, content={"detail": "Corps trop volumineux."})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "En-tête Content-Length invalide."})

        # ── TRAITEMENT ──
        response = await call_next(request)

        # ── LOGGING (ton système) ──
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
        response.headers["Content-Security-Policy"] = self.CSP

        return response