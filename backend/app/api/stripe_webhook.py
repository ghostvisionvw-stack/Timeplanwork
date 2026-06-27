import logging
import stripe
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, Header, Depends
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.models import User, SubscriptionStatus, AuditLog
from app.core.database import get_db, get_db_sync
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/stripe", tags=["stripe"])
logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

# ── CRÉER SESSION CHECKOUT ──
@router.post("/create-checkout")
async def create_checkout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Crée une session Stripe Checkout pour l'abonnement Pro."""
    try:
        # Créer ou récupérer le customer Stripe
        if not current_user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.full_name,
                metadata={"user_id": str(current_user.id)}
            )
            current_user.stripe_customer_id = customer.id
            db.commit()
        
        base_url = str(request.base_url).rstrip('/')
        
        session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id,
            client_reference_id=str(current_user.id),
            payment_method_types=["card"],
            line_items=[{
                "price": settings.STRIPE_PRICE_PRO_MONTHLY,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{base_url}/dashboard?success=1",
            cancel_url=f"{base_url}/dashboard?cancelled=1",
            locale="fr",
            subscription_data={
                "metadata": {"user_id": str(current_user.id)}
            }
        )
        
        logger.info(f"[CHECKOUT] Session créée pour {current_user.email}")
        return {"checkout_url": session.url}
        
    except stripe.error.StripeError as e:
        logger.error(f"[CHECKOUT_ERROR] {current_user.email}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ── CRÉER SESSION PORTAIL CLIENT ──
@router.post("/create-portal")
async def create_portal(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Crée une session Stripe Customer Portal pour gérer l'abonnement."""
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="Aucun abonnement actif.")
    
    try:
        base_url = str(request.base_url).rstrip('/')
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=f"{base_url}/dashboard",
        )
        return {"portal_url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ── WEBHOOK STRIPE ──
@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature")
):
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Signature manquante.")

    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("Webhook Stripe — signature invalide")
        raise HTTPException(status_code=400, detail="Signature invalide.")
    except Exception as e:
        logger.error(f"Webhook Stripe — erreur parsing: {e}")
        raise HTTPException(status_code=400, detail="Payload invalide.")

    logger.info(f"[WEBHOOK] {event['type']}")

    db = next(get_db_sync())
    try:
        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "checkout.session.completed":
            _handle_checkout_completed(db, data)
        elif event_type == "customer.subscription.created":
            _handle_sub_created(db, data)
        elif event_type == "customer.subscription.updated":
            _handle_sub_updated(db, data)
        elif event_type == "customer.subscription.deleted":
            _handle_sub_deleted(db, data)
        elif event_type == "invoice.payment_succeeded":
            _handle_payment_succeeded(db, data)
        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(db, data)

        db.commit()
    except Exception as e:
        logger.error(f"[WEBHOOK_ERROR] {event_type}: {e}")
        db.rollback()
        return {"status": "error_logged"}
    finally:
        db.close()

    return {"status": "ok"}


def _get_user_by_stripe_id(db: Session, customer_id: str):
    return db.query(User).filter(User.stripe_customer_id == customer_id).first()

def _add_audit(db: Session, user_id: int, action: str, details: str = None):
    db.add(AuditLog(user_id=user_id, action=action, details=details))

def _handle_checkout_completed(db: Session, session: dict):
    customer_id = session.get("customer")
    client_ref  = session.get("client_reference_id")
    if not client_ref:
        return
    user = db.query(User).filter(User.id == int(client_ref)).first()
    if user and customer_id:
        user.stripe_customer_id = customer_id
        logger.info(f"[STRIPE] Customer lié: user {user.id} → {customer_id}")

def _handle_sub_created(db: Session, subscription: dict):
    user = _get_user_by_stripe_id(db, subscription["customer"])
    if not user:
        return
    user.subscription_status = SubscriptionStatus.PRO
    user.stripe_sub_id = subscription["id"]
    user.sub_expires_at = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc)
    _add_audit(db, user.id, "subscription_created", f"sub_id={subscription['id']}")
    logger.info(f"[STRIPE] Pro activé: {user.email}")

def _handle_sub_updated(db: Session, subscription: dict):
    user = _get_user_by_stripe_id(db, subscription["customer"])
    if not user:
        return
    status_map = {
        "active":   SubscriptionStatus.PRO,
        "trialing": SubscriptionStatus.PRO,
        "past_due": SubscriptionStatus.PRO,
        "canceled": SubscriptionStatus.CANCELLED,
        "unpaid":   SubscriptionStatus.FREE,
    }
    user.subscription_status = status_map.get(subscription["status"], SubscriptionStatus.FREE)
    user.sub_expires_at = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc)
    _add_audit(db, user.id, "subscription_updated", f"status={subscription['status']}")

def _handle_sub_deleted(db: Session, subscription: dict):
    user = _get_user_by_stripe_id(db, subscription["customer"])
    if not user:
        return
    user.subscription_status = SubscriptionStatus.CANCELLED
    user.stripe_sub_id = None
    _add_audit(db, user.id, "subscription_cancelled")
    logger.info(f"[STRIPE] Abonnement annulé: {user.email}")

def _handle_payment_succeeded(db: Session, invoice: dict):
    user = _get_user_by_stripe_id(db, invoice.get("customer"))
    if user:
        _add_audit(db, user.id, "payment_succeeded", f"amount={invoice.get('amount_paid')}cents")
        logger.info(f"[STRIPE] Paiement reçu: {user.email}")

def _handle_payment_failed(db: Session, invoice: dict):
    user = _get_user_by_stripe_id(db, invoice.get("customer"))
    if user:
        _add_audit(db, user.id, "payment_failed", f"amount={invoice.get('amount_due')}cents")
        logger.warning(f"[STRIPE] Paiement échoué: {user.email}")
