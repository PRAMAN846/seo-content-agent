# SEO Content Agent

A practical SEO writing app with account login and per-user history.

## What it does

1. Accepts a query + optional source inputs
2. Collects source URLs from seed links and AI citation text
3. Filters low-quality sources (forums, Reddit, Quora, YouTube, etc.)
4. Extracts article text
5. Summarizes each competitor article
6. Builds SEO gap analysis
7. Writes a new article draft
8. Saves run history and outputs per user account

## What is included now

- FastAPI backend (`app/`)
- Login/register API with session cookies
- SQLite persistence for users, sessions, runs, and artifacts
- Dashboard UI with:
  - login page
  - run creation form
  - run history per user
  - progress bar + stage text while processing
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

You will see a login/register page first.

## Deploy on Render

1. Push repo to GitHub
2. Create Render `Web Service`
3. Build command:
   - `pip install -r requirements.txt`
4. Start command:
   - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Set environment variables in Render:
   - `OPENAI_API_KEY`
   - `SMALL_MODEL`
   - `ANALYST_MODEL`
   - `WRITER_MODEL`
   - `COOKIE_SECURE=true`
   - `SESSION_TTL_DAYS=30`
   - `APP_DB_PATH=/opt/render/project/src/data/seo_agent.db`
6. For SQLite persistence on Render, attach a Persistent Disk and use its mount path for `APP_DB_PATH`.
   - Without persistent disk, user history can reset on redeploy/restart.

## Custom subdomain

1. Render service -> `Settings` -> `Custom Domains`
2. Add subdomain (example `app.yourdomain.com`)
3. In DNS provider add the required CNAME exactly as Render shows
4. Wait until certificate is issued

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

- Replace SQLite with Postgres
- Add Redis worker queue for heavy load
- Add Google Docs/Sheets export
- Add Stripe billing + usage limits
- Add team/org roles
