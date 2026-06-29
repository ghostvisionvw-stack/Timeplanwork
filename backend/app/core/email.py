# ═══════════════════════════════════════════════════════════
# À AJOUTER À LA FIN DE app/core/email.py
# ═══════════════════════════════════════════════════════════

def send_contact_notification(nom: str, email: str, sujet: str, message: str) -> bool:
    """Notifie l'admin qu'un nouveau message contact est arrivé."""
    subject = f"📩 Nouveau message contact — {sujet}"
    # Échapper le HTML basique pour éviter l'injection dans l'email
    safe_msg = (message or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    safe_nom = (nom or "").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a1a2e; padding: 30px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0;">TimePlan<span style="color: #4f8ef7;">.work</span></h1>
        </div>
        <div style="padding: 30px; background: #f9f9f9;">
            <h2 style="color: #1a1a2e;">Nouveau message via le formulaire de contact</h2>
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 8px 0; color: #888; width: 100px;"><strong>Nom</strong></td>
                    <td style="padding: 8px 0; color: #1a1a2e;">{safe_nom}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #888;"><strong>Email</strong></td>
                    <td style="padding: 8px 0; color: #1a1a2e;"><a href="mailto:{email}" style="color:#4f8ef7;">{email}</a></td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #888;"><strong>Sujet</strong></td>
                    <td style="padding: 8px 0; color: #1a1a2e;">{sujet}</td>
                </tr>
            </table>
            <div style="background: #e8f0fe; border-left: 4px solid #4f8ef7; padding: 15px; margin: 20px 0; border-radius: 4px;">
                <p style="color: #1a1a2e; margin: 0; line-height: 1.6;">{safe_msg}</p>
            </div>
            <div style="text-align: center; margin: 25px 0;">
                <a href="mailto:{email}?subject=RE: {sujet}"
                   style="background: #4f8ef7; color: white; padding: 12px 26px;
                          text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Répondre à {safe_nom}
                </a>
            </div>
        </div>
        <div style="background: #1a1a2e; padding: 15px; text-align: center;">
            <p style="color: #888; font-size: 12px; margin: 0;">
                © 2025 TimePlan.work — Message reçu via timeplanwork.com/contact
            </p>
        </div>
    </div>
    """
    # Envoi à l'adresse admin
    return send_email("contact@timeplanwork.com", subject, html)