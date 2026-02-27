# SEO Content Agent (Starter)

This is a beginner-friendly starter app that runs an SEO writing workflow:

1. Accepts a query
2. Collects URLs from seed links + pasted AI citations/overview text
3. Filters out low-quality sources (Reddit, Quora, YouTube, etc.)
4. Extracts article text from top URLs
5. Summarizes each article
6. Creates a combined SEO gap analysis
7. Writes a draft article (target 1500-2000 words)
8. Saves output locally to `exports/*.md`

## What this starter includes

- FastAPI backend (`app/`)
- Simple frontend (`frontend/index.html`)
- In-memory run tracking
- URL extraction/filtering
- Web content extraction (`trafilatura` + fallback parser)
- OpenAI integration with fallback mode when API key is missing

## 1) Run locally (first thing to do)

## Prerequisites

- Python 3.9+
- Terminal access

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your OpenAI key in `.env`:

```bash
OPENAI_API_KEY=your_key_here
```

Set login credentials in `.env` (required for protected access):

```bash
APP_LOGIN_USER=your_login_id
APP_LOGIN_PASSWORD=your_password
```

Start server:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open frontend in browser:

- `http://localhost:8000`

Use the form to run the workflow.

## 2) Cheapest operating model (without paid search API)

- Paste links in `Seed URLs`, or
- Paste an AI response with citations in `AI citations text`

The app extracts URLs from the pasted text and processes the top valid ones.

## 3) How to host if you are new

Use this order:

1. Test locally first (`localhost:8000`)
2. Push code to GitHub
3. Deploy on Render or Railway

### GitHub (quick)

```bash
git init
git add .
git commit -m "Initial SEO agent starter"
```

Then create a new empty repo on GitHub and run:

```bash
git remote add origin <your-repo-url>
git branch -M main
git push -u origin main
```

### Render (easy for beginners)

- Create new `Web Service`
- Connect your GitHub repo
- Build command:
  - `pip install -r requirements.txt`
- Start command:
  - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Add environment variable:
  - `OPENAI_API_KEY=...`

Render gives you a URL like `https://your-app.onrender.com` where frontend is available directly.

### Custom domain + ID/password access

1. Keep `APP_LOGIN_USER` and `APP_LOGIN_PASSWORD` set in Render environment variables.
2. In Render service settings, open `Custom Domains`.
3. Add your domain (example: `app.yourdomain.com`).
4. In your domain provider (Cloudflare/GoDaddy/Namecheap), create the DNS record Render asks for (usually a `CNAME`).
5. Wait for SSL to be issued by Render (usually a few minutes).
6. Open your custom domain. Browser will ask for ID/password (Basic Auth).

Anyone with the correct credentials can access the app and API.

### Railway (alternative)

Same commands as above; set start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

## 4) What to add next for production

- Replace in-memory store with Postgres
- Add Redis + worker queue (Celery/BullMQ)
- Add real Google Docs/Sheets API export
- Add retries, alerts, and token/cost logging
- Add human approval before publish

## API endpoints

- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs`
- `GET /api/health`
