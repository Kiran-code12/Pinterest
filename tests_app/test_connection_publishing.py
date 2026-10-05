import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai.local import LocalProvider
from app.models import Pin, PinterestConnection, PublishedPin, PublishingQueueItem
from app.pinterest import errors as E
from app.providers.registry import sync_provider_rows
from app.services import pins as PN
from app.services import products as P
from app.services import publishing as PB


@pytest.fixture
def cs(app):
    return app.state.pinterest


def connect(cs, db, session=None):
    session = session if session is not None else {}
    cs.begin(session)
    return cs.complete(db, session, "mock-code", session["pinterest_state"])


@pytest.fixture
def prods(app, db):
    reg = app.state.registry
    rows = sync_provider_rows(db, reg)
    return P.discover(db, reg, rows, "Find 6 skincare products", provider_keys=["demo"]).products


def approved_pin(app, db, product, **kw):
    pin = PN.create_pins(db, product, LocalProvider(), app.state.settings, count=1, **kw)[0]
    PN.approve(db, pin)
    return pin


# ---- connection ----------------------------------------------------------------------------------------
def test_state_mismatch_is_rejected(cs, db):
    s = {}
    cs.begin(s)
    with pytest.raises(E.PinterestError, match="did not match"):
        cs.complete(db, s, "mock-code", "forged-state")
    with pytest.raises(E.PinterestError):
        cs.complete(db, {}, "mock-code", "anything")  # no state stored in this session


def test_connect_stores_encrypted_tokens_account_and_boards(cs, db, mock_pin):
    conn = connect(cs, db)
    assert conn.username == "mockaccount" and conn.status == "connected" and cs.can_publish(conn)
    assert [b.name for b in conn.boards] == ["Beauty Finds", "Makeup", "Skincare"]
    assert conn.access_token_enc and "mock-access" not in conn.access_token_enc
    assert cs.crypto.decrypt(conn.access_token_enc).startswith("mock-access")
    assert "mock-access" not in repr(conn)
    assert conn.default_board_id is None  # several boards: the user must choose


def test_single_board_becomes_default(app, db, cs, mock_pin):
    mock_pin.boards = mock_pin.boards[:1]
    assert connect(cs, db).default_board_name == "Beauty Finds"


def test_default_board_selection_and_unknown_board(cs, db):
    connect(cs, db)
    assert cs.set_default_board(db, "b-makeup").name == "Makeup"
    with pytest.raises(E.PinterestError):
        cs.set_default_board(db, "not-mine")


def test_reconnect_keeps_default_board(cs, db):
    connect(cs, db)
    cs.set_default_board(db, "b-skin")
    assert connect(cs, db).default_board_id == "b-skin"
    assert len(db.scalars(select(PinterestConnection)).all()) == 1


def test_access_token_refreshes_near_expiry(cs, db, mock_pin):
    conn = connect(cs, db)
    old = cs.crypto.decrypt(conn.access_token_enc)
    conn.expires_at = datetime.utcnow() + timedelta(minutes=1)
    db.commit()
    new = cs.access_token(db)
    assert new != old and "refresh" in mock_pin.calls


def test_401_triggers_one_refresh_and_retry(cs, db, mock_pin):
    connect(cs, db)
    mock_pin.expire_access_tokens()
    assert [b.name for b in cs.refresh_boards(db)][0] == "Beauty Finds"
    assert mock_pin.calls.count("refresh") == 1


def test_revoked_authorization_marks_connection_expired(cs, db, mock_pin):
    connect(cs, db)
    mock_pin.revoke_everything()
    with pytest.raises(E.AuthExpired):
        cs.refresh_boards(db)
    conn = cs.get(db)
    assert conn.status == "expired" and not cs.can_publish(conn)
    with pytest.raises(E.AuthExpired):
        cs.access_token(db)


def test_not_connected(cs, db):
    with pytest.raises(E.NotConnected):
        cs.access_token(db)


def test_undecryptable_tokens_require_reconnect(cs, db):
    conn = connect(cs, db)
    conn.access_token_enc = "garbage"
    db.commit()
    with pytest.raises(E.AuthExpired):
        cs.access_token(db)
    assert cs.get(db).status == "expired"


def test_disconnect_revokes_and_deletes_tokens(cs, db, mock_pin):
    connect(cs, db)
    assert cs.disconnect(db) is True and "revoke" in mock_pin.calls
    assert cs.get(db) is None and not cs.disconnect(db)


