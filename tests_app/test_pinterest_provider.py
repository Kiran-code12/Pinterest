import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import build_settings
from app.pinterest import errors as E
from app.pinterest.real import RealPinterestProvider
from app.pinterest.types import PinPayload


def S(**kw):
    return build_settings({"DATA_DIR": "/tmp/engine-tests-pin", "ADMIN_PASSWORD": "pw-123456", "SECRET_KEY": "k" * 40,
                           "PINTEREST_CLIENT_ID": "cid-1", "PINTEREST_CLIENT_SECRET": "client-secret-xyz",
                           "PINTEREST_REDIRECT_URI": "http://localhost:8000/pinterest/callback", **kw})


def provider(handler):
    return RealPinterestProvider(S(), httpx.Client(transport=httpx.MockTransport(handler)))


PAYLOAD = PinPayload(board_id="B1", title="T", description="D", link="https://ekaro.in/x/1", alt_text="A",
                     image_png=b"\x89PNG-bytes")


def test_not_configured_without_env():
    p = RealPinterestProvider(build_settings({"DATA_DIR": "/tmp/engine-tests-pin", "ADMIN_PASSWORD": "pw-123456",
                                              "SECRET_KEY": "k" * 40, "PINTEREST_REDIRECT_URI": ""}))
    assert not p.is_configured()
    with pytest.raises(E.PinterestError):
        p.authorization_url("s")


def test_authorization_url():
    url = provider(lambda r: httpx.Response(200)).authorization_url("state123")
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.pinterest.com/oauth/"
    assert q["client_id"] == ["cid-1"] and q["response_type"] == ["code"] and q["state"] == ["state123"]
    assert q["redirect_uri"] == ["http://localhost:8000/pinterest/callback"]
    assert set(q["scope"][0].split(",")) == {"boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read"}
    assert "client-secret-xyz" not in url  # the secret never goes through the browser


