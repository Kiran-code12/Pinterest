"""End-to-end tests of the MVP workflow: product -> pins saved as DRAFTS -> Draft Library -> copy / download ->
mark published manually.  Direct publishing is OFF (the default); no Pinterest request may ever happen."""
import csv
import io
import re
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, text

from app.ai.local import LocalProvider
from app.main import create_app
from app.models import Pin, PinAsset, PinVariation, Product, PublishedPin
from app.pinterest.disabled import DisabledPinterestProvider
from app.pinterest.errors import DirectPublishingDisabled
from app.pinterest.mock import MockPinterestProvider
from app.providers.registry import sync_provider_rows
from app.services import analytics as AN
from app.services import app_settings
from app.services import drafts as D
from app.services import pins as PN
from app.services import products as P
from app.services import publishing as PB
from tests_app.conftest import Web, make_settings


class Spy:
    """HTTP client that records every outgoing request and refuses to talk to anything."""

    def __init__(self):
        self.requests = []

        def handler(request):
            self.requests.append(str(request.url))
            return httpx.Response(500)
        self.client = httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def spy():
    return Spy()


@pytest.fixture
def app(tmp_path, spy):
    # the mock Pinterest provider is injected on purpose: with the feature off it must be ignored
    return create_app(make_settings(tmp_path, DIRECT_PUBLISHING_ENABLED="false"), http=spy.client,
                      pinterest_provider=MockPinterestProvider())


@pytest.fixture
def db(app):
    with app.state.db.session() as s:
        yield s


@pytest.fixture
def prods(app, db):
    reg = app.state.registry
    rows = sync_provider_rows(db, reg)
    return P.discover(db, reg, rows, "Find 6 skincare products", provider_keys=["demo"]).products


def make(app, db, product, n=3, **kw):
    return PN.create_pins(db, product, LocalProvider(), app.state.settings, count=n, **kw)


# ---- the gate ---------------------------------------------------------------------------------------------------
def test_pinterest_is_hard_disabled_by_default(app, db, prods, spy):
    assert isinstance(app.state.pinterest.provider, DisabledPinterestProvider)  # injected mock was ignored
    assert app.state.pinterest.get(db) is None and not app.state.settings.direct_publishing_enabled
    pin = make(app, db, prods[0], 1)[0]
    with pytest.raises(PB.PublishError, match="turned off"):
        PB.publish_pin(db, pin, app.state.settings, app.state.pinterest)
    assert "turned off" in PB.process_due(db, app.state.settings, app.state.pinterest)["messages"][0]
    assert "off" in AN.sync_from_pinterest(db, app.state.pinterest)["errors"][0]
    with pytest.raises(DirectPublishingDisabled):
        app.state.pinterest.begin({})
    with pytest.raises(DirectPublishingDisabled):
        app.state.pinterest.provider.create_pin("t", None)
    assert spy.requests == []


def test_settings_default_is_manual():
    from app.config import build_settings
    assert build_settings({"DATA_DIR": "/tmp/engine-tests-default", "ADMIN_PASSWORD": "x" * 12}).direct_publishing_enabled is False


# ---- drafts: creation, copy texts, downloads ---------------------------------------------------------------------
def test_every_generated_pin_is_a_draft(app, db, prods):
    pins = make(app, db, prods[0], 5)
    assert [p.status for p in pins] == ["draft"] * 5
    assert D.library_counts(db) == {"drafts": 5, "published": 0, "archived": 0, "all": 5}
    for p in pins:
        assert p.current_asset and p.variations and p.destination_url == prods[0].primary_link.affiliate_url