def test_disconnect_when_revoke_unconfirmed_still_deletes(cs, db, mock_pin):
    connect(cs, db)
    mock_pin.revoke_ok = False
    assert cs.disconnect(db) is False and cs.get(db) is None


def test_tokens_never_logged(cs, db, caplog, app, prods):
    caplog.set_level(logging.DEBUG)
    connect(cs, db)
    pin = approved_pin(app, db, prods[0])
    cs.set_default_board(db, "b-beauty")
    PB.publish_pin(db, pin, app.state.settings, cs)
    assert "mock-access" not in caplog.text and "mock-refresh" not in caplog.text


# ---- publishing -----------------------------------------------------------------------------------------
def test_publish_success_stores_full_record(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    pub = PB.publish_pin(db, pin, app.state.settings, cs)
    assert pin.status == "published" and pin.queue_item.status == "published" and pin.queue_item.attempts == 1
    assert pub.pinterest_pin_id in mock_pin.pins and pub.simulated and pub.provider == "mock"
    assert pub.pinterest_url is None  # the API gave no URL: none is invented
    assert (pub.board_id, pub.board_name) == ("b-beauty", "Beauty Finds")
    assert pub.destination_url == prods[0].primary_link.affiliate_url == mock_pin.pins[pub.pinterest_pin_id]["link"]
    assert pub.title == pin.seo_title and pub.asset_id == pin.current_asset.id and pub.asset_sha256
    assert "commission" in pub.description  # configurable disclosure text was appended
    assert pub.published_at


def test_disclosure_is_configurable(app, db, cs, prods, mock_pin):
    from app.services import app_settings
    app_settings.set_value(db, "disclosure_text", "I earn a small commission from links.")
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    pub = PB.publish_pin(db, pin, app.state.settings, cs)
    assert pub.description.endswith("I earn a small commission from links.")
    pin2 = approved_pin(app, db, prods[1])
    pin2.show_disclosure = False
    db.commit()
    assert "commission" not in PB.publish_pin(db, pin2, app.state.settings, cs).description


def test_per_pin_board_overrides_default(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    PB.set_pin_board(db, pin, cs, "b-skin")
    assert PB.publish_pin(db, pin, app.state.settings, cs).board_id == "b-skin"
    with pytest.raises(PB.PublishError):
        PB.set_pin_board(db, approved_pin(app, db, prods[1]), cs, "unknown-board")


def test_unapproved_pin_is_blocked(app, db, cs, prods):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = PN.create_pins(db, prods[0], LocalProvider(), app.state.settings, count=1)[0]
    with pytest.raises(PB.PublishError, match="approved"):
        PB.publish_pin(db, pin, app.state.settings, cs)


def test_missing_destination_blocks_publishing(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    pin.destination_url = ""
    db.commit()
    with pytest.raises(PB.PublishError, match="Affiliate destination is missing"):
        PB.publish_pin(db, pin, app.state.settings, cs)
    assert not mock_pin.pins and pin.status == "approved"


def test_no_board_not_connected_and_expired_are_blocked(app, db, cs, prods, mock_pin):
    pin = approved_pin(app, db, prods[0])
    with pytest.raises(PB.PublishError, match="not connected"):
        PB.publish_pin(db, pin, app.state.settings, cs)
    connect(cs, db)  # 3 boards, no default
    with pytest.raises(PB.PublishError, match="No board selected"):
        PB.publish_pin(db, pin, app.state.settings, cs)
    cs.set_default_board(db, "b-beauty")
    mock_pin.revoke_everything()
    cs.get(db).status = "expired"
    db.commit()
    with pytest.raises(PB.PublishError, match="reconnect"):
        PB.publish_pin(db, pin, app.state.settings, cs)
    assert not mock_pin.pins


def test_missing_write_permission_is_blocked(app, db, cs, prods, mock_pin):
    mock_pin.scopes = ["boards:read", "user_accounts:read"]
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    with pytest.raises(PB.PublishError, match="lacks permission"):
        PB.publish_pin(db, pin, app.state.settings, cs)


def test_demo_pins_blocked_on_real_provider_allowed_on_mock(app, db, cs, prods, monkeypatch):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    monkeypatch.setattr(type(cs.provider), "simulated", False)  # pretend the provider is the real one
    with pytest.raises(PB.PublishError, match="Demo-catalog pins"):
        PB.publish_pin(db, pin, app.state.settings, cs)


@pytest.mark.parametrize("err,code,reconnect,retryable", [
    (E.RateLimited(retry_after=30), "rate_limited", False, True),
    (E.ServiceUnavailable(), "unavailable", False, True),
    (E.PermissionDenied(), "permission_denied", False, False),
    (E.InvalidBoard(), "invalid_board", False, False),
    (E.InvalidImage(), "invalid_image", False, False),
    (E.InvalidURL(), "invalid_url", False, False),
    (E.BadRequest(), "bad_request", False, False)])
def test_failure_modes_never_mark_published_and_can_retry(app, db, cs, prods, mock_pin, err, code, reconnect, retryable):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    mock_pin.fail_next(err)
    with pytest.raises(PB.PublishFailed) as ex:
        PB.publish_pin(db, pin, app.state.settings, cs)
    assert ex.value.error.retryable is retryable
    assert pin.status == "failed" and not db.scalars(select(PublishedPin)).all() and not mock_pin.pins
    item = pin.queue_item
    assert (item.status, item.error_code, item.attempts) == ("failed", code, 1) and item.last_error and item.last_attempt_at
    pub = PB.publish_pin(db, pin, app.state.settings, cs)  # Retry
    assert pin.status == "published" and item.attempts == 2 and pub.pinterest_pin_id in mock_pin.pins


def test_expired_token_during_publish_refreshes_transparently(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    mock_pin.expire_access_tokens()
    assert PB.publish_pin(db, pin, app.state.settings, cs).pinterest_pin_id
    assert mock_pin.calls.count("refresh") == 1


def test_revoked_during_publish_asks_to_reconnect(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    mock_pin.revoke_everything()
    with pytest.raises(PB.PublishFailed) as ex:
        PB.publish_pin(db, pin, app.state.settings, cs)
    assert ex.value.error.needs_reconnect and pin.status == "failed" and cs.get(db).status == "expired"


def test_ambiguous_failure_requires_confirmation_before_retry(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    mock_pin.fail_next(E.ServiceUnavailable("timeout", ambiguous=True))
    with pytest.raises(PB.PublishFailed):
        PB.publish_pin(db, pin, app.state.settings, cs)
    assert pin.queue_item.ambiguous
    with pytest.raises(PB.DuplicateWarning) as dup:
        PB.publish_pin(db, pin, app.state.settings, cs)
    assert "may already have created" in dup.value.findings[0].reason
    assert PB.publish_pin(db, pin, app.state.settings, cs, force=True).pinterest_pin_id


def test_duplicate_protection(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    first = approved_pin(app, db, prods[0])
    PB.publish_pin(db, first, app.state.settings, cs)
    # same product + same variation (concept) -> warning
    twin = approved_pin(app, db, prods[0])
    with pytest.raises(PB.DuplicateWarning) as dup:
        PB.publish_pin(db, twin, app.state.settings, cs)
    assert "product + variation" in str(dup.value) and len(mock_pin.pins) == 1
    assert twin.status == "approved"  # untouched: user decides
    PB.publish_pin(db, twin, app.state.settings, cs, force=True)  # Publish Anyway
    assert len(mock_pin.pins) == 2
    # a different product is fine
    assert PB.publish_pin(db, approved_pin(app, db, prods[1]), app.state.settings, cs)


def test_same_pin_cannot_be_published_twice(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    PB.publish_pin(db, pin, app.state.settings, cs)
    with pytest.raises(PB.PublishError, match="already published"):
        PB.publish_pin(db, pin, app.state.settings, cs, force=True)
    assert len(mock_pin.pins) == 1


def test_publishing_status_claim_blocks_concurrent_publish(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    pin.status = "publishing"  # simulates another request mid-flight
    db.commit()
    with pytest.raises(PB.PublishError):
        PB.publish_pin(db, pin, app.state.settings, cs, force=True)
    assert not mock_pin.pins


def test_scheduler_uses_same_service_and_never_overrides_duplicates(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    a, b = approved_pin(app, db, prods[0]), approved_pin(app, db, prods[0])
    past = datetime.utcnow() - timedelta(minutes=5)
    PB.enqueue(db, a, app.state.settings, scheduled_for=past, allow_demo=True)
    PB.enqueue(db, b, app.state.settings, scheduled_for=past, allow_demo=True)
    report = PB.process_due(db, app.state.settings, cs)
    assert report["published"] == 1 and report["failed"] == 1
    assert a.status == "published" and b.status == "failed" and b.queue_item.error_code == "duplicate"
    assert len(mock_pin.pins) == 1


def test_scheduler_ignores_future_and_respects_daily_cap(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    now = datetime.utcnow()
    future = approved_pin(app, db, prods[0])
    PB.enqueue(db, future, app.state.settings, scheduled_for=now + timedelta(days=2), allow_demo=True)
    assert PB.process_due(db, app.state.settings, cs)["published"] == 0
    pins = [approved_pin(app, db, p) for p in prods[1:6]]
    for p in pins:
        PB.enqueue(db, p, app.state.settings, scheduled_for=now - timedelta(minutes=1), allow_demo=True)
    rep = PB.process_due(db, app.state.settings, cs)
    assert rep["published"] == 3 and rep["skipped"] == 1  # MAX_PINS_PER_DAY = 3


def test_scheduler_stops_and_reports_when_reconnect_needed(app, db, cs, prods, mock_pin):
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    p = approved_pin(app, db, prods[0])
    PB.enqueue(db, p, app.state.settings, scheduled_for=datetime.utcnow() - timedelta(minutes=1), allow_demo=True)
    mock_pin.revoke_everything()
    rep = PB.process_due(db, app.state.settings, cs)
    assert rep["failed"] == 1 and any("reconnect" in m.lower() for m in rep["messages"])


def test_free_slots_are_three_per_day(app, db):
    slots = PB.next_slots(db, app.state.settings, 7)
    assert len(slots) == 7 and slots == sorted(slots) and len(set(slots)) == 7
    per_day = {}
    for s in slots:
        d = PB.to_local(s, app.state.settings).date()
        per_day[d] = per_day.get(d, 0) + 1
    assert max(per_day.values()) <= 3


def test_unschedule_and_enqueue_rules(app, db, cs, prods):
    pin = PN.create_pins(db, prods[0], LocalProvider(), app.state.settings, count=1)[0]
    with pytest.raises(PB.PublishError):
        PB.enqueue(db, pin, app.state.settings, allow_demo=True)  # not approved
    PN.approve(db, pin)
    with pytest.raises(PB.PublishError, match="Demo"):
        PB.enqueue(db, pin, app.state.settings)
    PB.enqueue(db, pin, app.state.settings, allow_demo=True)
    assert pin.status == "scheduled"
    PB.unschedule(db, pin)
    assert pin.status == "approved" and not db.scalars(select(PublishingQueueItem)).all()


def test_analytics_pinterest_reported_vs_calculated(app, db, cs, prods, mock_pin):
    from app.services import analytics as A
    connect(cs, db)
    cs.set_default_board(db, "b-beauty")
    pin = approved_pin(app, db, prods[0])
    pub = PB.publish_pin(db, pin, app.state.settings, cs)
    out = A.sync_from_pinterest(db, cs)
    assert out["synced"] == 1 and not out["errors"]
    rep = A.pinterest_reported(db)
    assert (rep["impressions"], rep["saves"], rep["outbound_clicks"]) == (120, 7, 4)
    calc = A.calculated(db)
    assert calc["outbound_click_rate"] == round(100 * 4 / 120, 2) and calc["publish_success_rate"] == 100.0
    A.record_metrics(db, pub, impressions=1, source="manual")
    assert [r.source for pp, r in A.latest_by_pin(db)] == ["pinterest"]  # reported numbers win over manual ones


def test_analytics_sync_when_disconnected(cs, db):
    from app.services import analytics as A
    out = A.sync_from_pinterest(db, cs)
    assert out["synced"] == 0 and "not connected" in out["errors"][0].lower()


def test_export_fallback_contains_affiliate_url(app, db, prods):
    import io
    import zipfile
    pin = approved_pin(app, db, prods[0])
    z = zipfile.ZipFile(io.BytesIO(PB.export_pin(db, pin)))
    assert prods[0].primary_link.affiliate_url in z.read(f"pin_{pin.id}.txt").decode()
    assert f"pin_{pin.id}.png" in z.namelist()
    _ = Pin
