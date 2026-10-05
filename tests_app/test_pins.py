import pytest
from sqlalchemy import select

from app.ai.local import LocalProvider
from app.models import Pin
from app.providers.base import ProductData
from app.providers.registry import sync_provider_rows
from app.services import pins as PN
from app.services import products as P


@pytest.fixture
def prod(app, db):
    reg = app.state.registry
    rows = sync_provider_rows(db, reg)
    return P.discover(db, reg, rows, "Find 5 skincare products", provider_keys=["demo"]).products[0]


def make(app, db, prod, **kw):
    return PN.create_pins(db, prod, LocalProvider(), app.state.settings, **kw)


def test_create_pins_lifecycle(app, db, prod):
    pins = make(app, db, prod, count=5)
    assert len(pins) == 5 and {p.template_key for p in pins} == {"minimal_card", "beauty_editorial", "collage", "top_picks", "spotlight"}
    for p in pins:
        assert p.status == "ready_for_review"
        assert p.destination_url == prod.primary_link.affiliate_url and p.affiliate_link_id == prod.primary_link.id
        assert p.current_asset and (p.current_asset.width, p.current_asset.height) == (1000, 1500)
        assert len(p.variations) == 1 and PN.check_destination(p) == []


def test_missing_or_invalid_link_blocks_creation(app, db):
    reg = app.state.registry
    rows = sync_provider_rows(db, reg)
    nolink = P.upsert_product(db, reg["earnkaro"], rows["earnkaro"], ProductData(external_id="n", title="No link"), "manual").product
    with pytest.raises(PN.PinError, match="no affiliate link"):
        PN.create_pins(db, nolink, LocalProvider(), app.state.settings)
    bad = P.upsert_product(db, reg["earnkaro"], rows["earnkaro"],
                           ProductData(external_id="b", title="Bad", affiliate_url="https://plain.example/x"), "manual").product
    with pytest.raises(PN.PinError, match="invalid"):
        PN.create_pins(db, bad, LocalProvider(), app.state.settings)


def test_destination_tampering_is_detected(app, db, prod):
    pin = make(app, db, prod, count=1)[0]
    pin.destination_url = "https://elsewhere.example/steal"
    assert any("no longer matches" in p for p in PN.check_destination(pin))
    with pytest.raises(PN.PinError):
        PN.approve(db, pin)
    pin.destination_url = prod.primary_link.original_url  # plain product URL instead of affiliate URL
    assert PN.check_destination(pin)


def test_edit_resets_approval_and_keeps_history(app, db, prod):
    pin = make(app, db, prod, count=1)[0]
    PN.approve(db, pin)
    assert pin.status == "approved" and pin.approved_at
    warnings = PN.update_pin(db, pin, {"headline": "Totally new headline", "seo_description": "Guaranteed 100% glow"},
                             app.state.settings)
    assert pin.status == "ready_for_review" and pin.approved_at is None and pin.headline == "Totally new headline"
    assert warnings and len(pin.variations) == 2 and pin.variations[-1].source == "manual" and len(pin.assets) == 2
    assert pin.destination_url == prod.primary_link.affiliate_url  # destination is not editable


def test_edit_sanitizes_and_validates(app, db, prod):
    pin = make(app, db, prod, count=1)[0]
    PN.update_pin(db, pin, {"headline": "Hi\x00 there" + "x" * 500, "keywords": "#Lip, lip ,  Glow"}, app.state.settings)
    assert "\x00" not in pin.headline and len(pin.headline) <= 90 and pin.keywords == ["lip", "glow"]
    with pytest.raises(PN.PinError):
        PN.update_pin(db, pin, {"template_key": "nope"}, app.state.settings)
    with pytest.raises(PN.PinError):
        PN.update_pin(db, pin, {"seo_title": ""}, app.state.settings)


def test_regenerate_copy_and_image(app, db, prod):
    pin = make(app, db, prod, count=1)[0]
    first = pin.headline
    PN.regenerate_copy(db, pin, LocalProvider(), app.state.settings)
    assert pin.headline != first and len(pin.variations) == 2
    PN.regenerate_image(db, pin, app.state.settings, "spotlight")
    assert pin.template_key == "spotlight" and len(pin.assets) == 3


def test_reject_and_state_rules(app, db, prod):
    pin = make(app, db, prod, count=1)[0]
    PN.reject(db, pin)
    assert pin.status == "rejected"
    PN.approve(db, pin)  # a rejected pin can be approved again after review
    with pytest.raises(PN.PinError):
        PN.approve(db, pin)  # already approved


def test_collage_uses_extra_products(app, db, prod):
    reg = app.state.registry
    rows = sync_provider_rows(db, reg)
    others = P.discover(db, reg, rows, "Find 3 makeup products", provider_keys=["demo"]).products
    pins = make(app, db, prod, count=3, template_keys=["collage"], extra_product_ids=[o.id for o in others[:2]])
    assert pins[0].collage_product_ids and len(db.scalars(select(Pin)).all()) == 3
