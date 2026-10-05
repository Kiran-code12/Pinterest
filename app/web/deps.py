from __future__ import annotations

from typing import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..models import User
from ..security import csrf_ok, new_csrf_token


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.db.session()
    try:
        yield db
    finally:
        db.close()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = new_csrf_token()
    return token


def current_user(request: Request) -> User:
    from ..main import AuthRequired
    uid = request.session.get("uid")
    if not uid:
        raise AuthRequired()
    db = request.app.state.db.session()
    try:
        user = db.get(User, uid)
    finally:
        db.close()
    if user is None:
        request.session.clear()
        raise AuthRequired()
    return user


async def verify_csrf(request: Request) -> None:
    """Required on every state-changing request (form field `csrf_token` or header X-CSRF-Token)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    provided = request.headers.get("x-csrf-token")
    if not provided and "form" in request.headers.get("content-type", ""):
        provided = (await request.form()).get("csrf_token")
    if not csrf_ok(request.session.get("csrf"), provided if isinstance(provided, str) else None):
        raise HTTPException(status_code=403, detail="Security token missing or expired. Reload the page and retry.")


def rate_limit(request: Request, kind: str = "action") -> None:
    from ..main import RateLimited
    s = request.app.state.settings
    limit = s.login_rate_limit if kind == "login" else s.action_rate_limit
    ip = request.client.host if request.client else "unknown"
    if not request.app.state.limiter.allow(f"{kind}:{ip}", limit):
        raise RateLimited()
