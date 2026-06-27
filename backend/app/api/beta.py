from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from app.models.models import User, BetaStatus
from app.core.database import get_db
from app.api.auth import get_current_user
import logging

router = APIRouter(prefix="/api/beta", tags=["beta"])
logger = logging.getLogger(__name__)

class BetaRequest(BaseModel):
    message: str = ""

class BetaApplyPublic(BaseModel):
    email: EmailStr
    full_name: str
    message: str = ""

# ── VÉRIFIER SON ACCÈS BÊTA (utilisateur connecté) ──
@router.get("/status")
async def beta_status(current_user: User = Depends(get_current_user)):
    return {
        "has_access": current_user.has_beta_access,
        "beta_status": current_user.beta_status,
        "grade": current_user.grade,
        "is_pro": current_user.is_pro,
    }

# ── DEMANDER L'ACCÈS BÊTA (utilisateur connecté) ──
@router.post("/apply")
async def apply_beta(
    body: BetaRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.has_beta_access:
        return {"message": "Vous avez déjà accès à la bêta."}
    if current_user.beta_status == BetaStatus.PENDING:
        raise HTTPException(status_code=400, detail="Votre demande est déjà en cours de traitement.")
    if current_user.beta_status == BetaStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Votre demande a été refusée. Contactez le support.")

    current_user.beta_status = BetaStatus.PENDING
    current_user.beta_message = body.message[:500] if body.message else ""
    db.commit()
    logger.info(f"Demande bêta: {current_user.email}")
    return {"message": "Votre demande d'accès bêta a été envoyée. Vous serez notifié par email."}

# ── INSCRIPTION BÊTA SANS COMPTE (depuis la landing) ──
@router.post("/waitlist")
async def join_waitlist(body: BetaApplyPublic, request: Request, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        if existing.beta_status == BetaStatus.APPROVED:
            return {"message": "Vous avez déjà accès à la bêta !"}
        if existing.beta_status == BetaStatus.PENDING:
            return {"message": "Votre demande est déjà enregistrée."}
        existing.beta_status = BetaStatus.PENDING
        existing.beta_message = body.message[:500]
        db.commit()
        return {"message": "Demande mise à jour. On vous contacte bientôt !"}

    # Créer un compte en attente
    from app.core.security import hash_password
    import secrets
    temp_pw = secrets.token_hex(16)
    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(temp_pw),
        full_name=body.full_name,
        is_active=False,  # Inactif jusqu'à validation
        beta_status=BetaStatus.PENDING,
        beta_message=body.message[:500],
    )
    db.add(user)
    db.commit()
    logger.info(f"Nouvelle inscription waitlist: {body.email}")
    return {"message": "Inscription enregistrée ! On vous contacte bientôt pour vous donner accès."}
