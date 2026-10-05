import json
from decimal import Decimal

import httpx
import pytest

from app.config import build_settings
from app.providers.amazon import AmazonProvider, parse_asin
from app.providers.base import Capability, NotSupported, ProductData, ProviderError, ProviderNotConfigured
from app.providers.demo import DemoProvider
from app.providers.earnkaro import EarnKaroProvider
from app.providers.registry import build_registry


def S(**kw):
    env = {"DATA_DIR": "/tmp/engine-tests-prov", "ADMIN_PASSWORD": "pw-123456", "SECRET_KEY": "k" * 40}
    env.update(kw)
    return build_settings(env)


def test_registry_has_all_providers():
    reg = build_registry(S())
    assert set(reg) == {"amazon", "earnkaro", "demo"}
    assert "demo" not in build_registry(S(ENABLE_DEMO_PROVIDER="false"))


def test_demo_search_and_limit():
    out = DemoProvider().search_products("lip", category="Beauty", limit=5)
    assert len(out) == 5 and all(p.affiliate_url.startswith("https://example.com/") for p in out)
    assert all(p.category in ("Beauty", "Makeup", "Skincare") for p in out)
    assert DemoProvider().is_demo


def test_amazon_without_credentials_is_import_only():
    a = AmazonProvider(S())
    assert not a.is_configured() and a.capabilities == {Capability.IMPORT}
    with pytest.raises(ProviderNotConfigured):
        a.search_products("lipstick")


def test_amazon_asin_parsing_and_links():
    assert parse_asin("B0ABCDEF12") == "B0ABCDEF12"
    assert parse_asin("https://www.amazon.in/Some-Name/dp/B0ABCDEF12/ref=x?th=1") == "B0ABCDEF12"
    assert parse_asin("https://example.com/dp/B0ABCDEF12") == "B0ABCDEF12"  # host checked by validate_affiliate_url
    assert parse_asin("nonsense") is None
    a = AmazonProvider(S(AMAZON_PARTNER_TAG="mytag-21"))
    assert a.build_affiliate_url("B0ABCDEF12") == "https://www.amazon.in/dp/B0ABCDEF12?tag=mytag-21"
    assert a.validate_affiliate_url("https://www.amazon.in/dp/B0ABCDEF12?tag=mytag-21")[0]
    assert not a.validate_affiliate_url("https://www.amazon.in/dp/B0ABCDEF12")[0]            # tag missing
    assert not a.validate_affiliate_url("https://www.amazon.in/dp/B0ABCDEF12?tag=other-21")[0]  # wrong tag
    assert not a.validate_affiliate_url("https://evil.example/dp/B0ABCDEF12?tag=mytag-21")[0]   # wrong host


def test_amazon_import_row_builds_tagged_link():
    a = AmazonProvider(S(AMAZON_PARTNER_TAG="mytag-21"))
    d = a.import_row({"asin": "B0ABCDEF12", "title": "T", "price": "₹1,299"})
    assert d.affiliate_url.endswith("tag=mytag-21") and d.price == Decimal("1299")
    with pytest.raises(ProviderError):
        a.import_row({"asin": "bad", "title": "T"})


def _amazon_http(calls):
    def handler(request: httpx.Request):
        calls.append(request)
        if "auth/o2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600, "token_type": "bearer"})
        body = json.loads(request.content)
        if body.get("keywords") == "denied":
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"searchResult": {"items": [{
            "asin": "B0ABCDEF12", "detailPageURL": "https://www.amazon.in/dp/B0ABCDEF12?tag=mytag-21",
            "images": {"primary": {"large": {"url": "https://m.media-amazon.com/images/I/x.jpg"}}},
            "itemInfo": {"title": {"displayValue": "Great Serum 30ml"}, "byLineInfo": {"brand": {"displayValue": "Brandy"}},
                         "features": {"displayValues": ["Feature one", "Feature two"]}},
            "offersV2": {"listings": [{"price": {"money": {"amount": 499.0, "currency": "INR"}}}]}}]}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_amazon_creators_api_request_shape_and_parsing():
    calls = []
    a = AmazonProvider(S(AMAZON_CREDENTIAL_ID="cid-abc123", AMAZON_CREDENTIAL_SECRET="csecret-xyz789",
                         AMAZON_PARTNER_TAG="mytag-21"), _amazon_http(calls))
    assert a.is_configured() and Capability.SEARCH in a.capabilities
    out = a.search_products("serum", category="Skincare", limit=3)
    token_req, search_req = calls[0], calls[1]
    assert str(token_req.url) == "https://api.amazon.co.uk/auth/o2/token"  # India = credential version 3.2
    tbody = json.loads(token_req.content)
    assert tbody["grant_type"] == "client_credentials" and tbody["scope"] == "creatorsapi::default"
    assert str(search_req.url) == "https://creatorsapi.amazon/catalog/v1/searchItems"
    assert search_req.headers["authorization"] == "Bearer tok-123" and search_req.headers["x-marketplace"] == "www.amazon.in"
    sbody = json.loads(search_req.content)
    assert sbody["partnerTag"] == "mytag-21" and sbody["partnerType"] == "Associates" and "keywords" in sbody
    p = out[0]
    assert (p.external_id, p.title, p.brand, p.price) == ("B0ABCDEF12", "Great Serum 30ml", "Brandy", Decimal("499.0"))
    assert p.affiliate_url == "https://www.amazon.in/dp/B0ABCDEF12?tag=mytag-21" and p.price_fetched_at is not None
    a.search_products("again")
    assert sum("auth/o2/token" in str(c.url) for c in calls) == 1  # token cached


def test_amazon_error_messages_do_not_leak_secrets():
    calls = []
    a = AmazonProvider(S(AMAZON_CREDENTIAL_ID="cid-abc123", AMAZON_CREDENTIAL_SECRET="csecret-xyz789",
                         AMAZON_PARTNER_TAG="mytag-21"), _amazon_http(calls))
    with pytest.raises(ProviderError) as ex:
        a.search_products("denied")
    assert "csecret-xyz789" not in str(ex.value) and "tok-123" not in str(ex.value)


def test_earnkaro_is_import_only_and_validates_links():
    e = EarnKaroProvider(S())
    assert e.capabilities == {Capability.IMPORT}
    with pytest.raises(NotSupported):
        e.search_products("x")
    with pytest.raises(NotSupported):
        e.get_affiliate_link(ProductData(external_id="1", title="t"))
    assert e.validate_affiliate_url("https://ekaro.in/enkr2020/abc")[0]
    ok, msg = e.validate_affiliate_url("https://www.nykaa.com/product")
    assert not ok and "Profit Link" in msg
    d = e.import_row({"title": "Lip balm", "affiliate_url": "https://ekaro.in/x/1", "price": "249"})
    assert d.affiliate_url == "https://ekaro.in/x/1" and d.price == Decimal("249")
    with pytest.raises(ProviderError):
        e.import_row({"title": "no link"})
