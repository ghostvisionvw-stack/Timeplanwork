from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from pydantic import BaseModel
from app.models.models import User, WorkDay, AuditLog, SubscriptionStatus, BetaStatus, UserGrade, RefreshToken
from app.api.auth import get_current_admin
from app.core.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ── DASHBOARD ──
@router.get("/dashboard")
async def dashboard(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    total_users  = db.query(func.count(User.id)).scalar()
    pro_users    = db.query(func.count(User.id)).filter(User.subscription_status == SubscriptionStatus.PRO).scalar()
    beta_pending = db.query(func.count(User.id)).filter(User.beta_status == BetaStatus.PENDING).scalar()
    beta_approved= db.query(func.count(User.id)).filter(User.beta_status == BetaStatus.APPROVED).scalar()
    lifetime     = db.query(func.count(User.id)).filter(User.lifetime_pro == True).scalar()
    total_days   = db.query(func.count(WorkDay.id)).scalar()
    new_today    = db.query(func.count(User.id)).filter(
        func.date(User.created_at) == datetime.now(timezone.utc).date()
    ).scalar()
    return {
        "users": {"total": total_users, "pro": pro_users, "new_today": new_today, "free": total_users - pro_users},
        "beta": {"pending": beta_pending, "approved": beta_approved},
        "lifetime": lifetime,
        "work_days": {"total": total_days},
        "mrr_estimate": round(pro_users * 4.99, 2),
    }

# ── LISTE UTILISATEURS ──
@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None, status: Optional[str] = None,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    q = db.query(User)
    if search:
        q = q.filter(User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%"))
    if status == "pro":
        q = q.filter(User.subscription_status == SubscriptionStatus.PRO)
    elif status == "free":
        q = q.filter(User.subscription_status == SubscriptionStatus.FREE)
    elif status == "inactive":
        q = q.filter(User.is_active == False)
    elif status == "beta":
        q = q.filter(User.beta_status == BetaStatus.APPROVED)

    total = q.count()
    users = q.order_by(desc(User.created_at)).offset((page-1)*per_page).limit(per_page).all()

    result = []
    for u in users:
        # Dernière IP de connexion depuis les RefreshTokens
        last_token = db.query(RefreshToken).filter(
            RefreshToken.user_id == u.id,
            RefreshToken.is_revoked == False
        ).order_by(desc(RefreshToken.created_at)).first()

        # Dernière IP depuis les AuditLogs
        last_login_log = db.query(AuditLog).filter(
            AuditLog.user_id == u.id,
            AuditLog.action == "login_success"
        ).order_by(desc(AuditLog.created_at)).first()

        result.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "is_active": u.is_active,
            "is_pro": u.is_pro,
            "subscription_status": u.subscription_status,
            "beta_status": u.beta_status,
            "grade": u.grade,
            "lifetime_pro": u.lifetime_pro,
            "created_at": u.created_at.isoformat(),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "last_ip": last_login_log.ip_address if last_login_log else None,
            "failed_login_count": u.failed_login_count,
            "is_locked": u.is_locked,
        })
    return {"total": total, "page": page, "per_page": per_page, "users": result}

# ── DÉTAIL UTILISATEUR ──
@router.get("/users/{user_id}")
async def get_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    logs = db.query(AuditLog).filter(AuditLog.user_id == user_id).order_by(desc(AuditLog.created_at)).limit(20).all()
    ips = db.query(AuditLog.ip_address, AuditLog.created_at).filter(
        AuditLog.user_id == user_id, AuditLog.action == "login_success"
    ).order_by(desc(AuditLog.created_at)).limit(10).all()
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "is_active": user.is_active, "is_admin": user.is_admin, "is_pro": user.is_pro,
        "subscription_status": user.subscription_status,
        "beta_status": user.beta_status, "grade": user.grade,
        "lifetime_pro": user.lifetime_pro,
        "created_at": user.created_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "failed_login_count": user.failed_login_count,
        "is_locked": user.is_locked,
        "login_ips": [{"ip": ip, "at": at.isoformat()} for ip, at in ips],
        "recent_activity": [{"action": l.action, "ip": l.ip_address, "at": l.created_at.isoformat()} for l in logs]
    }

