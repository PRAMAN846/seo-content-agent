from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_articles import router as articles_router
from app.api.routes_briefs import router as briefs_router
from app.api.routes_library import router as library_router
from app.api.routes_personalities import router as personalities_router
from app.api.routes_runs import router as runs_router
from app.api.routes_settings import router as settings_router
from app.api.routes_visibility import router as visibility_router
from app.api.routes_workspace import router as workspace_router
from app.core.auth import get_current_user_optional
from app.core.config import settings
from app.models.schemas import AppPublicConfig
from app.workers.scheduler import start_scheduler

app = FastAPI(title="SEO Content Agent")
app.include_router(runs_router)
app.include_router(auth_router)
app.include_router(briefs_router)
app.include_router(articles_router)
app.include_router(library_router)
app.include_router(personalities_router)
app.include_router(settings_router)
app.include_router(workspace_router)
app.include_router(visibility_router)

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
    if settings.enable_scheduler:
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


@app.get("/api/app-config", response_model=AppPublicConfig)
def app_config() -> AppPublicConfig:
    return AppPublicConfig(
        brand_name=settings.app_brand_name,
        product_name=settings.app_product_name,
        logo_path=settings.app_logo_path,
        wordmark_text=settings.app_wordmark_text,
        nav_eyebrow=settings.app_nav_eyebrow,
        visibility_only=settings.visibility_only,
    )
