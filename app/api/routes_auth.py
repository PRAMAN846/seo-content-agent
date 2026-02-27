from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.core.auth import SESSION_COOKIE, get_current_user
from app.core.config import settings
from app.models.schemas import LoginRequest, RegisterRequest, UserPublic
from app.models.store import run_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    max_age_seconds = settings.session_ttl_days * 24 * 60 * 60
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=max_age_seconds,
        expires=max_age_seconds,
        path="/",
    )


@router.post("/register", response_model=UserPublic)
def register(payload: RegisterRequest, response: Response) -> UserPublic:
    try:
        user = run_store.create_user(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    token = run_store.create_session(user.id, ttl_days=settings.session_ttl_days)
    _set_session_cookie(response, token)
    return user


@router.post("/login", response_model=UserPublic)
def login(payload: LoginRequest, response: Response) -> UserPublic:
    user = run_store.authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = run_store.create_session(user.id, ttl_days=settings.session_ttl_days)
    _set_session_cookie(response, token)
    return user


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    current_user: UserPublic = Depends(get_current_user),
) -> dict[str, str]:
    del current_user
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        run_store.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/me", response_model=UserPublic)
def me(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return current_user
