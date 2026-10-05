"""MockPinterestProvider: a faithful in-memory simulation for automated tests and offline trials.

Everything is SIMULATED: nothing is sent to Pinterest. The UI shows a red banner whenever it is active and
published records are flagged `simulated`. It can reproduce the failure modes the app must handle.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from .errors import (
    AuthExpired,
    BadRequest,
    InvalidBoard,
    InvalidImage,
    InvalidURL,
    PermissionDenied,
    PinterestError,
    RateLimited,
    ServiceUnavailable,
)
from .provider import PinterestProvider
from .types import Account, Board, CreatedPin, PinMetrics, PinPayload, TokenSet

PNG_MAGIC = b"\x89PNG"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MockPinterestProvider(PinterestProvider):
    name = "mock"
    simulated = True

    def __init__(self, username: str = "mockaccount", boards: list[tuple[str, str]] | None = None):
        self.username = username
        self.boards = [Board(id=i, name=n, privacy="PUBLIC") for i, n in
                       (boards or [("b-beauty", "Beauty Finds"), ("b-makeup", "Makeup"), ("b-skin", "Skincare")])]
        self.scopes = ["boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read"]
        self.valid_tokens: set[str] = set()
        self.valid_refresh: set[str] = set()
        self.pins: dict[str, dict] = {}
        self.calls: list[str] = []
        self.fail_queue: list[PinterestError] = []   # consumed one per create_pin call
        self.metrics = PinMetrics(impressions=120, saves=7, pin_clicks=11, outbound_clicks=4, raw={"mock": True})
        self.access_ttl = 3600
        self.revoke_ok = True
        self._ids = itertools.count(1000)
        self._tokens = itertools.count(1)

    # ---- test controls --------------------------------------------------------------------------------
    def fail_next(self, *errors: PinterestError) -> None:
        self.fail_queue.extend(errors)

    def expire_access_tokens(self) -> None:
        self.valid_tokens.clear()

    def revoke_everything(self) -> None:
        self.valid_tokens.clear()
        self.valid_refresh.clear()

    # ---- provider API ---------------------------------------------------------------------------------
    def is_configured(self) -> bool:
        return True

    def authorization_url(self, state: str) -> str:
        return f"/pinterest/mock-authorize?state={state}"

    def _issue(self) -> TokenSet:
        n = next(self._tokens)
        access, refresh = f"mock-access-{n}", f"mock-refresh-{n}"
        self.valid_tokens.add(access)
        self.valid_refresh.add(refresh)
        return TokenSet(access_token=access, refresh_token=refresh,
                        expires_at=_now() + timedelta(seconds=self.access_ttl),
                        refresh_expires_at=_now() + timedelta(days=365), scopes=list(self.scopes))

    def exchange_code(self, code: str) -> TokenSet:
        self.calls.append("exchange_code")
        if code != "mock-code":
            raise AuthExpired("Pinterest did not accept the authorization code. Reconnect Pinterest.")
        return self._issue()

    def refresh(self, refresh_token: str) -> TokenSet:
        self.calls.append("refresh")
        if refresh_token not in self.valid_refresh:
            raise AuthExpired()
        self.valid_refresh.discard(refresh_token)
        return self._issue()

    def _auth(self, token: str) -> None:
        if token not in self.valid_tokens:
            raise AuthExpired()

    def get_account(self, access_token: str) -> Account:
        self.calls.append("get_account")
        self._auth(access_token)
        return Account(username=self.username, id="mock-acct-1", account_type="BUSINESS")

    def get_boards(self, access_token: str) -> list[Board]:
        self.calls.append("get_boards")
        self._auth(access_token)
        return list(self.boards)

    def create_pin(self, access_token: str, payload: PinPayload) -> CreatedPin:
        self.calls.append("create_pin")
        self._auth(access_token)
        if self.fail_queue:
            raise self.fail_queue.pop(0)
        if "pins:write" not in self.scopes:
            raise PermissionDenied()
        if payload.board_id not in {b.id for b in self.boards}:
            raise InvalidBoard()
        if not payload.image_png.startswith(PNG_MAGIC):
            raise InvalidImage()
        if not payload.link.startswith(("http://", "https://")):
            raise InvalidURL()
        if not payload.title or len(payload.title) > 100 or len(payload.description) > 800:
            raise BadRequest()
        pin_id = str(next(self._ids))
        self.pins[pin_id] = {"id": pin_id, "title": payload.title, "description": payload.description,
                             "link": payload.link, "board_id": payload.board_id, "alt_text": payload.alt_text}
        return CreatedPin(pin_id=pin_id, url=None, board_id=payload.board_id)  # real API gives no pin URL

    def get_pin(self, access_token: str, pin_id: str) -> dict:
        self.calls.append("get_pin")
        self._auth(access_token)
        if pin_id not in self.pins:
            raise InvalidBoard("Pinterest could not find that pin (HTTP 404).")
        return dict(self.pins[pin_id])

    def get_analytics(self, access_token: str, pin_id: str, days: int = 30) -> PinMetrics:
        self.calls.append("get_analytics")
        self._auth(access_token)
        if pin_id not in self.pins:
            raise ServiceUnavailable("No analytics for that pin.")
        return self.metrics

    def revoke(self, access_token: str) -> bool:
        self.calls.append("revoke")
        self.valid_tokens.discard(access_token)
        return self.revoke_ok


_ = RateLimited