# ── DEMANDES BÊTA ──
@router.get("/beta/requests")
async def beta_requests(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    users = db.query(User).filter(User.beta_status == BetaStatus.PENDING).order_by(desc(User.created_at)).all()
    return {"requests": [{
        "id": u.id, "email": u.email, "full_name": u.full_name,
        "beta_message": u.beta_message, "created_at": u.created_at.isoformat(),
    } for u in users]}

@router.patch("/beta/{user_id}/approve")
async def approve_beta(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.beta_status = BetaStatus.APPROVED
    user.grade = UserGrade.BETA
    user.beta_approved_at = datetime.now(timezone.utc)
    user.beta_approved_by = admin.id
    db.commit()
    return {"message": f"Accès bêta accordé à {user.email}"}

@router.patch("/beta/{user_id}/reject")
async def reject_beta(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.beta_status = BetaStatus.REJECTED
    db.commit()
    return {"message": f"Demande refusée pour {user.email}"}

# ── GRADES ──
class GradeRequest(BaseModel):
    grade: str

@router.patch("/users/{user_id}/grade")
async def set_grade(user_id: int, body: GradeRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de modifier votre propre grade.")
    grade_map = {"user": UserGrade.USER, "beta": UserGrade.BETA, "pro_lifetime": UserGrade.PRO_LIFETIME, "admin": UserGrade.ADMIN}
    if body.grade not in grade_map:
        raise HTTPException(status_code=400, detail="Grade invalide.")
    user.grade = grade_map[body.grade]
    if body.grade == "pro_lifetime":
        user.lifetime_pro = True
        user.beta_status = BetaStatus.APPROVED
    if body.grade == "beta":
        user.beta_status = BetaStatus.APPROVED
    if body.grade == "admin":
        user.is_admin = True
        user.beta_status = BetaStatus.APPROVED
    db.commit()
    return {"message": f"Grade '{body.grade}' accordé à {user.email}"}

# ── ACTIONS ──
@router.patch("/users/{user_id}/activate")
async def toggle_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de modifier votre propre compte.")
    user.is_active = not user.is_active
    # Si désactivé → révoquer tous les tokens
    if not user.is_active:
        db.query(RefreshToken).filter(RefreshToken.user_id == user_id).update({"is_revoked": True})
    db.commit()
    return {"message": f"Compte {'activé' if user.is_active else 'désactivé'}.", "is_active": user.is_active}

@router.patch("/users/{user_id}/unlock")
async def unlock_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    return {"message": "Compte déverrouillé."}

@router.patch("/users/{user_id}/grant-pro")
async def grant_pro(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    from datetime import timedelta
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.subscription_status = SubscriptionStatus.PRO
    user.sub_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db.commit()
    return {"message": f"Accès Pro 30 jours accordé à {user.email}"}

# ── SUPPRIMER COMPTE ──
@router.delete("/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de supprimer votre propre compte.")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Impossible de supprimer un compte admin.")
    email = user.email
    db.delete(user)
    db.commit()
    return {"message": f"Compte {email} supprimé définitivement."}

# ── AUDIT LOGS ──
@router.get("/audit-logs")
async def audit_logs(
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
    action: Optional[str] = None,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    logs = q.order_by(desc(AuditLog.created_at)).offset((page-1)*per_page).limit(per_page).all()
    return {"total": total, "logs": [{
        "id": l.id, "user_id": l.user_id, "action": l.action,
        "ip_address": l.ip_address, "details": l.details,
        "at": l.created_at.isoformat()
    } for l in logs]}

# ── STATS ──
@router.get("/stats")
async def stats(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    actions = db.query(AuditLog.action, func.count(AuditLog.id).label('count')).group_by(AuditLog.action).all()
    return {
        "actions": [{"action": a.action, "count": a.count} for a in actions],
        "total_logs": db.query(func.count(AuditLog.id)).scalar(),
    }
