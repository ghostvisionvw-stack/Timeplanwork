import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from app.core.config import settings
from app.core.middleware import SecurityMiddleware
from app.core.database import engine, Base
from app.api import auth, admin, stripe_webhook, beta, workdays, feedback, contact

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TimePlan.work backend démarrage...")
    Base.metadata.create_all(bind=engine)
    logger.info("Base de données initialisée.")
    yield
    logger.info("TimePlan.work backend arrêt.")

app = FastAPI(
    title="TimePlan.work API", version="1.0.0",
    docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
    openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
    lifespan=lifespan,
)

# ── MIDDLEWARES ──
if settings.ENVIRONMENT == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "timeplanwork.com",
            "www.timeplanwork.com",
            "timeplanwork-production.up.railway.app",
            "localhost",
        ]
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=3600,
)

app.add_middleware(SecurityMiddleware)

# ── ROUTES API ──
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(beta.router)
app.include_router(stripe_webhook.router)
app.include_router(workdays.router)
app.include_router(feedback.router)
app.include_router(contact.router)

# ── HEALTH ──
@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}

# ── PAGES FRONTEND ──
FRONTEND_DIR = Path(__file__).parent / "frontend"

@app.get("/")
async def root():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/calculateur")
async def calculateur():
    return FileResponse(FRONTEND_DIR / "calculateur.html")

@app.get("/login")
async def login_page():
    return FileResponse(FRONTEND_DIR / "login.html")

@app.get("/register")
async def register_page():
    return FileResponse(FRONTEND_DIR / "register.html")

@app.get("/reset-password")
async def reset_password_page():
    return FileResponse(FRONTEND_DIR / "reset-password.html")

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(FRONTEND_DIR / "dashboard.html")

@app.get("/admin")
async def admin_page():
    return FileResponse(FRONTEND_DIR / "admin.html")

@app.get("/mentions-legales")
async def mentions_legales():
    return FileResponse(FRONTEND_DIR / "mentions-legales.html")

@app.get("/confidentialite")
async def confidentialite():
    return FileResponse(FRONTEND_DIR / "confidentialite.html")

@app.get("/cgu")
async def cgu():
    return FileResponse(FRONTEND_DIR / "cgu.html")

@app.get("/contact")
async def contact_page():
    return FileResponse(FRONTEND_DIR / "contact.html")

# ── STATIQUES ──
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")