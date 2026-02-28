# SEO Content Agent

A two-agent SEO content workspace with login, per-user history, editable briefs, and article generation.

## Product structure

### 1) SEO Brief Agent
- Accepts a query plus optional source URLs / AI citations / overview text
- Builds competitor analysis when sources are available
- Produces an editable markdown SEO brief
- Lets the user edit the brief draft and save it
- Lets the user generate content directly from that brief

### 2) Content Writer Agent
Supports 3 modes:
- `Use SEO Brief Agent output`
- `Paste your own brief`
- `Quick draft from query`

## What is included now

- FastAPI backend (`app/`)
- Login/register/logout API with session cookies
- Xpaan-branded login and dashboard UI
- Two separate agent tabs in dashboard
- Per-user history for:
  - briefs
  - articles
- Progress bars and stage labels for both agents
- Database store that supports:
  - Neon/Postgres via `DATABASE_URL` (recommended for production)
  - SQLite fallback via `APP_DB_PATH` (for local/dev)
- Local export to `exports/*.md`

## Local setup

### Prerequisites
- Python 3.9+
- Terminal access

### Setup

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

## Deploy on Render with Neon

### Neon
1. Create Neon project
2. Copy pooled connection string
3. Keep `sslmode=require`

### Render environment variables
- `OPENAI_API_KEY`
- `SMALL_MODEL=gpt-5-mini`
- `ANALYST_MODEL=gpt-5-mini`
- `WRITER_MODEL=gpt-5`
- `COOKIE_SECURE=true`
- `SESSION_TTL_DAYS=30`
- `DATABASE_URL=<your-neon-connection-string>`

### Render commands
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Main API overview

### Auth
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Briefs
- `GET /api/briefs`
- `POST /api/briefs`
- `GET /api/briefs/{brief_id}`
- `PATCH /api/briefs/{brief_id}`

### Articles
- `GET /api/articles`
- `POST /api/articles`
- `GET /api/articles/{article_id}`

## Next upgrades
- Add Google Docs / Sheets export
- Add Redis worker queue for heavier usage
- Add Stripe billing + usage limits
- Add team/org roles
