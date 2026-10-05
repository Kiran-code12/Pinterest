import pytest
from sqlalchemy import select

from app.models import AffiliateLink, Product
from app.providers.base import ProductData, ProviderError
from app.providers.registry import sync_provider_rows
from app.services import products as P
from app.services.scoring import SCORE_LABEL, score_product


@pytest.fixture
def ctx(app, db):
    reg = app.state.registry
    return reg, sync_provider_rows(db, reg)


def test_discover_demo_and_unconfigured_amazon(db, ctx):
    reg, rows = ctx
    out = P.discover(db, reg, rows, "Find 8 beauty products suitable for Pinterest")
    assert len(out.products) == 8
    assert any("Amazon" in m and "skipped" in m for m in out.messages)
    assert any("EarnKaro" in m and "skipped" in m for m in out.messages)
    scores = [p.score for p in out.products]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    assert all(p.primary_link and p.primary_link.is_valid for p in out.products)


def test_parse_request():
    r = P.parse_request("Find 20 beauty products suitable for Pinterest")
    assert (r.count, r.category) == (20, "Beauty")
    assert P.parse_request("lipstick", "Makeup").category == "Makeup"
    assert P.parse_request("find 500 things").count == 100


def test_upsert_preserves_affiliate_link_and_dedupes(db, ctx):
    reg, rows = ctx
    ek, row = reg["earnkaro"], rows["earnkaro"]
    d = ProductData(external_id="k1", title="Rose Lip Balm", brand="B", affiliate_url="https://ekaro.in/a/1",
                    product_url="https://shop.example/p/1")
    r1 = P.upsert_product(db, ek, row, d, "import")
    assert r1.created and r1.product.primary_link.is_valid
    # re-import WITHOUT a link must not remove it
    r2 = P.upsert_product(db, ek, row, ProductData(external_id="k1", title="Rose Lip Balm", brand="B"), "import")
    assert not r2.created and r2.product.primary_link.affiliate_url == "https://ekaro.in/a/1"
    # an INVALID incoming link must not overwrite a valid one
    P.upsert_product(db, ek, row, ProductData(external_id="k1", title="Rose Lip Balm", brand="B",
                                              affiliate_url="https://plain-shop.example/p/1"), "import")
    db.refresh(r1.product)
    assert r1.product.primary_link.affiliate_url == "https://ekaro.in/a/1" and r1.product.primary_link.is_valid
    assert db.scalar(select(AffiliateLink).where(AffiliateLink.product_id == r1.product.id)) is not None


def test_plain_url_as_affiliate_is_flagged_invalid(db, ctx):
    reg, rows = ctx
    res = P.upsert_product(db, reg["earnkaro"], rows["earnkaro"],
                           ProductData(external_id="k2", title="Plain", affiliate_url="https://shop.example/p/2"), "manual")
    assert not res.product.primary_link.is_valid and res.warnings


def test_missing_link_warns(db, ctx):
    reg, rows = ctx
    res = P.upsert_product(db, reg["earnkaro"], rows["earnkaro"], ProductData(external_id="k3", title="No link"), "manual")
    assert res.product.primary_link is None and any("No affiliate link" in w for w in res.warnings)
    assert res.product.score < 80


def test_duplicate_detection_penalty(db, ctx):
    reg, rows = ctx
    ek, row = reg["earnkaro"], rows["earnkaro"]
    a = P.upsert_product(db, ek, row, ProductData(external_id="a", title="Velvet Lipstick Rose", brand="X",
                                                  affiliate_url="https://ekaro.in/1"), "import").product
    b = P.upsert_product(db, ek, row, ProductData(external_id="b", title="Rose Velvet Lipstick", brand="X",
                                                  affiliate_url="https://ekaro.in/2"), "import").product
    assert a.dedupe_key == b.dedupe_key and b.score_breakdown["duplicate_penalty"] < 0 and a.score_breakdown["duplicate_penalty"] == 0


def test_csv_import_earnkaro(db, ctx):
    reg, rows = ctx
    csv_text = (b"title,affiliate_url,brand,category,price,image_url\n"
                b"Good serum,https://ekaro.in/s/1,Acme,Skincare,499,https://example.com/a.jpg\n"
                b"Bad row,https://not-earnkaro.example/x,Acme,Skincare,10,\n"
                b",https://ekaro.in/s/2,Acme,Skincare,10,\n")
    out = P.import_csv(db, reg["earnkaro"], rows["earnkaro"], csv_text)
    assert out.created == 2 and len(out.errors) == 1  # missing title rejected
    bad = db.scalar(select(Product).where(Product.title == "Bad row"))
    assert bad is not None and not bad.primary_link.is_valid and any("Bad" in w or "Row" in w for w in out.warnings)


def test_csv_limits(db, ctx):
    reg, rows = ctx
    with pytest.raises(ProviderError, match="1MB"):
        P.import_csv(db, reg["earnkaro"], rows["earnkaro"], b"x" * 1_100_000)
    with pytest.raises(ProviderError, match="UTF-8"):
        P.import_csv(db, reg["earnkaro"], rows["earnkaro"], b"\xff\xfe\xfa")


def test_score_is_labelled_heuristic_and_bounded():
    assert "not a sales prediction" in SCORE_LABEL
    hi, br = score_product(title="Rose Matte Lipstick", brand="X", category="Makeup", description="A" * 60, price=299,
                           has_image=True, link_state="valid", is_duplicate=False)
    lo, _ = score_product(title="X", brand=None, category="Other", description=None, price=9999, has_image=False,
                          link_state="missing", is_duplicate=True)
    assert 0 <= lo < hi <= 100 and set(br) >= {"pinterest_suitability", "affiliate_availability", "duplicate_penalty"}
