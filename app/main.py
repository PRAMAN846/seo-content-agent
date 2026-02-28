from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_articles import router as articles_router
from app.api.routes_briefs import router as briefs_router
from app.api.routes_runs import router as runs_router
from app.api.routes_settings import router as settings_router
from app.core.auth import get_current_user_optional
from app.workers.scheduler import start_scheduler

app = FastAPI(title="SEO Content Agent")
app.include_router(runs_router)
app.include_router(auth_router)
app.include_router(briefs_router)
app.include_router(articles_router)
app.include_router(settings_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = Path("frontend")
if frontend_dir.exists():
    app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")


@app.on_event("startup")
def on_startup() -> None:
    start_scheduler()


@app.get("/")
def root(request: Request) -> RedirectResponse:
    if get_current_user_optional(request):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_model=None)
def login_page(request: Request) -> Response:
    if get_current_user_optional(request):
        return RedirectResponse("/dashboard", status_code=302)
    return FileResponse(frontend_dir / "login.html")


@app.get("/dashboard", response_model=None)
def dashboard_page(request: Request) -> Response:
    if not get_current_user_optional(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(frontend_dir / "dashboard.html")
