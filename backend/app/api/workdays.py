import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.models.models import User, WorkDay, DayType
from app.api.auth import get_current_user, require_pro
from app.core.database import get_db

router = APIRouter(prefix="/api/workdays", tags=["workdays"])
logger = logging.getLogger(__name__)

# ── SCHEMAS ──
class WorkDayCreate(BaseModel):
    date: str               # YYYY-MM-DD
    day_type: str           # work / saturday / economic_leave
    start_time: Optional[str] = None   # HH:MM
    end_time: Optional[str] = None     # HH:MM
    real_minutes: int = 0
    paid_minutes: int = 0
    gap_minutes: int = 0
    note: Optional[str] = None

class WorkDayBulk(BaseModel):
    days: list[WorkDayCreate]

# ── GET ALL ──
@router.get("/")
async def get_workdays(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    days = db.query(WorkDay).filter(WorkDay.user_id == current_user.id).order_by(WorkDay.date).all()
    return {"days": [{
        "id": d.id,
        "date": d.date,
        "day_type": d.day_type,
        "start_time": d.start_time,
        "end_time": d.end_time,
        "real_minutes": d.real_minutes,
        "paid_minutes": d.paid_minutes,
        "gap_minutes": d.gap_minutes,
        "note": d.note,
    } for d in days]}

# ── SYNC BULK (depuis localStorage) ──
@router.post("/sync")
async def sync_workdays(
    body: WorkDayBulk,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Synchronise toutes les journées depuis le localStorage."""
    type_map = {
        "travail": DayType.WORK,
        "work": DayType.WORK,
        "samedi": DayType.SATURDAY,
        "saturday": DayType.SATURDAY,
        "chomage": DayType.ECONOMIC_LEAVE,
        "economic_leave": DayType.ECONOMIC_LEAVE,
    }

    synced = 0
    for day in body.days:
        existing = db.query(WorkDay).filter(
            WorkDay.user_id == current_user.id,
            WorkDay.date == day.date
        ).first()

        day_type = type_map.get(day.day_type, DayType.WORK)

        if existing:
            existing.day_type = day_type
            existing.start_time = day.start_time
            existing.end_time = day.end_time
            existing.real_minutes = day.real_minutes
            existing.paid_minutes = day.paid_minutes
            existing.gap_minutes = day.gap_minutes
            existing.note = day.note
            existing.updated_at = datetime.now(timezone.utc)
        else:
            wd = WorkDay(
                user_id=current_user.id,
                date=day.date,
                day_type=day_type,
                start_time=day.start_time,
                end_time=day.end_time,
                real_minutes=day.real_minutes,
                paid_minutes=day.paid_minutes,
                gap_minutes=day.gap_minutes,
                note=day.note,
            )
            db.add(wd)
        synced += 1

    db.commit()
    logger.info(f"[SYNC] {current_user.email} — {synced} journées synchronisées")
    return {"message": f"{synced} journées synchronisées.", "synced": synced}

# ── ADD ONE ──
@router.post("/")
async def add_workday(
    body: WorkDayCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    type_map = {
        "travail": DayType.WORK, "work": DayType.WORK,
        "samedi": DayType.SATURDAY, "saturday": DayType.SATURDAY,
        "chomage": DayType.ECONOMIC_LEAVE, "economic_leave": DayType.ECONOMIC_LEAVE,
    }
    existing = db.query(WorkDay).filter(
        WorkDay.user_id == current_user.id, WorkDay.date == body.date
    ).first()

    if existing:
        existing.day_type = type_map.get(body.day_type, DayType.WORK)
        existing.start_time = body.start_time
        existing.end_time = body.end_time
        existing.real_minutes = body.real_minutes
        existing.paid_minutes = body.paid_minutes
        existing.gap_minutes = body.gap_minutes
        existing.note = body.note
        existing.updated_at = datetime.now(timezone.utc)
    else:
        wd = WorkDay(
            user_id=current_user.id,
            date=body.date,
            day_type=type_map.get(body.day_type, DayType.WORK),
            start_time=body.start_time,
            end_time=body.end_time,
            real_minutes=body.real_minutes,
            paid_minutes=body.paid_minutes,
            gap_minutes=body.gap_minutes,
            note=body.note,
        )
        db.add(wd)
    db.commit()
    return {"message": "Journée enregistrée."}

# ── DELETE ──
@router.delete("/{date}")
async def delete_workday(
    date: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wd = db.query(WorkDay).filter(
        WorkDay.user_id == current_user.id, WorkDay.date == date
    ).first()
    if not wd:
        raise HTTPException(status_code=404, detail="Journée introuvable.")
    db.delete(wd)
    db.commit()
    return {"message": "Journée supprimée."}

# ── EXPORT PDF (Pro uniquement) ──
@router.get("/export/pdf")
async def export_pdf(
    current_user: User = Depends(require_pro),
    db: Session = Depends(get_db)
):
    """Génère un PDF juridique des heures — réservé aux abonnés Pro."""
    from fastapi.responses import Response
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    import io

    days = db.query(WorkDay).filter(WorkDay.user_id == current_user.id).order_by(WorkDay.date).all()

    if not days:
        raise HTTPException(status_code=404, detail="Aucune journée enregistrée.")

    # Calculs
    travailles = [d for d in days if d.day_type != DayType.ECONOMIC_LEAVE]
    samedis = [d for d in days if d.day_type == DayType.SATURDAY]
    total_reelles = sum(d.real_minutes or 0 for d in travailles)
    total_payees = sum(d.paid_minutes or 0 for d in travailles)
    total_ecart = total_reelles - total_payees

    def min_to_str(m):
        sign = '+' if m >= 0 else '-'
        a = abs(m)
        return f"{sign}{a//60}h{a%60:02d}"

    def min_to_abs(m):
        return f"{m//60}h{m%60:02d}"

    # Génération PDF
    buffer = io.BytesIO()
    W, H = A4
    c = canvas.Canvas(buffer, pagesize=A4)

    BG = HexColor('#0b0e18')
    ACCENT = HexColor('#3d7fff')
    GREEN = HexColor('#00c875')
    TEXT = HexColor('#e8edf8')
    MUTED = HexColor('#6b7799')
    WHITE = HexColor('#ffffff')
    RED = HexColor('#ff5f57')
    BORDER = HexColor('#252d4a')

    # Fond
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # Bande top
    c.setFillColor(ACCENT)
    c.rect(0, H-3*mm, W, 3*mm, fill=1, stroke=0)

    # En-tête
    c.setFillColor(ACCENT)
    c.roundRect(20*mm, H-22*mm, 10*mm, 10*mm, 2, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(22.5*mm, H-15*mm, 'T')
    c.setFillColor(TEXT)
    c.setFont('Helvetica-Bold', 16)
    c.drawString(33*mm, H-14.5*mm, 'TimePlan')
    c.setFillColor(ACCENT)
    c.drawString(67*mm, H-14.5*mm, '.work')

    # Titre rapport
    c.setFillColor(TEXT)
    c.setFont('Helvetica-Bold', 18)
    c.drawRightString(W-20*mm, H-14.5*mm, 'RAPPORT JURIDIQUE')

    # Sous-titre
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 9)
    c.drawRightString(W-20*mm, H-20*mm, f'Généré le {datetime.now().strftime("%d/%m/%Y")} — Confidentiel')

    # Séparateur
    c.setStrokeColor(BORDER)
    c.setLineWidth(1)
    c.line(20*mm, H-26*mm, W-20*mm, H-26*mm)

    # Infos utilisateur
    c.setFillColor(HexColor('#131728'))
    c.roundRect(20*mm, H-46*mm, W-40*mm, 16*mm, 4, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawString(24*mm, H-33*mm, 'TRAVAILLEUR')
    c.drawString(90*mm, H-33*mm, 'EMAIL')
    c.drawString(155*mm, H-33*mm, 'PÉRIODE')
    c.setFillColor(TEXT)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(24*mm, H-38*mm, current_user.full_name or 'N/A')
    c.drawString(90*mm, H-38*mm, current_user.email)
    if days:
        periode = f"{days[0].date} → {days[-1].date}"
        c.drawString(155*mm, H-38*mm, periode)

    # Stats résumé
    stats_y = H-58*mm
    stats = [
        ('Jours travaillés', str(len(travailles)), TEXT),
        ('Samedis non payés', str(len(samedis)), HexColor('#ff6b35')),
        ('Heures réelles', min_to_abs(total_reelles), ACCENT),
        ('Heures payées', min_to_abs(total_payees), TEXT),
        ('ÉCART TOTAL', min_to_str(total_ecart), GREEN if total_ecart >= 0 else RED),
    ]
    col_w = (W - 40*mm) / len(stats)
    for i, (label, val, col) in enumerate(stats):
        x = 20*mm + i * col_w
        c.setFillColor(HexColor('#131728'))
        c.roundRect(x, stats_y-14*mm, col_w-3*mm, 16*mm, 3, fill=1, stroke=0)
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7)
        c.drawString(x+3*mm, stats_y+0.5*mm, label.upper())
        c.setFillColor(col)
        c.setFont('Helvetica-Bold', 13)
        c.drawString(x+3*mm, stats_y-7*mm, val)

    # Tableau des journées
    table_y = stats_y - 22*mm
    c.setFillColor(MUTED)
    c.setFont('Helvetica-Bold', 7)
    headers = ['DATE', 'TYPE', 'DÉBUT', 'FIN', 'RÉEL', 'PAYÉ', 'ÉCART', 'NOTE']
    col_widths = [28, 22, 16, 16, 16, 16, 18, 38]
    x = 20*mm
    for header, cw in zip(headers, col_widths):
        c.drawString(x+2*mm, table_y, header)
        x += cw*mm

    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(20*mm, table_y-2*mm, W-20*mm, table_y-2*mm)

    row_h = 6*mm
    for i, d in enumerate(days):
        ry = table_y - (i+1)*row_h - 2*mm
        if ry < 20*mm:
            c.showPage()
            c.setFillColor(BG)
            c.rect(0, 0, W, H, fill=1, stroke=0)
            table_y = H - 20*mm
            ry = table_y - row_h

        # Alternance fond
        if i % 2 == 0:
            c.setFillColor(HexColor('#0f1420'))
            c.rect(20*mm, ry-1*mm, W-40*mm, row_h, fill=1, stroke=0)

        type_label = {'work': 'Travail', 'saturday': 'Samedi', 'economic_leave': 'Chômage'}.get(str(d.day_type), str(d.day_type))
        ecart = d.gap_minutes or 0
        ecart_col = GREEN if ecart > 0 else RED if ecart < 0 else MUTED

        row_data = [
            (d.date, TEXT),
            (type_label, MUTED),
            (d.start_time or '—', TEXT),
            (d.end_time or '—', TEXT),
            (min_to_abs(d.real_minutes or 0), TEXT),
            (min_to_abs(d.paid_minutes or 0), TEXT),
            (min_to_str(ecart), ecart_col),
            ((d.note or '')[:20], MUTED),
        ]

        x = 20*mm
        for (val, col), cw in zip(row_data, col_widths):
            c.setFillColor(col)
            c.setFont('Helvetica', 7)
            c.drawString(x+2*mm, ry+1.5*mm, str(val))
            x += cw*mm

    # Footer
    c.setFillColor(ACCENT)
    c.rect(0, 0, W, 2.5*mm, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 7)
    c.drawCentredString(W/2, 6*mm, 'Document généré par TimePlan.work — À utiliser comme pièce complémentaire dans un dossier juridique')

    c.save()
    buffer.seek(0)

    filename = f"TimePlan_Rapport_{current_user.full_name or 'utilisateur'}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return Response(
        content=buffer.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
