# TimePlan.work

Calculateur d'heures de travail universel avec export PDF juridique.

## Stack
- **Backend** : FastAPI + PostgreSQL (Railway)
- **Auth** : JWT + bcrypt
- **Paiement** : Stripe
- **Frontend** : React (Railway)

## Structure
```
timeplan/
├── backend/          # FastAPI
│   ├── app/
│   │   ├── api/      # Routes
│   │   ├── core/     # Config, sécurité
│   │   ├── models/   # DB models
│   │   ├── schemas/  # Pydantic
│   │   └── services/ # Logique métier
│   ├── main.py
│   └── requirements.txt
├── frontend/         # React
└── docs/             # Documentation
```

## Variables d'environnement Railway
```
DATABASE_URL=postgresql://...
SECRET_KEY=...          # 64 chars minimum
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
FRONTEND_URL=https://timeplan.work
ADMIN_EMAIL=votre@email.com
```
