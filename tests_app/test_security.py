import logging

import pytest

from app.config import build_settings
from app.logging_setup import SecretRedactingFilter
from app.security import RateLimiter, UnsafeURL, hash_password, validate_fetch_url, validate_http_url, verify_password


def test_password_hashing():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass" and verify_password("s3cret-pass", h)
    assert not verify_password("wrong", h) and not verify_password("x", "garbage")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.com/a", "https://user:pw@x.com/", "https://x.com/a b",
                                 "", "https:///nohost", "https://x.com/" + "a" * 3000])
def test_bad_urls_rejected(url):
    with pytest.raises(UnsafeURL):
        validate_http_url(url)


def test_good_url_accepted():
    assert validate_http_url("https://ekaro.in/enkr2020/abc?x=1") == "https://ekaro.in/enkr2020/abc?x=1"


def test_ssrf_guard_blocks_private_and_loopback():
    def resolver_for(ip):
        def resolve(host, port):
            return [(2, 1, 6, "", (ip, 0))]
        return resolve
    private, loop, meta, public = (resolver_for(i) for i in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "93.184.216.34"))
    for r in (private, loop, meta):
        with pytest.raises(UnsafeURL):
            validate_fetch_url("https://evil.example/x.png", resolver=r)
    assert validate_fetch_url("https://cdn.example/x.png", resolver=public)


def test_rate_limiter():
    rl = RateLimiter()
    assert all(rl.allow("a", 3) for _ in range(3))
    assert not rl.allow("a", 3) and rl.allow("b", 3)


def test_production_requires_strong_secrets():
    with pytest.raises(RuntimeError):
        build_settings({"APP_ENV": "production", "ADMIN_PASSWORD": "change-me", "SECRET_KEY": "x" * 40})
    with pytest.raises(RuntimeError):
        build_settings({"APP_ENV": "production", "ADMIN_PASSWORD": "a-strong-one-123", "SECRET_KEY": "short"})
    s = build_settings({"APP_ENV": "production", "ADMIN_PASSWORD": "a-strong-one-123", "SECRET_KEY": "x" * 40})
    assert s.is_prod


def test_settings_repr_hides_secrets():
    s = build_settings({"DATA_DIR": "/tmp/x-engine", "ADMIN_PASSWORD": "pw-123456", "PINTEREST_CLIENT_SECRET": "supersecret-value"})
    assert "supersecret-value" not in repr(s)


def test_log_filter_redacts_secrets(caplog):
    f = SecretRedactingFilter(["supersecret-value"])
    rec = logging.LogRecord("x", logging.INFO, "", 0, "token is supersecret-value ok", None, None)
    f.filter(rec)
    assert "supersecret-value" not in rec.getMessage() and "***" in rec.getMessage()
