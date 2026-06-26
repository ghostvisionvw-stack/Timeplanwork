import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from app.core.config import settings
from app.core.middleware import SecurityMiddleware
from app.core.database import engine, Base
from app.api import auth, admin, stripe_webhook

# ── LOGGING ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── STARTUP / SHUTDOWN ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TimePlan.work backend démarrage...")
    Base.metadata.create_all(bind=engine)
    logger.info("Base de données initialisée.")
    yield
    logger.info("TimePlan.work backend arrêt.")

# ── APPLICATION ──
app = FastAPI(
    title="TimePlan.work API",
    version="1.0.0",
    docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
    openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
    lifespan=lifespan,
)

# ── MIDDLEWARES (ordre important) ──

# 1. Trusted Hosts — bloquer les requêtes avec Host header invalide
if settings.ENVIRONMENT == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "timeplan.work",
            "www.timeplan.work",
            "timeplanwork.up.railway.app",
            "localhost",
        ]
    )

# 2. CORS — contrôle strict des origines
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=3600,
)

# 3. Sécurité custom (headers + rate limiting + logging)
app.add_middleware(SecurityMiddleware)

# ── ROUTES ──
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(stripe_webhook.router)

# ── HEALTH CHECK ──
@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}

# ── ROUTE RACINE ──
@app.get("/")
async def root():
    return {"message": "TimePlan.work API", "docs": "Accès restreint en production."}
