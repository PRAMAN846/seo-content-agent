# SEO Content Agent

A practical SEO writing app with SaaS-style login, per-user history, and progress tracking.

## What it does

1. Accepts a query + optional source inputs
2. Collects source URLs from seed links and AI citation text
3. Filters low-quality sources (forums, Reddit, Quora, YouTube, etc.)
4. Extracts article text
5. Summarizes competitor articles
6. Builds SEO gap analysis
7. Writes a new article draft
8. Saves runs/articles per user account

## What is included now

- FastAPI backend (`app/`)
- Login/register/logout API with session cookies
- Dashboard UI with login page, run form, run history, and progress bar
- Database store that supports:
  - Neon/Postgres via `DATABASE_URL` (recommended for production)
  - SQLite fallback via `APP_DB_PATH` (for local/dev)
- Local export to `exports/*.md`

## Local setup

## Prerequisites

- Python 3.9+
- Terminal access

## Setup

```bash
cd "/Users/pramanmenaria/Documents/content writing agent"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set at least:

```bash
OPENAI_API_KEY=your_key_here
SMALL_MODEL=gpt-5-mini
ANALYST_MODEL=gpt-5-mini
WRITER_MODEL=gpt-5
COOKIE_SECURE=false
```

Start server:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- `http://127.0.0.1:8000`

## Deploy on Render with Neon (recommended)

### A) Create Neon database

1. Sign in to Neon and create a new project.
2. Create database/branch (default is fine).
3. Copy connection string from Neon dashboard (`postgresql://...`).
4. Ensure SSL is enabled (`sslmode=require`, usually already included).

### B) Configure Render service

1. Push repo to GitHub.
2. Create Render `Web Service` from that repo.
3. Build command:
   - `pip install -r requirements.txt`
4. Start command:
   - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. In Render -> Environment, set:
   - `OPENAI_API_KEY`
   - `SMALL_MODEL=gpt-5-mini`
   - `ANALYST_MODEL=gpt-5-mini`
   - `WRITER_MODEL=gpt-5`
   - `COOKIE_SECURE=true`
   - `SESSION_TTL_DAYS=30`
   - `DATABASE_URL=<your-neon-connection-string>`
6. Redeploy service.

Notes:
- If `DATABASE_URL` is set, app uses Neon/Postgres automatically.
- If `DATABASE_URL` is empty, app falls back to SQLite.

## Custom subdomain

1. Render service -> `Settings` -> `Custom Domains`
2. Add subdomain (example: `app.yourdomain.com`)
3. In your DNS provider, add the exact CNAME shown by Render
4. Wait until certificate status is issued

## API overview

- Auth
  - `POST /api/auth/register`
  - `POST /api/auth/login`
  - `POST /api/auth/logout`
  - `GET /api/auth/me`
- Runs
  - `POST /api/runs`
  - `GET /api/runs`
  - `GET /api/runs/{run_id}`
- Health
  - `GET /api/health`

## Next upgrades

- Add Redis worker queue for high throughput
- Add Google Docs/Sheets export
- Add Stripe billing + usage limits
- Add team/org roles