def test_copy_texts_are_exactly_what_gets_pasted(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    t = D.final_texts(db, pin)
    assert t["title"] == pin.seo_title and t["url"] == prods[0].primary_link.affiliate_url == pin.destination_url
    assert t["description"].startswith(pin.seo_description) and t["description"].endswith(app_settings.get(db, "disclosure_text"))
    assert t["all"] == f"{t['title']}\n\n{t['description']}\n\n{t['url']}" and pin.headline in t["alt"]
    app_settings.set_value(db, "disclosure_text", "I earn a small commission.")
    assert D.final_texts(db, pin)["description"].endswith("I earn a small commission.")
    pin.show_disclosure = False
    db.commit()
    assert "commission" not in D.final_texts(db, pin)["description"]


def test_download_png_and_jpg_are_pinterest_ready(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    data, name, media = D.image_download(pin, app.state.settings, "png")
    img = Image.open(io.BytesIO(data))
    assert media == "image/png" and data[:4] == b"\x89PNG" and img.size == (1000, 1500) and re.fullmatch(r"pin-\d+-[a-z0-9-]+\.png", name)
    jdata, jname, jmedia = D.image_download(pin, app.state.settings, "jpg")
    assert jmedia == "image/jpeg" and jdata[:2] == b"\xff\xd8" and Image.open(io.BytesIO(jdata)).size == (1000, 1500) and jname.endswith(".jpg")
    Path(pin.current_asset.path).unlink()
    with pytest.raises(PN.PinError, match="missing"):
        D.image_download(pin, app.state.settings, "png")


# ---- edit / regenerate -------------------------------------------------------------------------------------------
def test_edit_and_regenerate_keep_it_a_draft(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    old_headline, old_asset = pin.headline, pin.current_asset.id
    PN.regenerate_copy(db, pin, LocalProvider(), app.state.settings)
    assert pin.headline != old_headline and pin.status == "draft" and len(pin.variations) == 2
    PN.regenerate_image(db, pin, app.state.settings, "spotlight")
    assert pin.template_key == "spotlight" and pin.current_asset.id != old_asset and pin.status == "draft"
    PN.update_pin(db, pin, {"headline": "My own headline", "seo_title": "My own title"}, app.state.settings)
    assert D.final_texts(db, pin)["title"] == "My own title" and len(pin.variations) == 3


# ---- mark published manually ----------------------------------------------------------------------------------------
def test_mark_published_manually_and_undo(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    with pytest.raises(PN.PinError, match="invalid"):
        D.mark_published_manually(db, pin, "javascript:alert(1)")
    pub = D.mark_published_manually(db, pin, "https://www.pinterest.com/pin/123/", "Skincare\x00 Finds")
    assert pin.status == "published_manually" and pub.mode == "manual" and pub.provider == "manual" and not pub.simulated
    assert pub.board_name == "Skincare Finds" and pub.destination_url == pin.destination_url == prods[0].primary_link.affiliate_url
    assert pub.title == pin.seo_title and pub.asset_sha256 == pin.current_asset.sha256
    with pytest.raises(PN.PinError, match="already"):
        D.mark_published_manually(db, pin)
    with pytest.raises(PN.PinError, match="Use 'Move back"):
        PN.update_pin(db, pin, {"headline": "x"}, app.state.settings)
    with pytest.raises(PN.PinError):
        PN.regenerate_copy(db, pin, LocalProvider(), app.state.settings)
    D.move_back_to_draft(db, pin)
    assert pin.status == "draft" and not db.scalars(select(PublishedPin)).all()
    PN.update_pin(db, pin, {"headline": "editable again"}, app.state.settings)


def test_mark_published_requires_intact_affiliate_destination(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    pin.destination_url = "https://elsewhere.example/x"
    db.commit()
    with pytest.raises(PN.PinError, match="no longer matches"):
        D.mark_published_manually(db, pin)
    assert pin.status == "draft"


def test_published_hint_for_similar_pins(app, db, prods):
    a, b = make(app, db, prods[0], 2)
    b.concept_key = a.concept_key
    db.commit()
    D.mark_published_manually(db, a)
    hint = D.similar_published(db, b)
    assert len(hint) == 1 and hint[0]["same_variation"] and hint[0]["mode"] == "manual"
    assert D.similar_published(db, a) == []


def test_manual_analytics_entry_works_for_manual_publications(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    pub = D.mark_published_manually(db, pin)
    AN.record_metrics(db, pub, impressions=500, saves=20, pin_clicks=30, outbound_clicks=9, source="manual")
    assert AN.pinterest_reported(db)["outbound_clicks"] == 9
    assert AN.calculated(db)["outbound_click_rate"] == 1.8


# ---- archive / restore / delete -----------------------------------------------------------------------------------
def test_archive_and_restore_roundtrip(app, db, prods):
    a, b = make(app, db, prods[0], 2)
    D.mark_published_manually(db, b)
    for pin, before in ((a, "draft"), (b, "published_manually")):
        D.archive(db, pin)
        assert pin.status == "archived" and pin.status_before_archive == before
        with pytest.raises(PN.PinError, match="already archived"):
            D.archive(db, pin)
        with pytest.raises(PN.PinError, match="archived"):
            PN.update_pin(db, pin, {"headline": "x"}, app.state.settings)
    assert D.library_counts(db) == {"drafts": 0, "published": 0, "archived": 2, "all": 2}
    D.restore(db, a)
    D.restore(db, b)
    assert (a.status, b.status) == ("draft", "published_manually") and a.status_before_archive is None
    with pytest.raises(PN.PinError, match="Only archived"):
        D.restore(db, a)  # not archived any more


def test_delete_removes_rows_and_files_only_for_that_draft(app, db, prods):
    keep, gone = make(app, db, prods[0], 2)
    PN.regenerate_image(db, gone, app.state.settings, "spotlight")  # a second asset
    files = [Path(a.path) for a in gone.assets]
    keep_file = Path(keep.current_asset.path)
    D.mark_published_manually(db, gone)
    assert len(files) == 2 and all(f.exists() for f in files)
    assert D.delete(db, gone, app.state.settings) == 2
    assert not any(f.exists() for f in files) and keep_file.exists()
    assert db.get(Pin, gone.id) is None and db.get(Product, prods[0].id) is not None
    assert not db.scalars(select(PublishedPin)).all() and not db.scalars(select(PinAsset).where(PinAsset.pin_id == gone.id)).all()
    assert not db.scalars(select(PinVariation).where(PinVariation.pin_id == gone.id)).all()
    assert db.get(Pin, keep.id) is not None


def test_api_published_pins_cannot_be_deleted(app, db, prods):
    pin = make(app, db, prods[0], 1)[0]
    pin.status = "published"
    db.commit()
    with pytest.raises(PN.PinError, match="Archive"):
        D.delete(db, pin, app.state.settings)


def test_delete_never_touches_files_outside_assets(app, db, prods, tmp_path):
    pin = make(app, db, prods[0], 1)[0]
    outside = tmp_path.parent / "outside-assets.txt"
    outside.write_text("precious")
    pin.assets[0].path = str(outside)
    db.commit()
    D.delete(db, pin, app.state.settings)
    assert outside.read_text() == "precious"
    outside.unlink()


# ---- library, export, schema -------------------------------------------------------------------------------------
def test_library_filters_search_and_paging(app, db, prods, monkeypatch):
    a = make(app, db, prods[0], 2, template_keys=["spotlight"])
    b = make(app, db, prods[1], 1, template_keys=["collage"])
    D.mark_published_manually(db, b[0])
    D.archive(db, a[1])
    assert {p.id for p in D.list_library(db, "drafts")[0]} == {a[0].id}
    assert {p.id for p in D.list_library(db, "published")[0]} == {b[0].id}
    assert {p.id for p in D.list_library(db, "archived")[0]} == {a[1].id}
    assert D.list_library(db, "all")[1] == 3
    assert D.list_library(db, "all", template="collage")[1] == 1
    assert D.list_library(db, "all", q=prods[1].title[:12])[1] == 1 and D.list_library(db, "all", q="zzzz-none")[1] == 0
    assert D.list_library(db, "all", product_id=prods[0].id)[1] == 2
    monkeypatch.setattr(D, "PAGE_SIZE", 2)
    page1, total = D.list_library(db, "all", page=1)
    page2, _ = D.list_library(db, "all", page=2)
    assert total == 3 and len(page1) == 2 and len(page2) == 1 and not {p.id for p in page1} & {p.id for p in page2}


def test_export_zip_contains_images_and_safe_csv(app, db, prods):
    pins = make(app, db, prods[0], 2)
    PN.update_pin(db, pins[0], {"seo_title": "=HYPERLINK(\"http://evil\")", "seo_description": "+cmd|' /C calc'!A0"}, app.state.settings)
    z = zipfile.ZipFile(io.BytesIO(D.export_zip(db, pins, app.state.settings)))
    names = z.namelist()
    assert sum(n.endswith(".png") for n in names) == 2 and "drafts.csv" in names and "README.txt" in names
    rows = list(csv.DictReader(io.StringIO(z.read("drafts.csv").decode())))
    assert [r["draft_id"] for r in rows] == [str(p.id) for p in pins]
    assert rows[0]["title"].startswith("'=") and rows[0]["description"].startswith("'+")  # formulas neutralised
    assert rows[1]["affiliate_url"] == prods[0].primary_link.affiliate_url and rows[1]["image_file"] in names
    with pytest.raises(PN.PinError):
        D.export_zip(db, [], app.state.settings)


def test_schema_upgrade_adds_missing_columns(tmp_path):
    from app.db import Database
    d = Database(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    d.create_all()
    with d.engine.begin() as c:
        c.execute(text("ALTER TABLE pins DROP COLUMN status_before_archive"))
    assert d.add_missing_columns() == ["pins.status_before_archive"]
    assert d.add_missing_columns() == []
    with d.engine.connect() as c:
        c.execute(text("SELECT status_before_archive FROM pins")).fetchall()


# ---- the web UI, end to end ------------------------------------------------------------------------------------
@pytest.fixture
def web(app):
    w = Web(app)
    assert w.login().status_code == 303
    return w


def attr(html: str, value: str) -> bool:
    import html as h
    return f'data-copy-text="{h.escape(value, quote=True)}"' in html


def test_full_manual_workflow_in_the_ui(web, app, db, spy):
    # find product -> select
    web.post("/products/discover", {"request_text": "Find 6 skincare products", "category": "Skincare", "providers": "demo", "limit": "6"})
    pid = db.scalars(select(Product.id).order_by(Product.id)).first()
    web.post(f"/products/{pid}/status", {"status": "selected"})
    assert "example.com/demo-affiliate" in web.get(f"/products/{pid}").text

    # create pins -> saved as drafts, lands in the Draft Library
    r = web.post("/create", {"product_id": str(pid), "count": "3", "template_keys": ["minimal_card", "spotlight", "top_picks"], "show_disclosure": "1"})
    assert r.headers["location"] == f"/drafts?product_id={pid}"
    lib = web.follow(r).text
    assert "Saved 3 drafts" in lib and "Draft Library" in lib and "Drafts (3)" in lib
    db.expire_all()
    pins = db.scalars(select(Pin).order_by(Pin.id)).all()
    assert len(pins) == 3 and all(p.status == "draft" for p in pins)
    ids = [p.id for p in pins]
    p0 = pins[0]
    t0 = D.final_texts(db, p0)

    # library cards: download link + copy buttons carrying the exact texts
    for i in ids:
        assert f'href="/drafts/{i}/image.png"' in lib and f'href="/drafts/{i}"' in lib
    assert attr(lib, t0["title"]) and attr(lib, t0["description"]) and attr(lib, t0["url"])

    # draft page: preview, copy buttons, downloads
    page = web.get(f"/drafts/{p0.id}").text
    assert f'src="/assets/pin/{p0.id}"' in page and "Copy title" in page and "Copy description" in page and "Copy affiliate URL" in page
    assert attr(page, t0["all"]) and t0["url"] in page and "affiliate destination verified" in page
    assert "Connect Pinterest" not in page and "Publish to Pinterest" not in page and "Direct publishing" not in page
    png = web.get(f"/drafts/{p0.id}/image.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and "attachment" in png.headers["content-disposition"]
    assert Image.open(io.BytesIO(png.content)).size == (1000, 1500)
    jpg = web.get(f"/drafts/{p0.id}/image.jpg")
    assert jpg.headers["content-type"] == "image/jpeg" and jpg.content[:2] == b"\xff\xd8"
    assert web.get(f"/drafts/{p0.id}/image.gif").status_code == 404

    # edit, regenerate copy, regenerate design
    web.post(f"/pins/{p0.id}/edit", {"headline": "My edited headline", "seo_title": "My edited title", "seo_description": "Edited description text.",
                                     "keywords": "a, b", "cta": "Look", "template_key": "minimal_card", "show_disclosure": "1"})
    db.expire_all()
    assert db.get(Pin, p0.id).headline == "My edited headline"
    assert "My edited title" in web.get(f"/drafts/{p0.id}").text
    before = db.get(Pin, p0.id).headline
    web.post(f"/pins/{p0.id}/regenerate-copy")
    db.expire_all()
    assert db.get(Pin, p0.id).headline != before and db.get(Pin, p0.id).status == "draft"
    n_assets = len(db.get(Pin, p0.id).assets)
    web.post(f"/pins/{p0.id}/regenerate-image", {"template_key": "beauty_editorial"})
    db.expire_all()
    assert db.get(Pin, p0.id).template_key == "beauty_editorial" and len(db.get(Pin, p0.id).assets) == n_assets + 1

    # bulk ZIP of two drafts
    z = web.c.post("/drafts/export.zip", data={"ids": [str(ids[0]), str(ids[1])], "csrf_token": web.token()})
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert sum(n.endswith(".png") for n in zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == 2

    # mark published manually -> moves to the Published tab; analytics can take numbers
    web.post(f"/drafts/{p0.id}/mark-published", {"pinterest_url": "https://www.pinterest.com/pin/999/", "board_name": "Skincare"})
    db.expire_all()
    assert db.get(Pin, p0.id).status == "published_manually"
    assert "Published (1)" in web.get("/drafts").text and "Marked as published manually" in web.get(f"/drafts/{p0.id}").text
    assert f"/drafts/{p0.id}" in web.get("/drafts?view=published").text and f"/drafts/{p0.id}" not in web.get("/drafts").text
    pp = db.scalar(select(PublishedPin))
    web.post("/analytics/record", {"published_pin_id": str(pp.id), "impressions": "100", "saves": "5", "pin_clicks": "7", "outbound_clicks": "3"})
    an = web.get("/analytics").text
    assert "entered by you" in an and "Sync now" not in an
    web.post(f"/drafts/{p0.id}/move-to-draft")
    db.expire_all()
    assert db.get(Pin, p0.id).status == "draft"

    # archive -> restore -> delete
    web.post(f"/drafts/{ids[1]}/archive")
    assert f"/drafts/{ids[1]}" in web.get("/drafts?view=archived").text and f"/drafts/{ids[1]}" not in web.get("/drafts").text
    web.post(f"/drafts/{ids[1]}/restore")
    db.expire_all()
    assert db.get(Pin, ids[1]).status == "draft"
    f = Path(db.get(Pin, ids[2]).current_asset.path)
    assert f.exists()
    r = web.post(f"/drafts/{ids[2]}/delete")
    assert r.headers["location"] == "/drafts"
    db.expire_all()
    assert db.get(Pin, ids[2]) is None and not f.exists() and web.get(f"/drafts/{ids[2]}").status_code == 404

    # bulk archive
    web.c.post("/drafts/bulk-archive", data={"ids": [str(ids[0]), str(ids[1])], "csrf_token": web.token()})
    db.expire_all()
    assert {db.get(Pin, i).status for i in (ids[0], ids[1])} == {"archived"}

    # THE WHOLE TIME: nothing talked to Pinterest (or to anyone else)
    assert spy.requests == []


def test_pinterest_features_refuse_in_the_ui_and_api(web, app, db, spy):
    web.post("/products/discover", {"request_text": "3 skincare", "providers": "demo", "limit": "3"})
    pid = db.scalars(select(Product.id)).first()
    web.post("/create", {"product_id": str(pid), "count": "1"})
    pin = db.scalars(select(Pin)).first()
    settings = web.get("/settings").text
    assert "manual workflow" in settings.lower() and "Connect Pinterest" not in settings and "DIRECT_PUBLISHING_ENABLED" in settings
    r = web.post("/pinterest/connect")
    assert r.headers["location"] == "/settings" and "turned off" in web.follow(r).text
    r = web.post(f"/pins/{pin.id}/publish")
    assert "turned off" in web.follow(r).text and db.get(Pin, pin.id).status == "draft"
    assert web.get("/queue").headers["location"] == "/drafts"
    assert "Queue</a>" not in web.get("/dashboard").text
    web.api_post(f"/api/pins/{pin.id}/approve")
    r = web.api_post(f"/api/pins/{pin.id}/publish")
    assert r.status_code == 400 and "turned off" in str(r.json())
    assert web.get("/api/pinterest/status").json()["connected"] is False
    assert spy.requests == []


def test_draft_routes_are_protected(app, web, db):
    web.post("/products/discover", {"request_text": "2 skincare", "providers": "demo", "limit": "2"})
    pid = db.scalars(select(Product.id)).first()
    web.post("/create", {"product_id": str(pid), "count": "1"})
    pin = db.scalars(select(Pin)).first()
    anon = Web(app)
    for path in ["/drafts", f"/drafts/{pin.id}", f"/drafts/{pin.id}/image.png", f"/assets/pin/{pin.id}"]:
        assert anon.c.get(path).status_code == 303, path
    for path in [f"/drafts/{pin.id}/archive", f"/drafts/{pin.id}/delete", f"/drafts/{pin.id}/mark-published", "/drafts/export.zip", "/drafts/bulk-archive"]:
        assert web.c.post(path, data={}).status_code == 403, path  # no CSRF token
        assert anon.c.post(path, data={"csrf_token": "x"}).status_code in (303, 403), path
    assert db.get(Pin, pin.id).status == "draft"
    assert web.post("/drafts/export.zip", {}).status_code == 303  # nothing selected -> friendly redirect, no crash


def test_draft_text_is_escaped_in_the_ui(web, db):
    web.post("/products/discover", {"request_text": "2 skincare", "providers": "demo", "limit": "2"})
    pid = db.scalars(select(Product.id)).first()
    web.post("/create", {"product_id": str(pid), "count": "1"})
    pin = db.scalars(select(Pin)).first()
    web.post(f"/pins/{pin.id}/edit", {"headline": 'x"><script>alert(1)</script>', "seo_title": 't"><img src=x onerror=alert(2)>',
                                     "seo_description": "d", "cta": "go", "template_key": "minimal_card"})
    for path in ("/drafts", f"/drafts/{pin.id}", "/dashboard"):
        html = web.get(path).text
        assert "<script>alert(1)" not in html and "<img src=x" not in html, path  # no raw injected tags
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in web.get(f"/drafts/{pin.id}").text  # shown as harmless text


def test_api_draft_actions(web, db, spy):
    web.api_post("/api/products/discover", {"request": "3 skincare", "providers": ["demo"], "limit": 3})
    pid = web.get("/api/products").json()[0]["id"]
    pin = web.api_post("/api/pins/generate", {"product_id": pid, "count": 1}).json()[0]
    assert pin["status"] == "draft"
    texts = web.get(f"/api/pins/{pin['id']}/texts").json()
    assert texts["url"] == pin["destination_url"] and texts["title"] == pin["seo_title"] and set(texts) == {"title", "description", "url", "alt", "all"}
    assert web.api_post(f"/api/pins/{pin['id']}/mark-published", {"pinterest_url": "ftp://bad"}).status_code == 400
    assert web.api_post(f"/api/pins/{pin['id']}/mark-published", {"board_name": "Skin"}).json()["status"] == "published_manually"
    assert web.api_post(f"/api/pins/{pin['id']}/move-to-draft").json()["status"] == "draft"
    assert web.api_post(f"/api/pins/{pin['id']}/archive").json()["status"] == "archived"
    assert web.api_post(f"/api/pins/{pin['id']}/restore").json()["status"] == "draft"
    tok = web.c.get("/api/csrf").json()["csrf_token"]
    r = web.c.delete(f"/api/pins/{pin['id']}", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200 and r.json()["image_files_removed"] == 1
    assert web.get(f"/api/pins/{pin['id']}").status_code == 404
    assert web.c.delete(f"/api/pins/{pin['id']}").status_code == 403
    assert spy.requests == []
