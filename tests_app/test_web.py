import io
import re

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Pin, Product
from tests_app.conftest import Web, make_settings


def ids(db, model):
    return [r.id for r in db.query(model).order_by(model.id)]


# ---- auth / security ---------------------------------------------------------------------------------------
def test_pages_require_login(app):
    c = TestClient(app, follow_redirects=False)
    for path in ["/dashboard", "/products", "/pins", "/queue", "/create", "/templates", "/settings", "/analytics"]:
        r = c.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    assert c.get("/api/products").status_code == 401
    assert c.get("/assets/pin/1").status_code == 303
    assert c.get("/api/health").status_code == 200


def test_login_success_failure_and_logout(app):
    w = Web(app)
    assert w.login("wrong").headers["location"] == "/login"
    assert "Wrong username or password" in w.get("/login").text
    assert w.login().headers["location"] == "/dashboard"
    assert w.get("/dashboard").status_code == 200
    w.post("/logout")
    assert w.get("/dashboard").status_code == 303


def test_csrf_required_on_post(web):
    assert web.c.post("/products/discover", data={"request_text": "x"}).status_code == 403
    assert web.c.post("/logout", data={"csrf_token": "forged"}).status_code == 403
    assert web.c.post("/api/products/discover", json={"request": "x"}).status_code == 403


def test_security_headers(web):
    r = web.get("/dashboard")
    assert "default-src 'self'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "<script>" not in r.text.replace('<script src="/static/app.js"></script>', "")


def test_login_is_rate_limited(tmp_path):
    app = create_app(make_settings(tmp_path, LOGIN_RATE_LIMIT="3"))
    w = Web(app)
    codes = [w.login("bad").status_code for _ in range(5)]
    assert codes[:3] == [303, 303, 303] and codes[3] == 429


def test_errors_are_safe(web):
    r = web.get("/pins/99999")
    assert r.status_code == 404 and "Traceback" not in r.text
    assert web.get("/nope").status_code == 404
    assert web.get("/assets/pin/99999").status_code == 404


def test_unhandled_error_hides_details(app, web, monkeypatch):
    from app.web import pages
    monkeypatch.setattr(pages, "dashboard_stats", lambda db: 1 / 0)
    c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    c.cookies.update(web.c.cookies)
    r = c.get("/dashboard")
    assert r.status_code == 500 and "ZeroDivision" not in r.text and "reference" in r.text


def test_xss_in_product_data_is_escaped(web, db):
    web.post("/products/add", {"provider": "earnkaro", "title": '<script>alert(1)</script>"><img src=x onerror=alert(2)>',
                               "affiliate_url": "https://ekaro.in/x/xss", "brand": "<b>b</b>"})
    html = web.get("/products").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html and "<img src=x" not in html


