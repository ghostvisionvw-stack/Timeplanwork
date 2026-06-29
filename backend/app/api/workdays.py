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
    date: str
    day_type: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
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
        "id": d.id, "date": d.date, "day_type": d.day_type,
        "start_time": d.start_time, "end_time": d.end_time,
        "real_minutes": d.real_minutes, "paid_minutes": d.paid_minutes,
        "gap_minutes": d.gap_minutes, "note": d.note,
    } for d in days]}

# ── SYNC BULK ──
@router.post("/sync")
async def sync_workdays(
    body: WorkDayBulk,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    type_map = {
        "travail": DayType.WORK, "work": DayType.WORK,
        "samedi": DayType.SATURDAY, "saturday": DayType.SATURDAY,
        "chomage": DayType.ECONOMIC_LEAVE, "economic_leave": DayType.ECONOMIC_LEAVE,
    }
    synced = 0
    for day in body.days:
        existing = db.query(WorkDay).filter(
            WorkDay.user_id == current_user.id, WorkDay.date == day.date
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
            db.add(WorkDay(
                user_id=current_user.id, date=day.date, day_type=day_type,
                start_time=day.start_time, end_time=day.end_time,
                real_minutes=day.real_minutes, paid_minutes=day.paid_minutes,
                gap_minutes=day.gap_minutes, note=day.note,
            ))
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
        existing.start_time = body.start_time; existing.end_time = body.end_time
        existing.real_minutes = body.real_minutes; existing.paid_minutes = body.paid_minutes
        existing.gap_minutes = body.gap_minutes; existing.note = body.note
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(WorkDay(
            user_id=current_user.id, date=body.date,
            day_type=type_map.get(body.day_type, DayType.WORK),
            start_time=body.start_time, end_time=body.end_time,
            real_minutes=body.real_minutes, paid_minutes=body.paid_minutes,
            gap_minutes=body.gap_minutes, note=body.note,
        ))
    db.commit()
    return {"message": "Journée enregistrée."}

# ── EXPORT PDF ──
@router.get("/export/pdf")
async def export_pdf(
    mode: Optional[str] = None,
    mois: Optional[str] = None,
    current_user: User = Depends(require_pro),
    db: Session = Depends(get_db)
):
    from fastapi.responses import Response
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    import io

    days_all = db.query(WorkDay).filter(WorkDay.user_id == current_user.id).order_by(WorkDay.date).all()
    if not days_all:
        raise HTTPException(status_code=404, detail="Aucune journée enregistrée.")

    # Filtrer par mois si demandé (ex: mois=2025-10)
    if mois:
        days = [d for d in days_all if d.date[:7] == mois]
        if not days:
            raise HTTPException(status_code=404, detail=f"Aucune journée pour {mois}.")
    else:
        days = days_all

    # ── Labels lisibles ──
    TYPE_LABELS = {
        DayType.WORK: "Travail",
        DayType.SATURDAY: "Samedi",
        DayType.ECONOMIC_LEAVE: "Absence",
        "work": "Travail",
        "saturday": "Samedi",
        "economic_leave": "Absence",
    }

    travailles = [d for d in days if d.day_type != DayType.ECONOMIC_LEAVE]
    samedis    = [d for d in days if d.day_type == DayType.SATURDAY]
    total_reelles = sum(d.real_minutes or 0 for d in travailles)
    total_payees  = sum(d.paid_minutes or 0 for d in travailles)
    total_ecart   = total_reelles - total_payees

    # Regrouper par mois pour récap
    mois_data = {}
    for d in travailles:
        mk = d.date[:7]
        if mk not in mois_data:
            mois_data[mk] = {"reelles": 0, "payees": 0, "jours": 0}
        mois_data[mk]["reelles"] += d.real_minutes or 0
        mois_data[mk]["payees"]  += d.paid_minutes or 0
        mois_data[mk]["jours"]   += 1

    def fmt_mois(mk):
        mois = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
        y, m = mk.split("-")
        return f"{mois[int(m)-1]} {y}"

    def mts(m):
        s = '+' if m >= 0 else '-'
        a = abs(m)
        return f"{s}{a//60}h{a%60:02d}"

    def mta(m):
        if m is None: return "—"
        return f"{m//60}h{m%60:02d}"

    # ── COULEURS ──
    BG     = HexColor('#0b0e18')
    BG2    = HexColor('#131728')
    BG3    = HexColor('#1c2136')
    ACCENT = HexColor('#3d7fff')
    GREEN  = HexColor('#00c875')
    TEXT   = HexColor('#e8edf8')
    MUTED  = HexColor('#6b7799')
    WHITE  = HexColor('#ffffff')
    RED    = HexColor('#ff5f57')
    ORANGE = HexColor('#ff6b35')
    BORDER = HexColor('#252d4a')

    buffer = io.BytesIO()
    W, H = A4
    c = canvas.Canvas(buffer, pagesize=A4)
    page_num = [0]

    # ══════════════════════════════════════
    # MODE RÉCAP MENSUEL (une page par mois)
    # ══════════════════════════════════════
    if mode == 'monthly':
        def new_page_m():
            page_num[0] += 1
            c.setFillColor(BG); c.rect(0, 0, W, H, fill=1, stroke=0)
            c.setFillColor(ACCENT); c.rect(0, H-3*mm, W, 3*mm, fill=1, stroke=0)
            c.setFillColor(ACCENT); c.rect(0, 0, W, 2.5*mm, fill=1, stroke=0)
            c.setFillColor(MUTED); c.setFont('Helvetica', 7)
            c.drawString(20*mm, 6*mm, 'TimePlan.work — Récapitulatif mensuel confidentiel')
            c.drawRightString(W-20*mm, 6*mm, f'Page {page_num[0]}')

        # Page de garde synthèse
        new_page_m()
        c.setFillColor(WHITE); c.setFont('Helvetica-Bold', 22)
        c.drawString(20*mm, H-25*mm, 'Récapitulatif Mensuel')
        c.setFillColor(ACCENT); c.setFont('Helvetica', 10)
        c.drawString(20*mm, H-33*mm, f'Généré le {datetime.now().strftime("%d/%m/%Y")} — {current_user.full_name or current_user.email}')
        c.setStrokeColor(ACCENT); c.setLineWidth(1.5)
        c.line(20*mm, H-37*mm, W-20*mm, H-37*mm)

        # Tableau synthèse tous les mois
        ty = H-48*mm
        c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 7)
        c.drawString(20*mm, ty+3*mm, '— SYNTHÈSE PAR MOIS')
        c.setFillColor(BG3); c.rect(20*mm, ty-7*mm, W-40*mm, 8*mm, fill=1, stroke=0)
        heads_m = [('MOIS', 45), ('JOURS', 22), ('HEURES RÉELLES', 38), ('HEURES DÉCLARÉES', 38), ('ÉCART MOIS', 28), ('CUMUL', 28)]
        hx = 20*mm
        for h, hw in heads_m:
            c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 6.5)
            c.drawString(hx+2*mm, ty-3*mm, h); hx += hw*mm

        ry2 = ty - 8*mm
        cumul = 0
        for mk, md in sorted(mois_data.items()):
            em = md['reelles'] - md['payees']; cumul += em
            ec = GREEN if em > 0 else RED if em < 0 else MUTED
            cc = GREEN if cumul > 0 else RED
            c.setFillColor(HexColor('#0f1420')); c.rect(20*mm, ry2-5*mm, W-40*mm, 6*mm, fill=1, stroke=0)
            vx = 20*mm
            for val, col, vw in [(fmt_mois(mk), TEXT, 45), (str(md['jours']), TEXT, 22), (mta(md['reelles']), ACCENT, 38), (mta(md['payees']), TEXT, 38), (mts(em), ec, 28), (mts(cumul), cc, 28)]:
                c.setFillColor(col); c.setFont('Helvetica', 7.5); c.drawString(vx+2*mm, ry2-1.5*mm, val); vx += vw*mm
            ry2 -= 7*mm

        c.setStrokeColor(BORDER); c.setLineWidth(0.5); c.line(20*mm, ry2+1*mm, W-20*mm, ry2+1*mm)
        c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 8); c.drawString(22*mm, ry2-5*mm, 'TOTAL GÉNÉRAL')
        c.setFillColor(GREEN if total_ecart >= 0 else RED); c.setFont('Helvetica-Bold', 11)
        c.drawString(20*mm+(45+22+38+38)*mm+2*mm, ry2-5*mm, mts(total_ecart))

        # Une page par mois avec détail des journées
        for mk, md in sorted(mois_data.items()):
            jours_mois = [d for d in days if d.date[:7] == mk]
            c.showPage(); new_page_m()
            c.setFillColor(ACCENT); c.setFont('Helvetica-Bold', 14)
            c.drawString(20*mm, H-18*mm, fmt_mois(mk))
            ecart_m = md['reelles'] - md['payees']
            ec = GREEN if ecart_m > 0 else RED
            c.setFillColor(ec); c.setFont('Helvetica-Bold', 10)
            c.drawRightString(W-20*mm, H-18*mm, f'{md["jours"]} jours | {mta(md["reelles"])} réelles | {mta(md["payees"])} déclarées | {mts(ecart_m)}')
            c.setStrokeColor(BORDER); c.setLineWidth(0.8); c.line(20*mm, H-22*mm, W-20*mm, H-22*mm)

            ht = H-30*mm
            c.setFillColor(BG3); c.rect(20*mm, ht-6*mm, W-40*mm, 7*mm, fill=1, stroke=0)
            for hname, hw in [('DATE', 30),('TYPE',22),('DÉBUT',18),('FIN',18),('RÉEL',20),('DÉCLARÉ',22),('ÉCART',20),('NOTE',0)]:
                c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 6.5); c.drawString(20*mm+sum(v[1] for v in [('DATE',30),('TYPE',22),('DÉBUT',18),('FIN',18),('RÉEL',20),('DÉCLARÉ',22),('ÉCART',20),('NOTE',0)][:([('DATE',30),('TYPE',22),('DÉBUT',18),('FIN',18),('RÉEL',20),('DÉCLARÉ',22),('ÉCART',20),('NOTE',0)].index((hname,hw)))])*mm+2*mm, ht-2.5*mm, hname)

            ry3 = ht - 7*mm
            for i2, d in enumerate(jours_mois):
                if i2 % 2 == 0: c.setFillColor(HexColor('#0f1420')); c.rect(20*mm, ry3-5*mm, W-40*mm, 6*mm, fill=1, stroke=0)
                type_label = TYPE_LABELS.get(d.day_type, str(d.day_type))
                ecart = d.gap_minutes or 0; ec2 = GREEN if ecart > 0 else RED if ecart < 0 else MUTED
                vx = 20*mm
                for val, col, vw in [(d.date, TEXT,30),(type_label,MUTED,22),(d.start_time or '—',TEXT,18),(d.end_time or '—',TEXT,18),(mta(d.real_minutes or 0),ACCENT,20),(mta(d.paid_minutes or 0),TEXT,22),(mts(ecart),ec2,20),((d.note or '')[:30],MUTED,0)]:
                    c.setFillColor(col); c.setFont('Helvetica', 7); c.drawString(vx+2*mm, ry3-1.5*mm, str(val)); vx += vw*mm if vw else 0
                ry3 -= 6*mm

            # Total du mois
            c.setStrokeColor(ACCENT); c.setLineWidth(1); c.line(20*mm, ry3, W-20*mm, ry3); ry3 -= 6*mm
            c.setFillColor(BG2); c.roundRect(20*mm, ry3-8*mm, W-40*mm, 9*mm, 3, fill=1, stroke=0)
            c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 8)
            c.drawString(24*mm, ry3-4*mm, f'TOTAL {fmt_mois(mk).upper()} : {md["jours"]} jours | {mta(md["reelles"])} réelles | {mta(md["payees"])} déclarées')
            c.setFillColor(GREEN if ecart_m >= 0 else RED); c.setFont('Helvetica-Bold', 10)
            c.drawRightString(W-24*mm, ry3-4.5*mm, f'ÉCART : {mts(ecart_m)}')

        c.save(); buffer.seek(0)
        nom = (current_user.full_name or 'utilisateur').replace(' ', '_')
        filename = f"TimePlan_RecapMensuel_{nom}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return Response(content=buffer.read(), media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"})

    # ══════════════════════════
    # MODE JOUR PAR JOUR (défaut)
    # ══════════════════════════
    def new_page(titre_suite=False):
        page_num[0] += 1
        c.setFillColor(BG)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        # Bande top
        c.setFillColor(ACCENT)
        c.rect(0, H-3*mm, W, 3*mm, fill=1, stroke=0)
        # Bande bas
        c.setFillColor(ACCENT)
        c.rect(0, 0, W, 2.5*mm, fill=1, stroke=0)
        # Footer
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7)
        c.drawString(20*mm, 6*mm, 'TimePlan.work — Document confidentiel à usage juridique uniquement')
        c.drawRightString(W-20*mm, 6*mm, f'Page {page_num[0]}')
        if titre_suite:
            c.setFillColor(MUTED)
            c.setFont('Helvetica', 8)
            c.drawString(20*mm, H-10*mm, '(suite)')

    # ════════════════════════════
    # PAGE 1 — PAGE DE GARDE
    # ════════════════════════════
    new_page()

    # Logo
    c.setFillColor(ACCENT)
    c.roundRect(20*mm, H-24*mm, 11*mm, 11*mm, 2, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont('Helvetica-Bold', 13)
    c.drawString(23*mm, H-16.5*mm, 'T')
    c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 18)
    c.drawString(34*mm, H-16*mm, 'TimePlan')
    c.setFillColor(ACCENT)
    c.drawString(74*mm, H-16*mm, '.work')
    c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 18)
    c.drawRightString(W-20*mm, H-16*mm, 'RAPPORT DES HEURES')
    c.setFillColor(MUTED); c.setFont('Helvetica', 9)
    c.drawRightString(W-20*mm, H-22*mm, f'Généré le {datetime.now().strftime("%d/%m/%Y à %H:%M")} — CONFIDENTIEL')

    c.setStrokeColor(BORDER); c.setLineWidth(1)
    c.line(20*mm, H-28*mm, W-20*mm, H-28*mm)

    # Bloc infos travailleur
    c.setFillColor(BG2)
    c.roundRect(20*mm, H-68*mm, W-40*mm, 36*mm, 4, fill=1, stroke=0)
    c.setStrokeColor(ACCENT); c.setLineWidth(2)
    c.line(20*mm, H-68*mm, 20*mm, H-32*mm)

    fields = [
        ('TRAVAILLEUR', current_user.full_name or '—', 20*mm),
        ('EMAIL', current_user.email, 20*mm),
        ('PÉRIODE COUVERTE', f"{days[0].date} → {days[-1].date}", 20*mm),
        ('CONVENTION COLLECTIVE', current_user.collective_agreement or 'Non spécifiée', 20*mm),
    ]
    fy = H-38*mm
    for i, (lbl, val, _) in enumerate(fields):
        col = 0 if i < 2 else 1
        row = i % 2
        x = 26*mm + col*85*mm
        y = fy - row*14*mm
        c.setFillColor(MUTED); c.setFont('Helvetica', 7)
        c.drawString(x, y, lbl)
        c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 9)
        c.drawString(x, y-5*mm, val)

    # Stats résumé
    sy = H-82*mm
    c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 7)
    c.drawString(20*mm, sy+3*mm, '— RÉSUMÉ')

    stats = [
        ('Jours travaillés', str(len(travailles)), TEXT),
        ('Jours spéciaux', str(len(samedis)), ORANGE),
        ('Heures réelles', mta(total_reelles), ACCENT),
        ('Heures déclarées', mta(total_payees), TEXT),
        ('ÉCART TOTAL', mts(total_ecart), GREEN if total_ecart >= 0 else RED),
    ]
    col_w = (W - 40*mm) / len(stats)
    for i, (lbl, val, col) in enumerate(stats):
        x = 20*mm + i*col_w
        c.setFillColor(BG3)
        c.roundRect(x, sy-18*mm, col_w-3*mm, 18*mm, 3, fill=1, stroke=0)
        if i == 4:
            c.setStrokeColor(GREEN if total_ecart >= 0 else RED)
            c.setLineWidth(1)
            c.roundRect(x, sy-18*mm, col_w-3*mm, 18*mm, 3, fill=0, stroke=1)
        c.setFillColor(MUTED); c.setFont('Helvetica', 6.5)
        c.drawString(x+3*mm, sy-3*mm, lbl.upper())
        c.setFillColor(col); c.setFont('Helvetica-Bold', 14)
        c.drawString(x+3*mm, sy-12*mm, val)

    # Récap mensuel
    ry = sy - 28*mm
    c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 7)
    c.drawString(20*mm, ry+3*mm, '— RÉCAPITULATIF PAR MOIS')

    # Headers
    c.setFillColor(BG3)
    c.rect(20*mm, ry-6*mm, W-40*mm, 7*mm, fill=1, stroke=0)
    heads = [('MOIS', 30), ('JOURS', 20), ('HEURES RÉELLES', 35), ('HEURES DÉCLARÉES', 35), ('ÉCART', 25), ('SOLDE', 30)]
    hx = 20*mm
    for h, hw in heads:
        c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 6.5)
        c.drawString(hx+2*mm, ry-2.5*mm, h)
        hx += hw*mm

    row_ry = ry - 7*mm
    total_ecart_cumul = 0
    for mk, md in sorted(mois_data.items()):
        ecart_m = md["reelles"] - md["payees"]
        total_ecart_cumul += ecart_m
        ecart_col = GREEN if ecart_m > 0 else RED if ecart_m < 0 else MUTED
        cumul_col = GREEN if total_ecart_cumul > 0 else RED

        c.setFillColor(HexColor('#0f1420'))
        c.rect(20*mm, row_ry-5*mm, W-40*mm, 6*mm, fill=1, stroke=0)

        vals = [
            (fmt_mois(mk), TEXT, 30),
            (str(md["jours"]), TEXT, 20),
            (mta(md["reelles"]), ACCENT, 35),
            (mta(md["payees"]), TEXT, 35),
            (mts(ecart_m), ecart_col, 25),
            (mts(total_ecart_cumul), cumul_col, 30),
        ]
        vx = 20*mm
        for val, col, vw in vals:
            c.setFillColor(col); c.setFont('Helvetica', 7)
            c.drawString(vx+2*mm, row_ry-1.5*mm, str(val))
            vx += vw*mm
        row_ry -= 7*mm

    # Ligne total
    c.setStrokeColor(BORDER); c.setLineWidth(0.5)
    c.line(20*mm, row_ry+1*mm, W-20*mm, row_ry+1*mm)
    c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 7.5)
    c.drawString(22*mm, row_ry-4*mm, 'TOTAL GÉNÉRAL')
    c.setFillColor(GREEN if total_ecart >= 0 else RED); c.setFont('Helvetica-Bold', 9)
    ecart_x = 20*mm + (30+20+35+35)*mm
    c.drawString(ecart_x+2*mm, row_ry-4*mm, mts(total_ecart))

    # Note juridique bas de page de garde
    note_y = row_ry - 16*mm
    c.setFillColor(BG2)
    c.roundRect(20*mm, note_y-14*mm, W-40*mm, 14*mm, 3, fill=1, stroke=0)
    c.setStrokeColor(ORANGE); c.setLineWidth(1.5)
    c.line(20*mm, note_y-14*mm, 20*mm, note_y)
    c.setFillColor(ORANGE); c.setFont('Helvetica-Bold', 7)
    c.drawString(24*mm, note_y-5*mm, 'AVERTISSEMENT LÉGAL')
    c.setFillColor(MUTED); c.setFont('Helvetica', 7)
    c.drawString(24*mm, note_y-10*mm,
        'Ce document a valeur probatoire complémentaire. Il doit être accompagné de vos fiches de paie '
        'et tout autre justificatif. Consultez un avocat spécialisé en droit du travail.')

    # ════════════════════════════
    # PAGE 2+ — DÉTAIL DES JOURNÉES
    # ════════════════════════════
    c.showPage()
    new_page()

    # Titre page 2
    c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 13)
    c.drawString(20*mm, H-16*mm, 'Détail des journées')
    c.setFillColor(MUTED); c.setFont('Helvetica', 8)
    c.drawRightString(W-20*mm, H-16*mm, f'{len(days)} journées enregistrées')

    c.setStrokeColor(BORDER); c.setLineWidth(0.8)
    c.line(20*mm, H-20*mm, W-20*mm, H-20*mm)

    # Headers tableau
    table_y = H-26*mm
    headers = [('DATE', 26), ('TYPE', 22), ('DÉBUT', 16), ('FIN', 16), ('RÉEL', 16), ('DÉCLARÉ', 18), ('ÉCART', 18), ('NOTE', 0)]
    col_widths = [d[1] for d in headers]

    def draw_table_header(ty):
        c.setFillColor(BG3)
        c.rect(20*mm, ty-6*mm, W-40*mm, 7*mm, fill=1, stroke=0)
        hx = 20*mm
        for hname, hw in headers:
            c.setFillColor(MUTED); c.setFont('Helvetica-Bold', 6.5)
            c.drawString(hx+2*mm, ty-2.5*mm, hname)
            hx += hw*mm if hw else 0

    draw_table_header(table_y)
    row_y = table_y - 7*mm
    mois_courant = ''

    for i, d in enumerate(days):
        # Nouvelle page si nécessaire
        if row_y < 20*mm:
            c.showPage()
            new_page(titre_suite=True)
            table_y = H-20*mm
            draw_table_header(table_y)
            row_y = table_y - 7*mm

        # Séparateur de mois
        mk = d.date[:7]
        if mk != mois_courant:
            if mois_courant:
                row_y -= 2*mm
            c.setFillColor(HexColor('#0d1a3a'))
            c.rect(20*mm, row_y-5*mm, W-40*mm, 6*mm, fill=1, stroke=0)
            c.setFillColor(ACCENT); c.setFont('Helvetica-Bold', 7.5)
            c.drawString(22*mm, row_y-1.5*mm, f'── {fmt_mois(mk)} ──')

            # Mini récap mois
            if mk in mois_data:
                md = mois_data[mk]
                ecart_m = md["reelles"] - md["payees"]
                ecart_col = GREEN if ecart_m > 0 else RED
                c.setFillColor(ecart_col); c.setFont('Helvetica-Bold', 6.5)
                c.drawRightString(W-22*mm, row_y-1.5*mm,
                    f'{md["jours"]}j | {mta(md["reelles"])} réelles | {mta(md["payees"])} déclarées | écart : {mts(ecart_m)}')

            mois_courant = mk
            row_y -= 6*mm

        # Alternance
        if i % 2 == 0:
            c.setFillColor(HexColor('#0f1420'))
            c.rect(20*mm, row_y-5*mm, W-40*mm, 6*mm, fill=1, stroke=0)

        type_label = TYPE_LABELS.get(d.day_type, str(d.day_type).replace('DayType.',''))
        ecart = d.gap_minutes or 0
        ecart_col = GREEN if ecart > 0 else RED if ecart < 0 else MUTED

        # Note tronquée
        note = (d.note or '')[:25]

        row_vals = [
            (d.date, TEXT, 26),
            (type_label, MUTED if type_label == 'Travail' else ORANGE, 22),
            (d.start_time or '—', TEXT, 16),
            (d.end_time or '—', TEXT, 16),
            (mta(d.real_minutes or 0), ACCENT, 16),
            (mta(d.paid_minutes or 0), TEXT, 18),
            (mts(ecart), ecart_col, 18),
            (note, MUTED, 0),
        ]
        rx = 20*mm
        for val, col, rw in row_vals:
            c.setFillColor(col); c.setFont('Helvetica', 7)
            c.drawString(rx+2*mm, row_y-1.5*mm, str(val))
            rx += rw*mm if rw else 0
        row_y -= 6*mm

    # Ligne total finale
    c.setStrokeColor(ACCENT); c.setLineWidth(1)
    c.line(20*mm, row_y, W-20*mm, row_y)
    row_y -= 6*mm
    c.setFillColor(BG2)
    c.roundRect(20*mm, row_y-8*mm, W-40*mm, 9*mm, 3, fill=1, stroke=0)
    c.setFillColor(TEXT); c.setFont('Helvetica-Bold', 8)
    c.drawString(24*mm, row_y-4*mm, f'TOTAL : {len(travailles)} jours | {mta(total_reelles)} réelles | {mta(total_payees)} déclarées')
    c.setFillColor(GREEN if total_ecart >= 0 else RED)
    c.setFont('Helvetica-Bold', 10)
    c.drawRightString(W-24*mm, row_y-4.5*mm, f'ÉCART : {mts(total_ecart)}')

    c.save()
    buffer.seek(0)

    nom = (current_user.full_name or 'utilisateur').replace(' ', '_')
    filename = f"TimePlan_Rapport_{nom}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return Response(
        content=buffer.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ── DELETE (doit être APRÈS /export/pdf pour éviter conflit de route) ──
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
    db.delete(wd); db.commit()
    logger.info(f"[DELETE] {current_user.email} — journée {date} supprimée")
    return {"message": "Journée supprimée."}