def test_code_exchange_uses_basic_auth_and_form_body():
    seen = {}

    def h(req):
        seen["req"] = req
        return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 2592000,
                                         "refresh_token_expires_in": 31536000,
                                         "scope": "boards:read,pins:write,pins:read"})
    ts = provider(h).exchange_code("the-code")
    req = seen["req"]
    assert str(req.url) == "https://api.pinterest.com/v5/oauth/token"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"cid-1:client-secret-xyz").decode()
    form = parse_qs(req.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
    assert ts.access_token == "AT" and ts.refresh_token == "RT" and "pins:write" in ts.scopes and ts.expires_at


def test_refresh_keeps_old_refresh_token_when_omitted():
    p = provider(lambda r: httpx.Response(200, json={"access_token": "AT2", "expires_in": 100}))
    ts = p.refresh("OLD-RT")
    assert ts.access_token == "AT2" and ts.refresh_token == "OLD-RT"


def test_bad_code_means_reconnect():
    with pytest.raises(E.AuthExpired):
        provider(lambda r: httpx.Response(400, json={"message": "invalid code"})).exchange_code("x")


def test_account_boards_pagination():
    def h(req):
        if req.url.path.endswith("/user_account"):
            return httpx.Response(200, json={"username": "myacct", "id": "42", "account_type": "BUSINESS"})
        if "bookmark" not in req.url.params:
            return httpx.Response(200, json={"items": [{"id": "1", "name": "Beauty", "privacy": "PUBLIC"}], "bookmark": "nxt"})
        return httpx.Response(200, json={"items": [{"id": "2", "name": "Makeup"}], "bookmark": None})
    p = provider(h)
    a = p.get_account("AT")
    assert (a.username, a.id, a.account_type) == ("myacct", "42", "BUSINESS")
    assert [b.name for b in p.get_boards("AT")] == ["Beauty", "Makeup"]


def test_create_pin_request_and_no_fabricated_url():
    seen = {}

    def h(req):
        seen["req"] = req
        return httpx.Response(201, json={"id": "999", "board_id": "B1", "link": "https://ekaro.in/x/1"})
    created = provider(h).create_pin("AT", PAYLOAD)
    req = seen["req"]
    assert req.method == "POST" and str(req.url) == "https://api.pinterest.com/v5/pins"
    assert req.headers["authorization"] == "Bearer AT"
    body = json.loads(req.content)
    assert body["board_id"] == "B1" and body["link"] == "https://ekaro.in/x/1" and body["alt_text"] == "A"
    assert body["media_source"]["source_type"] == "image_base64" and body["media_source"]["content_type"] == "image/png"
    assert base64.b64decode(body["media_source"]["data"]) == PAYLOAD.image_png
    assert created.pin_id == "999" and created.url is None  # Pinterest returned no URL: we do not invent one


def test_pin_url_only_when_api_provides_it():
    p = provider(lambda r: httpx.Response(201, json={"id": "5", "pin_url": "https://www.pinterest.com/pin/5/"}))
    assert p.create_pin("AT", PAYLOAD).url == "https://www.pinterest.com/pin/5/"


@pytest.mark.parametrize("status,body,exc", [
    (401, {}, E.AuthExpired), (403, {}, E.PermissionDenied), (429, {}, E.RateLimited),
    (500, {}, E.ServiceUnavailable), (503, {}, E.ServiceUnavailable),
    (400, {"message": "Invalid board id"}, E.InvalidBoard),
    (400, {"message": "Image could not be decoded"}, E.InvalidImage),
    (400, {"message": "link is not a valid URL"}, E.InvalidURL),
    (400, {"message": "something else"}, E.BadRequest), (404, {}, E.InvalidBoard)])
def test_error_mapping(status, body, exc):
    p = provider(lambda r: httpx.Response(status, json=body, headers={"retry-after": "30"}))
    with pytest.raises(exc) as ex:
        p.create_pin("SECRET-ACCESS-TOKEN", PAYLOAD)
    assert "SECRET-ACCESS-TOKEN" not in str(ex.value)
    if exc is E.RateLimited:
        assert ex.value.retry_after == 30 and ex.value.retryable
    if exc is E.AuthExpired:
        assert ex.value.needs_reconnect


def test_ambiguity_flags():
    def timeout(req):
        raise httpx.ReadTimeout("slow")

    def refused(req):
        raise httpx.ConnectError("down")
    with pytest.raises(E.ServiceUnavailable) as ex:
        provider(timeout).create_pin("AT", PAYLOAD)
    assert ex.value.ambiguous  # request may have reached Pinterest
    with pytest.raises(E.ServiceUnavailable) as ex2:
        provider(refused).create_pin("AT", PAYLOAD)
    assert not ex2.value.ambiguous
    with pytest.raises(E.ServiceUnavailable) as ex3:
        provider(lambda r: httpx.Response(504)).create_pin("AT", PAYLOAD)
    assert ex3.value.ambiguous


def test_analytics_parsing():
    def h(req):
        assert req.url.path.endswith("/pins/77/analytics")
        assert set(req.url.params["metric_types"].split(",")) == {"IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK"}
        return httpx.Response(200, json={"all": {"summary_metrics": {"IMPRESSION": 100, "SAVE": 5, "PIN_CLICK": 9,
                                                                       "OUTBOUND_CLICK": 3}}})
    m = provider(h).get_analytics("AT", "77")
    assert (m.impressions, m.saves, m.pin_clicks, m.outbound_clicks) == (100, 5, 9, 3)
    empty = provider(lambda r: httpx.Response(200, json={})).get_analytics("AT", "77")
    assert empty.impressions == 0 and empty.raw == {}


def test_revoke():
    seen = {}

    def h(req):
        seen["req"] = req
        return httpx.Response(200, json={})
    assert provider(h).revoke("AT") and str(seen["req"].url) == "https://api.pinterest.com/v5/oauth/token/revoke"
    assert "token=AT" in seen["req"].content.decode()
    assert not provider(lambda r: httpx.Response(401)).revoke("AT")
