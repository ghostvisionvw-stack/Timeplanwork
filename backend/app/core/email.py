import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, html_content: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"TimePlan.work <contact@timeplanwork.com>"
        msg["To"] = to_email

        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail("contact@timeplanwork.com", to_email, msg.as_string())

        logger.info(f"Email envoyé à {to_email} : {subject}")
        return True

    except Exception as e:
        logger.error(f"Erreur envoi email à {to_email} : {e}")
        return False


def send_beta_approved_email(to_email: str, first_name: str) -> bool:
    subject = "✅ Votre accès bêta TimePlan.work est activé !"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a1a2e; padding: 30px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">TimePlan<span style="color: #4f8ef7;">.work</span></h1>
        </div>
        <div style="padding: 30px; background: #f9f9f9;">
            <h2 style="color: #1a1a2e;">Bienvenue {first_name} ! 🎉</h2>
            <p style="color: #444; line-height: 1.6;">
                Votre accès bêta a été <strong>approuvé</strong>. Vous pouvez dès maintenant accéder à toutes les fonctionnalités de TimePlan.work.
            </p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="https://timeplanwork.com/login" 
                   style="background: #4f8ef7; color: white; padding: 14px 30px; 
                          text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Accéder à mon compte
                </a>
            </div>
            <p style="color: #888; font-size: 13px;">
                Une question ? Répondez à cet email, nous vous répondons sous 24h.
            </p>
        </div>
        <div style="background: #1a1a2e; padding: 15px; text-align: center;">
            <p style="color: #888; font-size: 12px; margin: 0;">
                © 2025 TimePlan.work — <a href="https://timeplanwork.com" style="color: #4f8ef7;">timeplanwork.com</a>
            </p>
        </div>
    </div>
    """
    return send_email(to_email, subject, html)


def send_reset_password_email(to_email: str, reset_token: str) -> bool:
    reset_url = f"https://timeplanwork.com/reset-password?token={reset_token}"
    subject = "🔑 Réinitialisation de votre mot de passe TimePlan.work"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a1a2e; padding: 30px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">TimePlan<span style="color: #4f8ef7;">.work</span></h1>
        </div>
        <div style="padding: 30px; background: #f9f9f9;">
            <h2 style="color: #1a1a2e;">Réinitialisation du mot de passe</h2>
            <p style="color: #444; line-height: 1.6;">
                Vous avez demandé à réinitialiser votre mot de passe. Cliquez sur le bouton ci-dessous. 
                Ce lien est valable <strong>1 heure</strong>.
            </p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}" 
                   style="background: #4f8ef7; color: white; padding: 14px 30px; 
                          text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Réinitialiser mon mot de passe
                </a>
            </div>
            <p style="color: #888; font-size: 13px;">
                Si vous n'avez pas fait cette demande, ignorez cet email. Votre mot de passe ne sera pas modifié.
            </p>
        </div>
        <div style="background: #1a1a2e; padding: 15px; text-align: center;">
            <p style="color: #888; font-size: 12px; margin: 0;">
                © 2025 TimePlan.work — <a href="https://timeplanwork.com" style="color: #4f8ef7;">timeplanwork.com</a>
            </p>
        </div>
    </div>
    """
    return send_email(to_email, subject, html)


def send_feedback_reply_email(to_email: str, first_name: str, original_message: str, reply: str) -> bool:
    subject = "💬 Réponse à votre feedback — TimePlan.work"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a1a2e; padding: 30px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">TimePlan<span style="color: #4f8ef7;">.work</span></h1>
        </div>
        <div style="padding: 30px; background: #f9f9f9;">
            <h2 style="color: #1a1a2e;">Bonjour {first_name},</h2>
            <p style="color: #444; line-height: 1.6;">Merci pour votre feedback ! Voici notre réponse :</p>
            <div style="background: #e8f0fe; border-left: 4px solid #4f8ef7; padding: 15px; margin: 20px 0; border-radius: 4px;">
                <p style="color: #1a1a2e; margin: 0; line-height: 1.6;">{reply}</p>
            </div>
            <p style="color: #888; font-size: 13px; border-top: 1px solid #ddd; padding-top: 15px;">
                <strong>Votre message original :</strong><br>
                <em>{original_message}</em>
            </p>
        </div>
        <div style="background: #1a1a2e; padding: 15px; text-align: center;">
            <p style="color: #888; font-size: 12px; margin: 0;">
                © 2025 TimePlan.work — <a href="https://timeplanwork.com" style="color: #4f8ef7;">timeplanwork.com</a>
            </p>
        </div>
    </div>
    """
    return send_email(to_email, subject, html)