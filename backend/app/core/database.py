from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Import Base ici pour éviter les imports circulaires
from app.models.models import Base

def get_db():
    """Dépendance FastAPI — session DB par requête."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db_sync():
    """Version synchrone pour les webhooks."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
