import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from app.models.models import User
from app.api.auth import get_current_user, get_current_admin
from app.core.database import get_db

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
logger = logging.getLogger(__name__)

# ── MODEL DB INLINE (ajouté via ALTER TABLE) ──
# Table: feedbacks
# id, user_id, subject, message, status, admin_reply, admin_id, created_at, replied_at

from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from app.core.database import Base

class Feedback(Base):
    __tablename__ = "feedbacks"
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject     = Column(String(200), nullable=False)
    message     = Column(Text, nullable=False)
    status      = Column(String(20), default="open")   # open / replied / closed
    admin_reply = Column(Text, nullable=True)
    admin_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    replied_at  = Column(DateTime(timezone=True), nullable=True)

# ── SCHEMAS ──
class FeedbackCreate(BaseModel):
    subject: str
    message: str

class FeedbackReply(BaseModel):
    reply: str
    status: Optional[str] = "replied"

# ── CRÉER FEEDBACK (utilisateur) ──
@router.post("/", status_code=201)
async def create_feedback(
    body: FeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if len(body.subject.strip()) < 3:
        raise HTTPException(status_code=400, detail="Sujet trop court.")
    if len(body.message.strip()) < 10:
        raise HTTPException(status_code=400, detail="Message trop court (min 10 caractères).")
    fb = Feedback(
        user_id=current_user.id,
        subject=body.subject.strip()[:200],
        message=body.message.strip(),
        status="open"
    )
    db.add(fb); db.commit(); db.refresh(fb)
    logger.info(f"[FEEDBACK] Nouveau feedback de {current_user.email} | sujet: {fb.subject}")
    return {"message": "Feedback envoyé. Merci pour votre retour !", "id": fb.id}

# ── MES FEEDBACKS (utilisateur) ──
@router.get("/mine")
async def my_feedbacks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    fbs = db.query(Feedback).filter(
        Feedback.user_id == current_user.id
    ).order_by(desc(Feedback.created_at)).all()
    return {"feedbacks": [{
        "id": f.id, "subject": f.subject, "message": f.message,
        "status": f.status, "admin_reply": f.admin_reply,
        "created_at": f.created_at.isoformat(),
        "replied_at": f.replied_at.isoformat() if f.replied_at else None,
    } for f in fbs]}

# ── TOUS LES FEEDBACKS (admin) ──
@router.get("/admin/all")
async def all_feedbacks(
    page: int = Query(1, ge=1),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    q = db.query(Feedback)
    if status:
        q = q.filter(Feedback.status == status)
    total = q.count()
    fbs = q.order_by(desc(Feedback.created_at)).offset((page-1)*20).limit(20).all()

    result = []
    for f in fbs:
        user = db.query(User).filter(User.id == f.user_id).first()
        result.append({
            "id": f.id,
            "user_email": user.email if user else "—",
            "user_name": user.full_name if user else "—",
            "subject": f.subject, "message": f.message,
            "status": f.status, "admin_reply": f.admin_reply,
            "created_at": f.created_at.isoformat(),
            "replied_at": f.replied_at.isoformat() if f.replied_at else None,
        })
    return {"total": total, "page": page, "feedbacks": result}

# ── RÉPONDRE À UN FEEDBACK (admin) ──
@router.patch("/admin/{feedback_id}/reply")
async def reply_feedback(
    feedback_id: int,
    body: FeedbackReply,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback introuvable.")
    fb.admin_reply = body.reply.strip()
    fb.admin_id = admin.id
    fb.status = body.status if body.status in ["replied", "closed"] else "replied"
    fb.replied_at = datetime.now(timezone.utc)
    db.commit()
    logger.info(f"[FEEDBACK_REPLY] Admin {admin.email} → feedback #{feedback_id}")
    return {"message": "Réponse envoyée."}

# ── FERMER/ROUVRIR (admin) ──
@router.patch("/admin/{feedback_id}/status")
async def set_feedback_status(
    feedback_id: int,
    status: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    if status not in ["open", "replied", "closed"]:
        raise HTTPException(status_code=400, detail="Statut invalide.")
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback introuvable.")
    fb.status = status; db.commit()
    return {"message": f"Statut mis à jour : {status}"}

# ── STATS (admin dashboard) ──
@router.get("/admin/stats")
async def feedback_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    total = db.query(Feedback).count()
    open_ = db.query(Feedback).filter(Feedback.status == "open").count()
    replied = db.query(Feedback).filter(Feedback.status == "replied").count()
    closed = db.query(Feedback).filter(Feedback.status == "closed").count()
    return {"total": total, "open": open_, "replied": replied, "closed": closed}
