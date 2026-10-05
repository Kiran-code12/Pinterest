"""Application factory."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .ai.base import AIProvider
from .ai.factory import build_ai_provider
from .config import Settings, get_settings
from .db import Database
from .logging_setup import setup_logging
from .models import User
from .pinterest.connection import ConnectionService
from .pinterest.mock import MockPinterestProvider
from .pinterest.provider import PinterestProvider
from .pinterest.real import RealPinterestProvider
from .providers.registry import build_registry, sync_provider_rows
from .security import RateLimiter, hash_password, verify_password
from .services.templates import seed_templates

BASE = Path(__file__).resolve().parent
log = logging.getLogger("engine")

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'self'")


class AuthRequired(Exception):
    pass


class RateLimited(Exception):
    pass


def seed_admin(db, settings: Settings) -> None:
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    if user is None:
        db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
    elif not verify_password(settings.admin_password, user.password_hash):
        user.password_hash = hash_password(settings.admin_password)  # env var is the source of truth
    db.commit()


def create_app(settings: Settings | None = None, *, http: httpx.Client | None = None,
               ai: AIProvider | None = None, pinterest_provider: PinterestProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.secret_values())
    app = FastAPI(title="AI Pinterest Affiliate Engine", version=__version__,
                  docs_url=None if settings.is_prod else "/api/docs", redoc_url=None,
                  openapi_url=None if settings.is_prod else "/api/openapi.json")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    db.create_all()
    registry = build_registry(settings, http)
    with db.session() as s:
        sync_provider_rows(s, registry)
        seed_templates(s)
        seed_admin(s, settings)
    app.state.settings, app.state.db, app.state.registry = settings, db, registry
    app.state.http = http
    app.state.ai = ai or build_ai_provider(settings, http)
    if pinterest_provider is None:
        pinterest_provider = (MockPinterestProvider() if settings.pinterest_provider == "mock"
                              else RealPinterestProvider(settings, http))
    app.state.pinterest = ConnectionService(pinterest_provider, settings)
    app.state.limiter = RateLimiter()

    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="engine_session",
                       same_site="lax", https_only=settings.is_prod, max_age=60 * 60 * 24 * 7)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/static"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    from .web import api, pages
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")

    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/api")

    @app.exception_handler(AuthRequired)
    async def _auth(request: Request, exc: AuthRequired):
        if wants_json(request):
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(RateLimited)
    async def _rate(request: Request, exc: RateLimited):
        return JSONResponse({"error": "Too many requests. Please wait a minute."}, status_code=429)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if wants_json(request):
            if isinstance(exc.detail, dict):  # structured details (e.g. duplicate findings, error codes)
                body = {"error": exc.detail.get("error") or exc.detail.get("message") or "Request failed", **exc.detail}
            else:
                body = {"error": str(exc.detail)}
            return JSONResponse(body, status_code=exc.status_code)
        return pages.render_error(request, exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        ref = uuid.uuid4().hex[:8]
        log.exception("unhandled error ref=%s path=%s", ref, request.url.path)  # details stay in the server log
        msg = f"Something went wrong (reference {ref})."
        if wants_json(request):
            return JSONResponse({"error": msg}, status_code=500)
        return pages.render_error(request, 500, msg)

    return app


def app_factory() -> FastAPI:  # for `uvicorn app.main:app_factory --factory`
    return create_app()
