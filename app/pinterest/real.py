"""Real Pinterest provider: official REST API v5 + OAuth 2.0 authorization-code flow only.

Endpoints (https://developers.pinterest.com/docs/api/v5/):
  authorize   GET  https://www.pinterest.com/oauth/?client_id&redirect_uri&response_type=code&scope&state
  token       POST https://api.pinterest.com/v5/oauth/token      (HTTP Basic client_id:client_secret, form body)
  revoke      POST https://api.pinterest.com/v5/oauth/token/revoke
  account     GET  /user_account              boards  GET /boards      create  POST /pins
  pin         GET  /pins/{id}                 analytics GET /pins/{id}/analytics

Verified against public documentation and search summaries only; no live account was available while writing
this, so request/response handling is defensive and covered by mocked-transport tests. See docs/PINTEREST_SETUP.md.
Nothing here ever logs or returns tokens.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..config import Settings
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

METRICS = "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK"


def _utc_in(seconds) -> datetime | None:
    try:
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(seconds))
    except (TypeError, ValueError):
        return None


class RealPinterestProvider(PinterestProvider):
    name = "pinterest"

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=40)

    def is_configured(self) -> bool:
        return bool(self.s.pinterest_client_id and self.s.pinterest_client_secret and self.s.pinterest_redirect_uri)

    # ---- OAuth ---------------------------------------------------------------------------------------
    def authorization_url(self, state: str) -> str:
        if not self.is_configured():
            raise PinterestError("Set PINTEREST_CLIENT_ID, PINTEREST_CLIENT_SECRET and PINTEREST_REDIRECT_URI first.")
        q = urlencode({"client_id": self.s.pinterest_client_id, "redirect_uri": self.s.pinterest_redirect_uri,
                       "response_type": "code", "scope": ",".join(self.s.pinterest_scopes), "state": state})
        return f"{self.s.pinterest_oauth_url}?{q}"

    def _basic(self) -> dict:
        raw = f"{self.s.pinterest_client_id}:{self.s.pinterest_client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(),
                "Content-Type": "application/x-www-form-urlencoded"}

    def _token_request(self, data: dict) -> TokenSet:
        try:
            r = self.http.post(f"{self.s.pinterest_api_base}/oauth/token", headers=self._basic(), data=data)
        except httpx.HTTPError as ex:
            raise ServiceUnavailable("Could not reach Pinterest to complete authorization.", ambiguous=False) from ex
        if r.status_code in (400, 401):
            raise AuthExpired("Pinterest did not accept the authorization code or refresh token. Reconnect Pinterest.")
        if r.status_code == 429:
            raise RateLimited(retry_after=_retry_after(r))
        if r.status_code >= 400:
            raise ServiceUnavailable(f"Pinterest token service error (HTTP {r.status_code}).")
        body = r.json()
        scope = body.get("scope", "")
        scopes = scope if isinstance(scope, list) else [x for x in scope.replace(",", " ").split() if x]
        return TokenSet(access_token=body["access_token"], refresh_token=body.get("refresh_token"),
                        expires_at=_utc_in(body.get("expires_in")),
                        refresh_expires_at=_utc_in(body.get("refresh_token_expires_in")), scopes=scopes)

    def exchange_code(self, code: str) -> TokenSet:
        return self._token_request({"grant_type": "authorization_code", "code": code,
                                    "redirect_uri": self.s.pinterest_redirect_uri})

    def refresh(self, refresh_token: str) -> TokenSet:
        ts = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        ts.refresh_token = ts.refresh_token or refresh_token  # some responses omit it: keep the old one
        return ts

    def revoke(self, access_token: str) -> bool:
        try:
            r = self.http.post(f"{self.s.pinterest_api_base}/oauth/token/revoke", headers=self._basic(),
                               data={"token": access_token, "token_type_hint": "access_token"})
        except httpx.HTTPError:
            return False
        return r.status_code in (200, 204)

    # ---- API -----------------------------------------------------------------------------------------
    def _request(self, method: str, path: str, token: str, *, create: bool = False, **kw) -> dict:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            r = self.http.request(method, f"{self.s.pinterest_api_base}{path}", headers=headers, **kw)
        except httpx.ReadTimeout as ex:
            raise ServiceUnavailable("Pinterest did not answer in time.", ambiguous=create) from ex
        except httpx.HTTPError as ex:
            raise ServiceUnavailable("Could not reach Pinterest.", ambiguous=False) from ex
        if r.status_code < 400:
            try:
                return r.json() if r.content else {}
            except ValueError as ex:
                raise ServiceUnavailable("Pinterest returned an unreadable response.", ambiguous=create) from ex
        raise _map_error(r, create)

    def get_account(self, access_token: str) -> Account:
        d = self._request("GET", "/user_account", access_token)
        username = d.get("username") or d.get("business_name")
        if not username:
            raise PermissionDenied("Pinterest did not return the account name. Reconnect and allow "
                                   "'user_accounts:read'.")
        return Account(username=str(username), id=str(d["id"]) if d.get("id") else None,
                       account_type=d.get("account_type"))

    def get_boards(self, access_token: str) -> list[Board]:
        boards, bookmark = [], None
        for _ in range(10):
            params = {"page_size": 100, **({"bookmark": bookmark} if bookmark else {})}
            d = self._request("GET", "/boards", access_token, params=params)
            boards += [Board(id=str(b["id"]), name=b.get("name", ""), privacy=b.get("privacy"))
                       for b in d.get("items", []) if b.get("id")]
            bookmark = d.get("bookmark")
            if not bookmark:
                break
        return boards

    def create_pin(self, access_token: str, payload: PinPayload) -> CreatedPin:
        body = {"board_id": payload.board_id, "title": payload.title[:100], "description": payload.description[:800],
                "link": payload.link, "alt_text": payload.alt_text[:500],
                "media_source": {"source_type": "image_base64", "content_type": "image/png",
                                 "data": base64.b64encode(payload.image_png).decode()}}
        d = self._request("POST", "/pins", access_token, create=True, json=body)
        pin_id = str(d.get("id") or "")
        if not pin_id:
            raise ServiceUnavailable("Pinterest answered without a pin id; check your board before retrying.",
                                     ambiguous=True)
        url = d.get("pin_url") or d.get("url")  # only if the API supplies one; never constructed
        return CreatedPin(pin_id=pin_id, url=url if isinstance(url, str) and url.startswith("http") else None,
                          board_id=str(d["board_id"]) if d.get("board_id") else payload.board_id)

    def get_pin(self, access_token: str, pin_id: str) -> dict:
        d = self._request("GET", f"/pins/{pin_id}", access_token)
        return {k: d.get(k) for k in ("id", "title", "description", "link", "board_id", "created_at", "alt_text")}

    def get_analytics(self, access_token: str, pin_id: str, days: int = 30) -> PinMetrics:
        end = date.today()
        start = end - timedelta(days=min(days, 89))
        d = self._request("GET", f"/pins/{pin_id}/analytics", access_token,
                          params={"start_date": start.isoformat(), "end_date": end.isoformat(),
                                  "metric_types": METRICS})
        block = d.get("all") or {}
        summary = block.get("summary_metrics") or block.get("lifetime_metrics") or {}

        def num(key):
            try:
                return int(float(summary.get(key) or 0))
            except (TypeError, ValueError):
                return 0
        return PinMetrics(impressions=num("IMPRESSION"), saves=num("SAVE"), pin_clicks=num("PIN_CLICK"),
                          outbound_clicks=num("OUTBOUND_CLICK"), raw={k: v for k, v in summary.items()})


def _retry_after(r: httpx.Response) -> int | None:
    try:
        return int(r.headers.get("retry-after", ""))
    except ValueError:
        return None


def _map_error(r: httpx.Response, create: bool) -> PinterestError:
    status = r.status_code
    try:
        message = str(r.json().get("message", ""))[:200]
    except (ValueError, AttributeError):
        message = ""
    low = message.lower()
    if status == 401:
        return AuthExpired()
    if status == 403:
        return PermissionDenied()
    if status == 429:
        return RateLimited(retry_after=_retry_after(r))
    if status >= 500:
        return ServiceUnavailable(f"Pinterest error (HTTP {status}). Try again later.",
                                  ambiguous=create and status in (502, 503, 504))
    if "board" in low:
        return InvalidBoard()
    if any(w in low for w in ("image", "media", "base64")):
        return InvalidImage(f"Pinterest rejected the pin image. {message}".strip())
    if any(w in low for w in ("link", "url")):
        return InvalidURL(f"Pinterest rejected the destination URL. {message}".strip())
    if status == 404:
        return InvalidBoard("Pinterest could not find the board or pin (HTTP 404).")
    return BadRequest(f"Pinterest rejected the request (HTTP {status}). {message}".strip())
