import logging
import re
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc, Column, Integer, String, Text, DateTime
from pydantic import BaseModel, EmailStr
from app.models.models import User
from app.api.auth import get_current_admin
from app.core.database import get_db, Base

router = APIRouter(prefix="/api/contact", tags=["contact"])
logger = logging.getLogger(__name__)

# ── MODEL DB ──
# Table: contact_messages
class ContactMessage(Base):
    __tablename__ = "contact_messages"
    id          = Column(Integer, primary_key=True, index=True)
    nom         = Column(String(80), nullable=False)
    email       = Column(String(120), nullable=False)
    sujet       = Column(String(120), nullable=False)
    message     = Column(Text, nullable=False)
    status      = Column(String(20), default="new")      # new / read / archived
    ip          = Column(String(45), nullable=True)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# ── SCHEMA ──
class ContactCreate(BaseModel):
    nom: str
    email: str
    sujet: str
    message: str

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Anti-spam basique : limite en mémoire par IP (réinitialisé au redémarrage)
_last_submit = {}

def _client_ip(request: Request) -> str:
    """Vraie IP du visiteur derrière le proxy (Cloudflare / Railway)."""
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# ── ENVOYER UN MESSAGE (public, pas d'auth) ──
@router.post("/", status_code=201)
async def create_contact(
    body: ContactCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    nom = body.nom.strip()
    email = body.email.strip().lower()
    sujet = body.sujet.strip()
    message = body.message.strip()

    # Validation
    if len(nom) < 2:
        raise HTTPException(status_code=400, detail="Nom trop court.")
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Adresse email invalide.")
    if len(message) < 10:
        raise HTTPException(status_code=400, detail="Message trop court (min 10 caractères).")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="Message trop long (max 2000 caractères).")

    # Anti-spam : 1 message / 30 secondes par IP
    ip = _client_ip(request)
    now = datetime.now(timezone.utc).timestamp()
    last = _last_submit.get(ip, 0)
    if now - last < 30:
        raise HTTPException(status_code=429, detail="Merci de patienter avant de renvoyer un message.")
    _last_submit[ip] = now
    # Purge des entrées expirées (évite une croissance mémoire illimitée)
    if len(_last_submit) > 1000:
        for k in [k for k, t in _last_submit.items() if now - t > 60]:
            del _last_submit[k]

    # Sauvegarde en DB
    msg = ContactMessage(
        nom=nom[:80],
        email=email[:120],
        sujet=sujet[:120],
        message=message,
        ip=ip[:45],
        status="new"
    )
    db.add(msg); db.commit(); db.refresh(msg)
    logger.info(f"[CONTACT] Nouveau message de {email} | sujet: {sujet}")

    # ── ENVOI EMAIL de notification à l'admin (en tâche de fond : SMTP est lent) ──
    from app.core.email import send_contact_notification
    background_tasks.add_task(send_contact_notification, nom=nom, email=email, sujet=sujet, message=message)

    return {"message": "Message envoyé. Nous vous répondrons rapidement !", "id": msg.id}

# ── TOUS LES MESSAGES (admin) ──
@router.get("/admin/all")
async def all_messages(
    page: int = 1,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    q = db.query(ContactMessage)
    if status:
        q = q.filter(ContactMessage.status == status)
    total = q.count()
    msgs = q.order_by(desc(ContactMessage.created_at)).offset((page-1)*20).limit(20).all()
    return {"total": total, "page": page, "messages": [{
        "id": m.id, "nom": m.nom, "email": m.email,
        "sujet": m.sujet, "message": m.message, "status": m.status,
        "created_at": m.created_at.isoformat(),
    } for m in msgs]}

# ── MARQUER LU / ARCHIVER (admin) ──
@router.patch("/admin/{message_id}/status")
async def set_message_status(
    message_id: int,
    status: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    if status not in ["new", "read", "archived"]:
        raise HTTPException(status_code=400, detail="Statut invalide.")
    m = db.query(ContactMessage).filter(ContactMessage.id == message_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Message introuvable.")
    m.status = status; db.commit()
    return {"message": f"Statut mis à jour : {status}"}

# ── STATS (admin) ──
@router.get("/admin/stats")
async def contact_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    total = db.query(ContactMessage).count()
    new = db.query(ContactMessage).filter(ContactMessage.status == "new").count()
    return {"total": total, "new": new}