# ---- the complete user journey --------------------------------------------------------------------------------
def test_full_workflow_discover_to_published(web, app, db):
    # 1. discover (demo catalog works offline) -> select
    r = web.post("/products/discover", {"request_text": "Find 6 skincare products suitable for Pinterest", "category": "Skincare",
                                        "providers": "demo", "limit": "6"})
    assert r.status_code == 303
    page = web.get("/products").text
    assert "Serum" in page or "Cleanser" in page or "Moisturizer" in page and "content-opportunity" in page.lower()
    pid = ids(db, Product)[0]
    assert web.post(f"/products/{pid}/status", {"status": "selected"}).status_code == 303
    detail = web.get(f"/products/{pid}").text
    assert "Affiliate destination" in detail and "example.com/demo-affiliate" in detail

    # 2. create 3 pins (copy + designs)
    r = web.post("/create", {"product_id": str(pid), "count": "3", "template_keys": ["minimal_card", "spotlight", "collage"],
                             "show_disclosure": "1"})
    assert r.status_code == 303
    db.expire_all()
    pins = db.query(Pin).order_by(Pin.id).all()
    assert len(pins) == 3 and all(p.status == "ready_for_review" for p in pins)
    p0 = pins[0]
    img = web.get(f"/assets/pin/{p0.id}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content[:4] == b"\x89PNG"
    page = web.get(f"/pins/{p0.id}").text
    assert "Approve" in page and "Regenerate" in page and "Edit" in page

    # 3. publishing before approval / before connecting is blocked
    web.post(f"/pins/{p0.id}/publish")
    db.expire_all()
    assert db.get(Pin, p0.id).status == "ready_for_review"

    # 4. approve
    web.post(f"/pins/{p0.id}/approve")
    db.expire_all()
    assert db.get(Pin, p0.id).status == "approved"
    page = web.get(f"/pins/{p0.id}").text
    assert "Pinterest is not connected" in page  # clear instruction instead of a fake success

    # 5. connect Pinterest (mock OAuth): button -> authorization page -> callback
    r = web.post("/pinterest/connect")
    assert r.status_code == 303 and r.headers["location"].startswith("/pinterest/mock-authorize?state=")
    auth_page = web.get(r.headers["location"])
    assert "Mock Pinterest" in auth_page.text and "SIMULATION MODE" in auth_page.text
    state = re.search(r"state=([\w\-]+)", r.headers["location"]).group(1)
    r = web.post("/pinterest/mock-authorize", {"state": state, "decision": "approve"})
    assert r.headers["location"].startswith("/pinterest/callback?code=mock-code")
    r = web.get(r.headers["location"])
    assert r.status_code == 303
    settings_page = web.get("/settings").text
    assert "Connected" in settings_page and "@mockaccount" in settings_page and "Beauty Finds" in settings_page
    assert "mock-access" not in settings_page and "mock-refresh" not in settings_page  # no tokens in the UI

    # 6. default board
    web.post("/pinterest/default-board", {"board_id": "b-beauty"})
    assert "Default board: <b>Beauty Finds</b>" in web.get("/settings").text.replace("\n", " ") or "Beauty Finds" in web.get("/settings").text

    # 7. choose a board for this pin and PUBLISH
    page = web.get(f"/pins/{p0.id}").text
    assert 'name="board_id"' in page and "Publish" in page and "Makeup" in page
    r = web.post(f"/pins/{p0.id}/publish", {"board_id": "b-makeup"})
    flash = web.follow(r)
    assert "SIMULATED publish" in flash.text and "Pinterest pin ID:" in flash.text
    db.expire_all()
    assert db.get(Pin, p0.id).status == "published"
    result = web.get(f"/pins/{p0.id}").text
    assert "Published (simulated)" in result and "Pinterest Pin ID" in result and "Makeup" in result
    assert ">View Pin</a>" not in result and "pinterest.com/pin/" not in result  # no URL from the API: none invented
    mock = app.state.pinterest.provider
    (pid_str, sent), = mock.pins.items()
    assert sent["board_id"] == "b-makeup" and sent["link"].startswith("https://example.com/demo-affiliate/")
    assert pid_str in result

    # 8. duplicate protection through the UI: a second pin of the same product + variation
    p1 = pins[1]
    web.post(f"/pins/{p1.id}/approve")
    p2_concept_clash = db.get(Pin, p1.id)
    p2_concept_clash.concept_key = db.get(Pin, p0.id).concept_key
    db.commit()
    r = web.post(f"/pins/{p1.id}/publish", {"board_id": "b-makeup"})
    assert "confirm=1" in r.headers["location"]
    confirm = web.follow(r)
    assert "Possible duplicate" in confirm.text and "Publish anyway" in confirm.text and "Cancel" in confirm.text
    assert len(mock.pins) == 1
    web.post(f"/pins/{p1.id}/publish", {"board_id": "b-makeup", "force": "1"})
    assert len(mock.pins) == 2

    # 9. queue + dashboard + analytics pages
    queue = web.get("/queue").text
    assert "Publishing queue" in queue and "published" in queue
    dash = web.get("/dashboard").text
    assert "Pinterest connected" in dash and "@mockaccount" in dash
    web.post("/analytics/sync")
    an = web.get("/analytics").text
    assert "Reported by Pinterest" in an and "Calculated by this app" in an and "Pinterest" in an

    # 10. disconnect
    web.post("/pinterest/disconnect")
    assert "Not connected" in web.get("/settings").text


def test_failed_publish_shows_reason_and_retry(web, app, db):
    from app.pinterest.errors import RateLimited
    web.post("/products/discover", {"request_text": "5 beauty", "providers": "demo", "limit": "5"})
    pid = ids(db, Product)[0]
    web.post("/create", {"product_id": str(pid), "count": "1"})
    pin = db.query(Pin).first()
    web.post(f"/pins/{pin.id}/approve")
    r = web.post("/pinterest/connect")
    state = re.search(r"state=([\w\-]+)", r.headers["location"]).group(1)
    web.get(web.post("/pinterest/mock-authorize", {"state": state, "decision": "approve"}).headers["location"])
    web.post("/pinterest/default-board", {"board_id": "b-beauty"})
    app.state.pinterest.provider.fail_next(RateLimited())
    page = web.follow(web.post(f"/pins/{pin.id}/publish"))
    assert "Publishing failed" in page.text and "rate limit" in page.text.lower()
    db.expire_all()
    assert db.get(Pin, pin.id).status == "failed"
    detail = web.get(f"/pins/{pin.id}").text
    assert "Reason:" in detail and "Retry publish" in detail
    assert "Retry" in web.get("/queue").text
    web.post(f"/pins/{pin.id}/publish")
    db.expire_all()
    assert db.get(Pin, pin.id).status == "published"


def test_oauth_callback_rejects_forged_state_and_denial(web):
    web.post("/pinterest/connect")
    r = web.get("/pinterest/callback?code=mock-code&state=forged")
    assert r.status_code == 303
    assert "Could not connect Pinterest" in web.get("/settings").text
    r = web.get("/pinterest/callback?error=access_denied")
    assert "cancelled or denied" in web.follow(r).text
    assert "Not connected" in web.get("/settings").text


def test_real_provider_unconfigured_shows_setup_hint(tmp_path):
    app = create_app(make_settings(tmp_path, PINTEREST_PROVIDER="real", PINTEREST_REDIRECT_URI=""))
    w = Web(app)
    w.login()
    page = w.get("/settings").text
    assert "PINTEREST_CLIENT_ID" in page and "SIMULATION MODE" not in page
    r = w.post("/pinterest/connect")
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert "PINTEREST_CLIENT_ID" in w.follow(r).text


def test_disclosure_setting_page(web, db):
    web.post("/settings/disclosure", {"disclosure_text": "I may earn a commission.", "image_label": "Ad"})
    page = web.get("/settings").text
    assert "I may earn a commission." in page and "legally sufficient" in page
    r = web.post("/settings/disclosure", {"disclosure_text": "", "image_label": "Ad"})
    assert "cannot be empty" in web.follow(r).text


def test_csv_upload_and_sample(web, db):
    csv_bytes = b"title,affiliate_url,brand,category\nUploaded serum,https://ekaro.in/u/1,Acme,Skincare\n"
    r = web.c.post("/products/import", data={"provider": "earnkaro", "csrf_token": web.token()},
                   files={"file": ("p.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert "1 new" in web.follow(r).text
    assert "Uploaded serum" in web.get("/products").text
    assert web.get("/products/sample.csv?provider=amazon").headers["content-type"].startswith("text/csv")


def test_templates_page_renders_samples(web):
    page = web.get("/templates").text
    assert page.count("sample.png") == 5
    r = web.get("/templates/spotlight/sample.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert web.get("/templates/nope/sample.png").status_code == 404


# ---- JSON API ------------------------------------------------------------------------------------------------
def test_api_workflow_with_duplicate_409(web, app, db):
    r = web.api_post("/api/products/discover", {"request": "Find 4 skincare products", "providers": ["demo"], "limit": 4})
    assert r.status_code == 200 and len(r.json()["products"]) == 4
    pid = r.json()["products"][0]["id"]
    gen = web.api_post("/api/pins/generate", {"product_id": pid, "count": 2})
    assert gen.status_code == 200
    a, b = gen.json()
    assert a["status"] == "ready_for_review" and a["destination_url"].startswith("https://example.com/demo-affiliate/")
    assert web.api_post(f"/api/pins/{a['id']}/publish").status_code == 400  # not approved
    assert web.api_post(f"/api/pins/{a['id']}/approve").json()["status"] == "approved"
    status = web.get("/api/pinterest/status").json()
    assert status["connected"] is False and status["simulated"] is True
    tok = web.post("/pinterest/connect").headers["location"]
    state = re.search(r"state=([\w\-]+)", tok).group(1)
    web.get(web.post("/pinterest/mock-authorize", {"state": state, "decision": "approve"}).headers["location"])
    web.post("/pinterest/default-board", {"board_id": "b-skin"})
    st = web.get("/api/pinterest/status").json()
    assert st["connected"] and st["username"] == "mockaccount" and st["default_board_id"] == "b-skin" and st["can_publish"]
    assert "token" not in str(st).lower()
    ok = web.api_post(f"/api/pins/{a['id']}/publish")
    assert ok.status_code == 200 and ok.json()["pinterest_pin_id"] and ok.json()["simulated"] and ok.json()["pinterest_url"] is None
    db.expire_all()
    db.get(Pin, b["id"]).concept_key = db.get(Pin, a["id"]).concept_key
    db.commit()
    web.api_post(f"/api/pins/{b['id']}/approve")
    dup = web.api_post(f"/api/pins/{b['id']}/publish")
    assert dup.status_code == 409 and dup.json()["duplicate"] is True and dup.json()["findings"] and dup.json()["error"]
    assert web.api_post(f"/api/pins/{b['id']}/publish", {"force": True}).status_code == 200
    assert web.get("/api/stats").json()["pins_published"] == 2
    patch = web.c.patch(f"/api/pins/{b['id']}", json={"headline": "x"}, headers={"X-CSRF-Token": web.c.get("/api/csrf").json()["csrf_token"]})
    assert patch.status_code == 400  # published pins cannot be edited


def test_api_publish_failure_is_structured(web, app):
    from app.pinterest.errors import RateLimited
    web.api_post("/api/products/discover", {"request": "2 skincare", "providers": ["demo"], "limit": 2})
    pid = web.get("/api/products").json()[0]["id"]
    pin = web.api_post("/api/pins/generate", {"product_id": pid, "count": 1}).json()[0]
    web.api_post(f"/api/pins/{pin['id']}/approve")
    state = re.search(r"state=([\w\-]+)", web.post("/pinterest/connect").headers["location"]).group(1)
    web.get(web.post("/pinterest/mock-authorize", {"state": state, "decision": "approve"}).headers["location"])
    web.post("/pinterest/default-board", {"board_id": "b-beauty"})
    app.state.pinterest.provider.fail_next(RateLimited())
    r = web.api_post(f"/api/pins/{pin['id']}/publish")
    assert r.status_code == 502 and r.json()["code"] == "rate_limited" and r.json()["retryable"] is True
    assert web.get(f"/api/pins/{pin['id']}").json()["status"] == "failed"
    app.state.pinterest.provider.revoke_everything()
    r = web.api_post(f"/api/pins/{pin['id']}/publish")
    assert r.status_code == 401 and r.json()["needs_reconnect"] is True


def test_api_validation(web):
    assert web.api_post("/api/pins/generate", {"product_id": 1, "count": 99}).status_code == 422
    assert web.api_post("/api/pins/generate", {"product_id": 424242}).status_code == 404
    assert web.api_post("/api/products/discover", {"request": "x" * 1000}).status_code == 422
    assert web.c.get("/api/openapi.json").status_code == 200  # dev only; disabled in production


def test_templates_have_no_inline_styles_or_scripts():
    """The strict CSP (style-src/script-src 'self') silently drops inline styles: keep templates clean."""
    from pathlib import Path
    for f in Path("app/templates").glob("*.html"):
        text = f.read_text()
        assert ' style="' not in text, f"{f.name} uses an inline style (blocked by CSP)"
        assert "<style" not in text and "onclick=" not in text, f.name
        assert not re.search(r"<script(?![^>]*src=)", text), f.name


def test_production_mode_hardening(tmp_path):
    app = create_app(make_settings(tmp_path, APP_ENV="production", ADMIN_PASSWORD="a-Strong-prod-pass-9", SECRET_KEY="p" * 48))
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)  # Secure cookies need https
    assert c.get("/api/docs").status_code == 404 and c.get("/api/openapi.json").status_code == 404
    token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    r = c.post("/login", data={"username": "admin", "password": "a-Strong-prod-pass-9", "csrf_token": token})
    cookie = r.headers["set-cookie"].lower()
    assert r.status_code == 303 and "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie


def test_dev_session_cookie_flags(app):
    w = Web(app)
    cookie = w.login().headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
