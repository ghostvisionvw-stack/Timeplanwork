from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from pydantic import BaseModel, EmailStr
from app.models.models import User, WorkDay, AuditLog, SubscriptionStatus, BetaStatus, UserGrade, RefreshToken, EmailLog
from app.api.auth import get_current_admin
from app.core.database import get_db
from app.core.email import send_beta_approved_email, send_feedback_reply_email, send_email

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ── DASHBOARD ──
@router.get("/dashboard")
async def dashboard(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    total_users   = db.query(func.count(User.id)).scalar()
    pro_users     = db.query(func.count(User.id)).filter(User.subscription_status == SubscriptionStatus.PRO).scalar()
    beta_pending  = db.query(func.count(User.id)).filter(User.beta_status == BetaStatus.PENDING).scalar()
    beta_approved = db.query(func.count(User.id)).filter(User.beta_status == BetaStatus.APPROVED).scalar()
    lifetime      = db.query(func.count(User.id)).filter(User.lifetime_pro == True).scalar()
    total_days    = db.query(func.count(WorkDay.id)).scalar()
    new_today     = db.query(func.count(User.id)).filter(
        func.date(User.created_at) == datetime.now(timezone.utc).date()
    ).scalar()
    emails_sent   = db.query(func.count(EmailLog.id)).scalar()
    return {
        "users": {"total": total_users, "pro": pro_users, "new_today": new_today, "free": total_users - pro_users},
        "beta": {"pending": beta_pending, "approved": beta_approved},
        "lifetime": lifetime,
        "work_days": {"total": total_days},
        "emails_sent": emails_sent,
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
            "grades": u.grades_list,
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
        "grades": user.grades_list,
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
async def approve_beta(
    user_id: int, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.beta_status = BetaStatus.APPROVED
    user.grade = UserGrade.BETA
    user.is_active = True
    user.beta_approved_at = datetime.now(timezone.utc)
    user.beta_approved_by = admin.id
    user.add_grade("beta")
    db.commit()
    first_name = user.full_name.split()[0] if user.full_name else "là"
    background_tasks.add_task(send_beta_approved_email, user.email, first_name)
    return {"message": f"Accès bêta accordé à {user.email}"}

@router.patch("/beta/{user_id}/reject")
async def reject_beta(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.beta_status = BetaStatus.REJECTED
    db.commit()
    return {"message": f"Demande refusée pour {user.email}"}

# ── MULTI-GRADES ──
class GradesRequest(BaseModel):
    grades: List[str]  # ["beta", "pro_lifetime", "admin"]

@router.patch("/users/{user_id}/grades")
async def set_grades(
    user_id: int, body: GradesRequest,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin.id and "admin" not in body.grades:
        raise HTTPException(status_code=400, detail="Impossible de retirer votre propre grade admin.")

    valid = {"user", "beta", "pro_lifetime", "admin"}
    grades = [g for g in body.grades if g in valid]
    user.set_grades(grades)

    if "admin" in grades:
        user.is_admin = True
    if "pro_lifetime" in grades:
        user.lifetime_pro = True
        user.beta_status = BetaStatus.APPROVED
    if "beta" in grades:
        user.beta_status = BetaStatus.APPROVED

    db.commit()
    return {"message": f"Grades mis à jour pour {user.email}", "grades": user.grades_list}

# Grade simple (compat)
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
    user.add_grade(body.grade)
    if body.grade == "pro_lifetime":
        user.lifetime_pro = True
        user.beta_status = BetaStatus.APPROVED
    if body.grade == "beta":
        user.beta_status = BetaStatus.APPROVED
    if body.grade == "admin":
        user.is_admin = True
        user.beta_status = BetaStatus.APPROVED
    db.commit()
    return {"message": f"Grade '{body.grade}' ajouté à {user.email}", "grades": user.grades_list}

# ── ACTIONS ──
@router.patch("/users/{user_id}/activate")
async def toggle_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de modifier votre propre compte.")
    user.is_active = not user.is_active
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
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.subscription_status = SubscriptionStatus.PRO
    user.sub_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    db.commit()
    return {"message": f"Accès Pro 30 jours accordé à {user.email}"}

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

# ── SUPER-ADMIN ──
def get_current_superadmin(current_user: User = Depends(get_current_admin)) -> User:
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Accès réservé au super-administrateur.")
    return current_user

@router.get("/users/{user_id}/reveal-ip")
async def reveal_ip(user_id: int, db: Session = Depends(get_db), superadmin: User = Depends(get_current_superadmin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    db.add(AuditLog(user_id=superadmin.id, action="ip_revealed", details=f"IPs de user_id:{user_id} ({user.email})"))
    db.commit()
    login_ips = db.query(AuditLog.ip_address, AuditLog.created_at).filter(
        AuditLog.user_id == user_id, AuditLog.action == "login_success"
    ).order_by(desc(AuditLog.created_at)).limit(20).all()
    return {"user_id": user_id, "email": user.email, "ips": [{"ip": ip, "at": at.isoformat()} for ip, at in login_ips if ip]}

# ── FEEDBACKS ──
@router.get("/feedbacks")
async def list_feedbacks(
    page: int = Query(1, ge=1), status: Optional[str] = None,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    from app.api.feedback import Feedback
    q = db.query(Feedback)
    if status:
        q = q.filter(Feedback.status == status)
    total = q.count()
    open_count = db.query(Feedback).filter(Feedback.status == "open").count()
    fbs = q.order_by(desc(Feedback.created_at)).offset((page-1)*20).limit(20).all()
    result = []
    for f in fbs:
        u = db.query(User).filter(User.id == f.user_id).first()
        result.append({
            "id": f.id, "user_email": u.email if u else "—", "user_name": u.full_name if u else "—",
            "subject": f.subject, "message": f.message, "status": f.status,
            "admin_reply": f.admin_reply, "created_at": f.created_at.isoformat(),
            "replied_at": f.replied_at.isoformat() if f.replied_at else None,
        })
    return {"total": total, "open_count": open_count, "page": page, "feedbacks": result}

class AdminReply(BaseModel):
    reply: str
    status: Optional[str] = "replied"

@router.patch("/feedbacks/{feedback_id}/reply")
async def admin_reply_feedback(
    feedback_id: int, body: AdminReply, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    from app.api.feedback import Feedback
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback introuvable.")
    fb.admin_reply = body.reply.strip()
    fb.admin_id = admin.id
    fb.status = body.status if body.status in ["replied", "closed"] else "replied"
    fb.replied_at = datetime.now(timezone.utc)
    db.commit()
    user = db.query(User).filter(User.id == fb.user_id).first()
    if user:
        first_name = user.full_name.split()[0] if user.full_name else "là"
        background_tasks.add_task(send_feedback_reply_email, user.email, first_name, fb.message, body.reply.strip())
    return {"message": "Réponse envoyée."}

@router.patch("/feedbacks/{feedback_id}/status")
async def set_feedback_status_admin(
    feedback_id: int, status: str,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    from app.api.feedback import Feedback
    if status not in ["open", "replied", "closed"]:
        raise HTTPException(status_code=400, detail="Statut invalide.")
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback introuvable.")
    fb.status = status
    db.commit()
    return {"message": f"Statut: {status}"}

# ── EMAILS ──
GRADE_LABELS = {
    "all": "tous les utilisateurs",
    "beta": "utilisateurs BETA",
    "pro": "abonnés PRO",
    "pro_lifetime": "PRO à vie",
    "admin": "administrateurs",
    "inactive": "comptes inactifs",
}

class EmailRequest(BaseModel):
    subject: str
    message: str
    target: str = "individual"       # individual | all | beta | pro | pro_lifetime | admin | inactive
    recipient_id: Optional[int] = None
    recipient_email: Optional[str] = None

@router.post("/emails/send")
async def send_admin_email(
    body: EmailRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    if not body.subject.strip() or not body.message.strip():
        raise HTTPException(status_code=400, detail="Sujet et message requis.")

    def build_html(name, msg):
        first = name.split()[0] if name else "là"
        return f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
            <div style="background:#1a1a2e;padding:30px;text-align:center;">
                <h1 style="color:#fff;margin:0;">TimePlan<span style="color:#4f8ef7;">.work</span></h1>
            </div>
            <div style="padding:30px;background:#f9f9f9;">
                <h2 style="color:#1a1a2e;">Bonjour {first},</h2>
                <div style="color:#444;line-height:1.7;font-size:15px;">{msg.replace(chr(10), '<br>')}</div>
            </div>
            <div style="background:#1a1a2e;padding:15px;text-align:center;">
                <p style="color:#888;font-size:12px;margin:0;">© 2025 TimePlan.work — 
                <a href="https://timeplanwork.com" style="color:#4f8ef7;">timeplanwork.com</a></p>
            </div>
        </div>"""

    if body.target == "individual":
        # Email individuel
        user = None
        if body.recipient_id:
            user = db.query(User).filter(User.id == body.recipient_id).first()
        elif body.recipient_email:
            user = db.query(User).filter(User.email == body.recipient_email.lower()).first()
        if not user:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        html = build_html(user.full_name or "", body.message)
        background_tasks.add_task(send_email, user.email, body.subject, html)
        db.add(EmailLog(
            sender_id=admin.id, recipient_id=user.id, recipient_email=user.email,
            subject=body.subject, body_preview=body.message[:300],
            target_group="individual", sent_count=1
        ))
        db.add(AuditLog(user_id=admin.id, action="email_sent",
            details=f"Email individuel -> {user.email} | sujet: {body.subject[:50]}"))
        db.commit()
        return {"message": f"Email envoyé à {user.email}", "sent_count": 1}

    else:
        # Email de groupe
        q = db.query(User).filter(User.is_active == True)
        if body.target == "beta":
            q = q.filter(User.beta_status == BetaStatus.APPROVED)
        elif body.target == "pro":
            q = q.filter(User.subscription_status == SubscriptionStatus.PRO)
        elif body.target == "pro_lifetime":
            q = q.filter(User.lifetime_pro == True)
        elif body.target == "admin":
            q = q.filter(User.is_admin == True)
        elif body.target == "inactive":
            q = db.query(User).filter(User.is_active == False)

        users = q.all()
        if not users:
            raise HTTPException(status_code=404, detail="Aucun utilisateur dans ce groupe.")

        sent = 0
        for u in users:
            html = build_html(u.full_name or "", body.message)
            background_tasks.add_task(send_email, u.email, body.subject, html)
            db.add(EmailLog(
                sender_id=admin.id, recipient_id=u.id, recipient_email=u.email,
                subject=body.subject, body_preview=body.message[:300],
                target_group=body.target, sent_count=1
            ))
            sent += 1

        db.add(AuditLog(user_id=admin.id, action="email_group_sent",
            details=f"Email groupe '{body.target}' -> {sent} destinataires | sujet: {body.subject[:50]}"))
        db.commit()
        return {"message": f"Email envoyé à {sent} utilisateur(s)", "sent_count": sent}

@router.get("/emails/history")
async def email_history(
    page: int = Query(1, ge=1),
    target: Optional[str] = None,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    q = db.query(EmailLog)
    if target:
        q = q.filter(EmailLog.target_group == target)
    total = q.count()
    logs = q.order_by(desc(EmailLog.created_at)).offset((page-1)*20).limit(20).all()
    result = []
    for l in logs:
        sender = db.query(User).filter(User.id == l.sender_id).first()
        result.append({
            "id": l.id,
            "sender": sender.email if sender else "—",
            "recipient_email": l.recipient_email,
            "subject": l.subject,
            "body_preview": l.body_preview,
            "target_group": l.target_group,
            "sent_count": l.sent_count,
            "status": l.status,
            "created_at": l.created_at.isoformat(),
        })
    return {"total": total, "page": page, "history": result}

@router.get("/emails/stats")
async def email_stats(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    total = db.query(func.count(EmailLog.id)).scalar()
    by_group = db.query(EmailLog.target_group, func.count(EmailLog.id)).group_by(EmailLog.target_group).all()
    return {
        "total": total,
        "by_group": [{"group": g, "count": c} for g, c in by_group]
    }

# ── EMAIL CUSTOM INDIVIDUEL (ancienne route gardée) ──
class CustomEmailRequest(BaseModel):
    subject: str
    message: str

@router.post("/users/{user_id}/send-email")
async def send_custom_email(
    user_id: int, body: CustomEmailRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    first_name = user.full_name.split()[0] if user.full_name else "là"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#1a1a2e;padding:30px;text-align:center;">
            <h1 style="color:#fff;margin:0;">TimePlan<span style="color:#4f8ef7;">.work</span></h1>
        </div>
        <div style="padding:30px;background:#f9f9f9;">
            <h2 style="color:#1a1a2e;">Bonjour {first_name},</h2>
            <div style="color:#444;line-height:1.7;font-size:15px;">{body.message.replace(chr(10), '<br>')}</div>
        </div>
        <div style="background:#1a1a2e;padding:15px;text-align:center;">
            <p style="color:#888;font-size:12px;margin:0;">© 2025 TimePlan.work</p>
        </div>
    </div>"""
    background_tasks.add_task(send_email, user.email, body.subject, html)
    db.add(EmailLog(
        sender_id=admin.id, recipient_id=user.id, recipient_email=user.email,
        subject=body.subject, body_preview=body.message[:300], target_group="individual"
    ))
    db.add(AuditLog(user_id=admin.id, action="email_sent",
        details=f"Email -> {user.email} | sujet: {body.subject[:50]}"))
    db.commit()
    return {"message": f"Email envoyé à {user.email}"}
