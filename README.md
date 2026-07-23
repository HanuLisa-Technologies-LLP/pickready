# PickReady

Multi-tenant recruitment/ATS platform for Hanulisa Technologies LLP. Next.js 14 + FastAPI, OTP-only auth for every role, Postgres + pgvector for data and AI matching, Celery for all async work, fully Dockerized.

- **Functional requirements**: [docs/PRD.md](docs/PRD.md)
- **Architecture**: [docs/ESD.md](docs/ESD.md)
- **Build conventions**: [claude.md](claude.md)
- **API/route contract**: [docs/API_CONTRACT.md](docs/API_CONTRACT.md)

## Local dev quick start

```bash
cp .env.example .env          # fill in real keys before first run
docker compose -f infra/docker-compose.yml up --build
# frontend: http://localhost:3000
# backend:  http://localhost:8000/docs
docker compose -f infra/docker-compose.yml exec backend alembic upgrade head
docker compose -f infra/docker-compose.yml exec backend python -m app.scripts.seed_dev_data
```

Seeded dev logins (OTP prints to the backend log in development):

| Role | Email |
|---|---|
| Super Admin | admin@hanulisa.com |
| Client | client@acme.example.com |
| HR Manager | hr1@hanulisa.com |
| Recruiter | rec1@hanulisa.com |

## Tests

```bash
cd backend && python -m pytest tests -q
```

## Deploy

Frontend → Vercel (native Next.js build). Backend/worker/beat → Railway ([infra/railway.json](infra/railway.json)) or Render ([infra/render.yaml](infra/render.yaml)) with managed Postgres (pgvector enabled) and Redis. All secrets live in the platform secret manager — never in images.
