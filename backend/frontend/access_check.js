// ── VÉRIFICATION ACCÈS BÊTA ──
(async function checkBetaAccess() {
  const token = localStorage.getItem('access_token');
  const user = JSON.parse(localStorage.getItem('user') || 'null');

  // Si pas connecté → page de demande d'accès
  if (!token || !user) {
    showAccessWall('not_logged');
    return;
  }

  // Admin → accès direct
  if (user.is_admin) return;

  // Vérifier le statut bêta via API
  try {
    const res = await fetch('/api/beta/status', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (res.ok) {
      const data = await res.json();
      if (data.has_access) return; // Accès autorisé
      showAccessWall(data.beta_status);
    } else {
      showAccessWall('error');
    }
  } catch(e) {
    // En cas d'erreur réseau, on laisse passer (fail open)
  }
})();

function showAccessWall(status) {
  document.body.innerHTML = `
    <div style="min-height:100vh;background:#0b0e18;display:flex;align-items:center;justify-content:center;padding:24px;font-family:'Inter',system-ui,sans-serif;">
      <div style="background:#131728;border:1px solid #252d4a;border-radius:20px;padding:40px 36px;max-width:440px;width:100%;text-align:center;">
        <div style="font-size:48px;margin-bottom:16px;">${status === 'pending' ? '⏳' : status === 'rejected' ? '❌' : '🔒'}</div>
        <div style="font-size:22px;font-weight:800;color:#e8edf8;margin-bottom:10px;letter-spacing:-.5px;">
          ${status === 'pending' ? 'Demande en cours...' : status === 'rejected' ? 'Accès refusé' : 'Accès bêta requis'}
        </div>
        <p style="font-size:14px;color:#6b7799;line-height:1.6;margin-bottom:28px;">
          ${status === 'pending'
            ? 'Votre demande d\'accès bêta est en cours de traitement. Vous serez notifié dès qu\'elle sera validée.'
            : status === 'rejected'
            ? 'Votre demande d\'accès bêta a été refusée. Contactez le support pour plus d\'informations.'
            : status === 'not_logged'
            ? 'Le calculateur est en accès bêta. Connectez-vous ou demandez votre accès pour continuer.'
            : 'Le calculateur est actuellement réservé aux membres de la bêta.'}
        </p>
        ${status === 'not_logged' ? `
          <a href="/login" style="display:block;background:#3d7fff;color:#fff;border-radius:10px;padding:12px;font-weight:700;text-decoration:none;margin-bottom:10px;">Se connecter</a>
          <a href="/register" style="display:block;background:transparent;border:1px solid #252d4a;color:#e8edf8;border-radius:10px;padding:12px;font-weight:700;text-decoration:none;">Créer un compte</a>
        ` : status === 'none' || !status ? `
          <button onclick="requestBeta()" style="width:100%;background:#3d7fff;color:#fff;border:none;border-radius:10px;padding:13px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;">Demander l'accès bêta</button>
          <div id="beta-msg" style="margin-top:12px;font-size:13px;color:#6b7799;"></div>
        ` : `
          <a href="/" style="display:block;background:#3d7fff;color:#fff;border-radius:10px;padding:12px;font-weight:700;text-decoration:none;">Retour à l'accueil</a>
        `}
        <div style="margin-top:20px;"><a href="/" style="font-size:13px;color:#6b7799;text-decoration:none;">← Retour à l'accueil</a></div>
      </div>
    </div>`;
}

async function requestBeta() {
  const token = localStorage.getItem('access_token');
  const btn = document.querySelector('button[onclick="requestBeta()"]');
  btn.disabled = true; btn.textContent = 'Envoi...';
  try {
    const res = await fetch('/api/beta/apply', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: '' })
    });
    const d = await res.json();
    document.getElementById('beta-msg').textContent = d.message || 'Demande envoyée !';
    document.getElementById('beta-msg').style.color = '#00c875';
    btn.textContent = '✓ Demande envoyée';
  } catch(e) {
    document.getElementById('beta-msg').textContent = 'Erreur. Réessayez.';
    btn.disabled = false; btn.textContent = 'Demander l\'accès bêta';
  }
}
