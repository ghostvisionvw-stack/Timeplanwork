import logging
import stripe
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.models import User, SubscriptionStatus, AuditLog
from app.core.database import get_db_sync

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature")
):
    """
    Webhook Stripe — reçoit les événements d'abonnement.
    Sécurisé par vérification de signature HMAC.
    """
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Signature manquante.")

    payload = await request.body()

    # ── Vérification signature Stripe ──
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

    logger.info(f"Webhook Stripe reçu: {event['type']}")

    # ── Traitement des événements ──
    db = next(get_db_sync())
    try:
        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "customer.subscription.created":
            _handle_sub_created(db, data)

        elif event_type == "customer.subscription.updated":
            _handle_sub_updated(db, data)

        elif event_type == "customer.subscription.deleted":
            _handle_sub_deleted(db, data)

        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(db, data)

        elif event_type == "checkout.session.completed":
            _handle_checkout_completed(db, data)

        db.commit()
    except Exception as e:
        logger.error(f"Webhook Stripe — erreur traitement {event_type}: {e}")
        db.rollback()
        # On retourne 200 quand même pour éviter que Stripe re-essaie indéfiniment
        return {"status": "error_logged"}
    finally:
        db.close()

    return {"status": "ok"}


def _get_user_by_stripe_id(db: Session, customer_id: str) -> User | None:
    return db.query(User).filter(User.stripe_customer_id == customer_id).first()


def _handle_checkout_completed(db: Session, session: dict):
    """Checkout terminé — lier le customer Stripe à l'utilisateur."""
    customer_id = session.get("customer")
    client_ref  = session.get("client_reference_id")  # user_id passé lors du checkout
    if not client_ref:
        return
    user = db.query(User).filter(User.id == int(client_ref)).first()
    if user and customer_id:
        user.stripe_customer_id = customer_id
        logger.info(f"Stripe customer lié: user {user.id} → {customer_id}")


def _handle_sub_created(db: Session, subscription: dict):
    """Abonnement créé → passer l'utilisateur en Pro."""
    user = _get_user_by_stripe_id(db, subscription["customer"])
    if not user:
        logger.warning(f"Sub créé — utilisateur introuvable: {subscription['customer']}")
        return
    user.subscription_status = SubscriptionStatus.PRO
    user.stripe_sub_id = subscription["id"]
    user.sub_expires_at = datetime.fromtimestamp(
        subscription["current_period_end"], tz=timezone.utc
    )
    _add_audit(db, user.id, "subscription_created", f"sub_id={subscription['id']}")
    logger.info(f"Utilisateur passé en Pro: {user.email}")


def _handle_sub_updated(db: Session, subscription: dict):
    """Abonnement mis à jour — renouvellement ou changement."""
    user = _get_user_by_stripe_id(db, subscription["customer"])
    if not user:
        return
    status_map = {
        "active":   SubscriptionStatus.PRO,
        "trialing": SubscriptionStatus.PRO,
        "past_due": SubscriptionStatus.PRO,      # Grace period
        "canceled": SubscriptionStatus.CANCELLED,
        "unpaid":   SubscriptionStatus.FREE,
    }
    new_status = status_map.get(subscription["status"], SubscriptionStatus.FREE)
    user.subscription_status = new_status
    user.sub_expires_at = datetime.fromtimestamp(
        subscription["current_period_end"], tz=timezone.utc
    )
    _add_audit(db, user.id, "subscription_updated", f"status={subscription['status']}")


def _handle_sub_deleted(db: Session, subscription: dict):
    """Abonnement annulé → repasser en Free."""
    user = _get_user_by_stripe_id(db, subscription["customer"])
    if not user:
        return
    user.subscription_status = SubscriptionStatus.CANCELLED
    user.stripe_sub_id = None
    _add_audit(db, user.id, "subscription_cancelled")
    logger.info(f"Abonnement annulé: {user.email}")


def _handle_payment_failed(db: Session, invoice: dict):
    """Paiement échoué — logger pour suivi."""
    user = _get_user_by_stripe_id(db, invoice.get("customer"))
    if user:
        _add_audit(db, user.id, "payment_failed", f"amount={invoice.get('amount_due')}")
        logger.warning(f"Paiement échoué: {user.email}")


def _add_audit(db: Session, user_id: int, action: str, details: str = None):
    log = AuditLog(user_id=user_id, action=action, details=details)
    db.add(log)
