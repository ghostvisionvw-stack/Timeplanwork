# Guide de déploiement — TimePlan.work

## 1. Création du repo GitHub

1. Aller sur github.com → New repository
2. Nom : `timeplan-work`
3. Privé (IMPORTANT — ne jamais mettre en public)
4. Copier tous les fichiers dans le repo via vscode.dev

## 2. Variables d'environnement Railway (OBLIGATOIRES)

Dans Railway → votre service → Variables :

```
# Générer avec Python: import secrets; print(secrets.token_hex(64))
SECRET_KEY=<64_caracteres_aleatoires>

DATABASE_URL=<fourni_automatiquement_par_Railway_PostgreSQL>

STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PRO_MONTHLY=price_...

FRONTEND_URL=https://timeplan.work
ALLOWED_ORIGINS=["https://timeplan.work","https://www.timeplan.work"]

ADMIN_EMAIL=votre@email.com
ENVIRONMENT=production
```

## 3. Déploiement Railway

1. Railway → New Project → Deploy from GitHub
2. Sélectionner `timeplan-work`
3. Ajouter PostgreSQL : Add Service → Database → PostgreSQL
4. Les variables DATABASE_URL sont injectées automatiquement
5. Déployer

## 4. Configuration Stripe

1. Créer un compte sur stripe.com
2. Dashboard → Developers → API Keys → copier `Secret key`
3. Products → Create product : "TimePlan Pro" — 4,99€/mois
4. Copier le `Price ID` → STRIPE_PRICE_PRO_MONTHLY
5. Webhooks → Add endpoint :
   - URL : `https://votre-app.railway.app/api/webhooks/stripe`
   - Events à écouter :
     - `checkout.session.completed`
     - `customer.subscription.created`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_failed`
6. Copier le Signing Secret → STRIPE_WEBHOOK_SECRET

## 5. Nom de domaine

1. Acheter `timeplan.work` sur Namecheap ou OVH
2. Dans Railway → Settings → Domains → Add custom domain
3. Configurer les DNS chez votre registrar :
   - Type CNAME : `@` → votre-app.up.railway.app
   - Type CNAME : `www` → votre-app.up.railway.app
4. Railway génère automatiquement le certificat SSL

## 6. Premier compte admin

Après déploiement, en base de données :
```sql
UPDATE users SET is_admin = true WHERE email = 'votre@email.com';
```
Ou via Railway → PostgreSQL → Query

## 7. Sécurité — checklist finale

- [ ] SECRET_KEY généré avec `secrets.token_hex(64)` (JAMAIS réutiliser)
- [ ] ENVIRONMENT=production (désactive /docs)
- [ ] Repo GitHub en PRIVÉ
- [ ] Stripe en mode Live (pas Test) pour la production
- [ ] HTTPS activé (automatique avec Railway)
- [ ] Backup PostgreSQL activé dans Railway
- [ ] Webhook Stripe testé avec stripe-cli
