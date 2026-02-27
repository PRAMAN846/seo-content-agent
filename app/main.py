from __future__ import annotations

import base64
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_runs import router as runs_router
from app.core.config import settings
from app.workers.scheduler import start_scheduler

app = FastAPI(title="SEO Content Agent")
app.include_router(runs_router)

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


def _is_auth_enabled() -> bool:
    return bool(settings.app_login_user and settings.app_login_password)


def _unauthorized_response() -> PlainTextResponse:
    return PlainTextResponse(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="SEO Agent"'},
    )


def _has_valid_basic_auth(request: Request) -> bool:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Basic "):
        return False

    token = auth_header.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False

    if ":" not in decoded:
        return False

    username, password = decoded.split(":", 1)
    return secrets.compare_digest(username, settings.app_login_user or "") and secrets.compare_digest(
        password, settings.app_login_password or ""
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not _is_auth_enabled():
        return await call_next(request)

    if request.url.path == "/api/health":
        return await call_next(request)

    if not _has_valid_basic_auth(request):
        return _unauthorized_response()

    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    start_scheduler()


@app.get("/")
def root() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")
