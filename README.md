# SEO Content Agent

A multi-surface SEO content workspace with direct agents, a conversational AI workspace, per-user history, editable briefs, and article generation.

## Product structure

### 1) AI Workspace
- Conversational orchestrator tab
- Understands whether the user wants:
  - a content brief
  - a direct full article
  - content from a selected saved brief
- Asks short clarifying questions when needed
- Routes work into the existing brief/article systems

### 2) SEO Brief Agent
- Accepts a query plus optional top ranking URLs / AI citations / overview text
- Builds competitor analysis when sources are available
- Produces an editable markdown SEO brief
- Lets the user edit the brief draft and save it
- Lets the user generate content directly from that brief

### 3) Content Writer Agent
Supports 3 modes:
- `Use SEO Brief Agent output`
- `Paste your own brief`
- `Quick draft from query`

## What is included now

- FastAPI backend (`app/`)
- Login/register/logout API with session cookies
- Xpaan-branded login and dashboard UI
- `Home`, `AI Workspace`, `Content Brief Agent`, `Content Writing Agent`, and `Settings` in dashboard
- Account Settings tab with:
  - user name
  - brand name
  - brand URL
  - custom SEO brief prompt override
  - custom content writer prompt override
- Personality controls for:
  - AI Workspace
  - Content Brief Agent
  - Content Writing Agent
- Custom personality notes for:
  - AI Workspace
  - Content Brief Agent
  - Content Writing Agent
- Saved brief view now shows the original request inputs used to generate that brief
- Per-user history for:
  - briefs
  - articles
- Progress bars and stage labels for both agents
- Article view with `See Brief` toggle
- Database store that supports:
  - Neon/Postgres via `DATABASE_URL` (recommended for production)
  - SQLite fallback via `APP_DB_PATH` (for local/dev)
- Local export to `exports/*.md`
- Google Docs / Sheets integration status placeholders in UI
- Prompt customization applied automatically during brief and article generation
- Personality presets applied automatically during AI routing, brief generation, and content writing

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
ORCHESTRATOR_MODEL=gpt-5-mini
COOKIE_SECURE=false
ENABLE_SCHEDULER=false
```

Start server:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:
- `http://127.0.0.1:8000`

### Local preview with production-like data

If you want to test UI changes locally with the same accounts/projects as production, do not point your laptop at the live production database by default. Instead:

1. Create a staging database by cloning production.
2. Put that connection string in `DATABASE_URL`.
3. Keep `COOKIE_SECURE=false` locally.
4. Set `ENABLE_SCHEDULER=false` so local preview does not trigger scheduled runs.

Example:

```bash
export DATABASE_URL="your_staging_or_prod_clone_db_url"
export COOKIE_SECURE=false
export ENABLE_SCHEDULER=false
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Notes:
- `DATABASE_URL` takes priority over `APP_DB_PATH`.
- You will still log in through `localhost`; browser sessions do not carry over from production.
- If you point local directly at production, any edits, deletions, or runs from your local machine will affect production data.

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
- `ORCHESTRATOR_MODEL=gpt-5-mini`
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

### Settings
- `GET /api/settings`
- `PUT /api/settings`

### Personalities
- `GET /api/personalities`

### AI Workspace
- `POST /api/workspace/message`

## Next upgrades
- Reviewer agents and reviewer personalities
- Add Google Docs / Sheets export
- Add Redis worker queue for heavier usage
- Add Stripe billing + usage limits
- Add team/org roles
