# Local Preview Guide

Use this when you want to see dashboard changes locally before pushing to production.

## What the app serves locally

The dashboard route serves:

- [app/main.py](/Users/pramanmenaria/Documents/content writing agent/app/main.py)
- [frontend/dashboard.html](/Users/pramanmenaria/Documents/content writing agent/frontend/dashboard.html)

So any UI change in `frontend/dashboard.html` can be tested on localhost without deploying first.

## Safe recommendation

Use a staging or cloned production database, not the live production database.

Why:
- your local login session is separate from production
- local saves and deletes would affect whatever database you connect to
- scheduled runs should stay off during local preview

## Recommended local preview setup

1. Start from your project folder:

```bash
cd "/Users/pramanmenaria/Documents/content writing agent"
source .venv/bin/activate
```

2. Export preview-safe variables:

```bash
export DATABASE_URL="your_staging_or_prod_clone_db_url"
export COOKIE_SECURE=false
export ENABLE_SCHEDULER=false
```

3. Run the app:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

4. Open:

- [http://127.0.0.1:8000/login](http://127.0.0.1:8000/login)
- [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard)

## If you only want a fully local database

Leave `DATABASE_URL` empty and use the SQLite file configured by `APP_DB_PATH`.

That is safest for development, but it will not show the same accounts and project data as production.

## Important notes

- `DATABASE_URL` overrides `APP_DB_PATH`.
- `ENABLE_SCHEDULER=false` prevents your local server from kicking off scheduled visibility runs.
- If you connect local directly to production, local edits will change production data.

## Suggested workflow

For normal UI work:
- use localhost
- use a staging/prod-clone database
- keep scheduler disabled

For final verification:
- review locally first
- then push to `main`
- then do one final production smoke check
