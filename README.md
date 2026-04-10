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
- Workspace-branded login and dashboard UI
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
WRITER_MODEL=gpt-5.4
WRITER_REASONING_EFFORT=medium
IMAGE_MODEL=gpt-image-1.5
ARTICLE_IMAGE_COUNT=3
ARTICLE_IMAGE_SIZE=1536x1024
ARTICLE_IMAGE_QUALITY=high
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

### Recommended setup for Content Agent + Playwright

Use a `Render Docker web service` for the production app.

Why:
- `playwright` the Python package installs from `requirements.txt`
- Chromium browser binaries and Linux system dependencies are much more reliable when baked into the image
- this avoids the common "package installed but browser missing" or "shared library missing" problems on native runtimes

Files now included for this setup:
- [Dockerfile](/Users/pramanmenaria/Documents/content%20writing%20agent/Dockerfile)
- [.dockerignore](/Users/pramanmenaria/Documents/content%20writing%20agent/.dockerignore)
- [render.yaml](/Users/pramanmenaria/Documents/content%20writing%20agent/render.yaml)

### Neon
1. Create Neon project
2. Copy pooled connection string
3. Keep `sslmode=require`

### Render environment variables
- `OPENAI_API_KEY`
- `SMALL_MODEL=gpt-5-mini`
- `ANALYST_MODEL=gpt-5-mini`
- `WRITER_MODEL=gpt-5.4`
- `WRITER_REASONING_EFFORT=medium`
- `IMAGE_MODEL=gpt-image-1.5`
- `ARTICLE_IMAGE_COUNT=3`
- `ARTICLE_IMAGE_SIZE=1536x1024`
- `ARTICLE_IMAGE_QUALITY=high`
- `ORCHESTRATOR_MODEL=gpt-5-mini`
- `COOKIE_SECURE=true`
- `SESSION_TTL_DAYS=30`
- `DATABASE_URL=<your-neon-connection-string>`
- Optional for live source discovery:
  - `SERPER_API_KEY=<your-serper-key>`
- Optional but recommended for agent search/browser behavior:
  - `CONTENT_AGENT_SEARCH_ENABLED=true`
  - `CONTENT_AGENT_SEARCH_RESULT_COUNT=3`
  - `CONTENT_AGENT_BROWSER_FALLBACK_ENABLED=true`
  - `CONTENT_AGENT_BROWSER_TIMEOUT_MS=18000`

For a second visibility-only deployment such as `app.searchgrowthcircle.com`, also set:
- `VISIBILITY_ONLY=true`
- `APP_BRAND_NAME=Search Growth Circle`
- `APP_PRODUCT_NAME=AI Visibility Tracker`
- `APP_NAV_EYEBROW=Search Growth Circle`
- `APP_LOGO_PATH=` or your own hosted logo path
- Optional text-only logo:
  - `APP_WORDMARK_TEXT=CONTENT`
  - if you use this, you can leave `APP_LOGO_PATH` blank

### Recommended Render deployment flow

1. Push this repo with the new `Dockerfile`.
2. In Render, create a new `Web Service`.
3. Choose `Deploy from existing repository`.
4. Render should detect the repo as a Docker service because of the `Dockerfile`.
5. Add environment variables:
   - `DATABASE_URL` from Neon
   - `OPENAI_API_KEY`
   - optional `SERPER_API_KEY`
6. Deploy.

This Docker image already runs:
- `pip install -r requirements.txt`
- `python -m playwright install --with-deps chromium`

and starts the app with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}
```

### If you stay on Render native Python instead of Docker

It can work, but it is less reliable for browser support.

You would need a build command like:

```bash
pip install -r requirements.txt && python -m playwright install chromium
```

But even then, Chromium may still fail at runtime if the underlying image is missing Linux libraries. That is why Docker is the recommended production path for the new Content Agent browser fallback.

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
