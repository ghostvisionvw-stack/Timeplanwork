import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
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

# ── MIDDLEWARES ──

# 1. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=3600,
)

# 2. Sécurité custom
app.add_middleware(SecurityMiddleware)

# ── ROUTES API ──
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(stripe_webhook.router)

# ── HEALTH CHECK ──
@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}

# ── PAGES FRONTEND ──
# main.py est dans /app/backend/ → frontend est dans /app/frontend/
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

@app.get("/calculateur")
async def calculateur():
    return FileResponse(FRONTEND_DIR / "calculateur.html")

@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "TimePlan.work API", "docs": "Accès restreint en production."}

# ── FICHIERS STATIQUES ──
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")