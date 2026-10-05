"""Publishing: ONE service (`publish_pin`) used by the Publish button, Retry and the scheduler.

Safety rules enforced here, in this order:
  1. only APPROVED (or scheduled / failed-and-retried) pins; never an unapproved one
  2. the affiliate destination chain Product -> provider -> link -> pin URL must be intact, else BLOCK
  3. Pinterest must be connected with the needed permissions and a board must be chosen
  4. duplicate protection (needs explicit confirmation to override)
  5. atomic claim (status -> publishing) so a double click can never publish twice
  6. on any failure the pin is marked FAILED (never published) with the reason, time and attempt count
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Pin, PublishedPin, PublishingQueueItem, utcnow
from ..pinterest.connection import ConnectionService
from ..pinterest.errors import PinterestError, ServiceUnavailable
from ..pinterest.types import PinPayload
from ..publishers.export import build_export_zip, pin_payload
from . import app_settings
from .pins import check_destination

log = logging.getLogger("engine.publishing")
MAX_ATTEMPTS = 3
MAX_IMAGE_BYTES = 10 * 1024 * 1024
PUBLISHABLE = ("approved", "scheduled", "failed")


class PublishError(Exception):
    """User-facing reason a pin cannot be published right now."""


@dataclass
class DuplicateFinding:
    reason: str
    other_pin_id: int | None = None


class DuplicateWarning(PublishError):
    def __init__(self, findings: list[DuplicateFinding]):
        super().__init__("; ".join(f.reason for f in findings))
        self.findings = findings


class PublishFailed(PublishError):
    def __init__(self, error: PinterestError):
        super().__init__(str(error))
        self.error = error


# ---- time helpers / slots ---------------------------------------------------------------------------------
def _tz(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.schedule_tz)
    except Exception:
        return ZoneInfo("UTC")


def to_local(dt: datetime | None, settings: Settings) -> datetime | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc).astimezone(_tz(settings))


def next_slots(db: Session, settings: Settings, count: int, now: datetime | None = None) -> list[datetime]:
    """Next free daily slots (naive UTC), at most `max_pins_per_day` per local day."""
    tz, now = _tz(settings), now or utcnow()
    taken = [t for t in db.scalars(select(PublishingQueueItem.scheduled_for)
                                   .where(PublishingQueueItem.scheduled_for.is_not(None))) if t]
    taken_set = {t.replace(second=0, microsecond=0) for t in taken}
    per_day: dict = {}
    for t in taken:
        d = to_local(t, settings).date()
        per_day[d] = per_day.get(d, 0) + 1
    hm = []
    for item in settings.schedule_slots:
        try:
            h, m = item.split(":")
            hm.append((int(h), int(m)))
        except ValueError:
            continue
    hm = sorted(hm)[: settings.max_pins_per_day] or [(9, 0)]
    out: list[datetime] = []
    day = to_local(now, settings).date()
    for _ in range(366):
        for h, m in hm:
            utc = datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
            if utc <= now or utc in taken_set or per_day.get(day, 0) >= settings.max_pins_per_day:
                continue
            out.append(utc)
            per_day[day] = per_day.get(day, 0) + 1
            if len(out) >= count:
                return out
        day += timedelta(days=1)
    return out


# ---- board ----------------------------------------------------------------------------------------------
def resolve_board(db: Session, pin: Pin, conn_svc: ConnectionService, board_id: str | None = None):
    conn = conn_svc.get(db)
    chosen = board_id or pin.board_id or (conn.default_board_id if conn else None)
    if not chosen:
        return None, None
    name = conn_svc.board_name(db, chosen)
    return (chosen, name) if name is not None else (chosen, None)


def set_pin_board(db: Session, pin: Pin, conn_svc: ConnectionService, board_id: str | None) -> None:
    """board_id empty = use the default board."""
    if pin.status in ("published", "publishing"):
        raise PublishError("The board of a published pin cannot be changed")
    if not board_id:
        pin.board_id = pin.board_name = None
    else:
        name = conn_svc.board_name(db, board_id)
        if name is None:
            raise PublishError("That board is not in your connected Pinterest account. Refresh boards first.")
        pin.board_id, pin.board_name = board_id, name
    db.commit()


# ---- preflight / duplicates ------------------------------------------------------------------------------
def preflight(db: Session, pin: Pin, conn_svc: ConnectionService, board_id: str | None = None) -> list[str]:
    """Reasons publishing is BLOCKED (cannot be overridden)."""
    blockers: list[str] = []
    if pin.status not in PUBLISHABLE:
        blockers.append(f"Pin is '{pin.status.replace('_', ' ')}': only approved pins can be published."
                        if pin.status != "published" else "This pin is already published.")
        return blockers
    if not pin.destination_url or pin.affiliate_link is None or not pin.affiliate_link.affiliate_url:
        blockers.append("Affiliate destination is missing.")
    else:
        blockers += check_destination(pin)
    if pin.product.provider and pin.product.provider.is_demo and not conn_svc.provider.simulated:
        blockers.append("Demo-catalog pins can never be published to a real Pinterest account.")
    conn = conn_svc.get(db)
    if conn is None:
        blockers.append("Pinterest is not connected. Connect your account in Settings.")
    elif conn.status != "connected":
        blockers.append("Pinterest authorization expired or was revoked. Please reconnect Pinterest.")
    elif not conn_svc.can_publish(conn):
        blockers.append("The connected Pinterest authorization lacks permission to create pins "
                        "(needs pins:write and boards:read). Reconnect and allow all permissions.")
    if conn is not None and not resolve_board(db, pin, conn_svc, board_id)[0]:
        blockers.append("No board selected. Choose a board for this pin or set a default board.")
    asset = pin.current_asset
    if asset is None or not Path(asset.path).is_file():
        blockers.append("The pin image is missing. Re-render the image.")
    if not pin.seo_title or len(pin.seo_title) > 100:
        blockers.append("Pin title must be 1-100 characters.")
    return blockers


def find_duplicates(db: Session, pin: Pin, conn_svc: ConnectionService, board_id: str | None = None) -> list[DuplicateFinding]:
    findings: list[DuplicateFinding] = []
    simulated = conn_svc.provider.simulated
    board, _ = resolve_board(db, pin, conn_svc, board_id)
    if pin.queue_item is not None and pin.queue_item.ambiguous:
        findings.append(DuplicateFinding("A previous attempt may already have created this pin on Pinterest (the "
                                         "answer was lost). Check your board before publishing again.", pin.id))
    rows = db.execute(select(PublishedPin, Pin).join(Pin, Pin.id == PublishedPin.pin_id).where(
        PublishedPin.pin_id != pin.id, PublishedPin.simulated == simulated, Pin.product_id == pin.product_id)).all()
    for pp, other in rows:
        where = f" on board '{pp.board_name or pp.board_id}'" if pp.board_id else ""
        if other.concept_key == pin.concept_key:
            findings.append(DuplicateFinding(f"This product + variation ('{pin.concept_key.replace('_', ' ')}') "
                                             f"has already been published (pin #{other.id}{where}).", other.id))
        elif board and pp.board_id == board:
            findings.append(DuplicateFinding(f"This product already has a pin on this board (pin #{other.id}"
                                             f"{where}).", other.id))
    asset = pin.current_asset
    if asset is not None:
        same = db.scalars(select(PublishedPin).where(PublishedPin.asset_sha256 == asset.sha256,
                                                     PublishedPin.pin_id != pin.id,
                                                     PublishedPin.simulated == simulated)).first()
        if same is not None and not any(f.other_pin_id == same.pin_id for f in findings):
            findings.append(DuplicateFinding(f"An identical image was already published (pin #{same.pin_id}).",
                                             same.pin_id))
    return findings


# ---- the publishing service -----------------------------------------------------------------------------
def build_payload(db: Session, pin: Pin, board_id: str) -> PinPayload:
    asset = pin.current_asset
    data = Path(asset.path).read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise PublishError("The pin image is larger than 10MB.")
    payload = pin_payload(pin, app_settings.get(db, "disclosure_text"))
    return PinPayload(board_id=board_id, title=payload["title"], description=payload["description"],
                      link=payload["link"], alt_text=payload["alt_text"], image_png=data)


def publish_pin(db: Session, pin: Pin, settings: Settings, conn_svc: ConnectionService, *, force: bool = False,
                board_id: str | None = None) -> PublishedPin:
    """Publish ONE approved pin to the connected Pinterest account via the official API."""
    blockers = preflight(db, pin, conn_svc, board_id)
    if blockers:
        raise PublishError(" ".join(blockers))
    board, board_name = resolve_board(db, pin, conn_svc, board_id)
    if not force:
        findings = find_duplicates(db, pin, conn_svc, board_id)
        if findings:
            raise DuplicateWarning(findings)
    # atomic claim: only one request can move the pin into 'publishing'
    claimed = db.execute(update(Pin).where(Pin.id == pin.id, Pin.status.in_(PUBLISHABLE)).values(status="publishing"))
    db.commit()
    if claimed.rowcount == 0:
        raise PublishError("This pin is already being published or is no longer publishable.")
    db.refresh(pin)
    item = pin.queue_item
    if item is None:
        item = PublishingQueueItem(pin_id=pin.id, destination="pinterest")
        pin.queue_item = item
        db.add(item)
    item.status, item.attempts, item.last_attempt_at = "publishing", (item.attempts or 0) + 1, utcnow()
    item.board_id, item.last_error, item.error_code, item.ambiguous = board, None, None, False
    db.commit()
    try:
        payload = build_payload(db, pin, board)
        created = conn_svc.call(db, lambda token: conn_svc.provider.create_pin(token, payload))
    except PinterestError as ex:
        _record_failure(db, pin, item, ex.code, str(ex), ambiguous=ex.ambiguous)
        log.warning("publish failed pin=%s code=%s", pin.id, ex.code)
        raise PublishFailed(ex) from ex
    except (PublishError, OSError) as ex:
        err = PinterestError(str(ex) if isinstance(ex, PublishError) else "Could not read the pin image file.")
        _record_failure(db, pin, item, "local_error", str(err), ambiguous=False)
        raise PublishFailed(err) from ex
    except Exception as ex:  # a bug must never leave the pin stuck as 'publishing'
        log.exception("unexpected publish error pin=%s", pin.id)
        err = ServiceUnavailable("Unexpected error while publishing; nothing was marked as published.", ambiguous=True)
        _record_failure(db, pin, item, "unexpected", str(err), ambiguous=True)
        raise PublishFailed(err) from ex
    asset = pin.current_asset
    published = PublishedPin(
        pin_id=pin.id, pinterest_pin_id=created.pin_id, pinterest_url=created.url, mode="api",
        provider=conn_svc.provider.name, simulated=conn_svc.provider.simulated, board_id=created.board_id or board,
        board_name=board_name, title=payload.title, description=payload.description, destination_url=payload.link,
        asset_id=asset.id if asset else None, asset_sha256=asset.sha256 if asset else None)
    db.add(published)
    pin.status = "published"
    item.status = "published"
    db.commit()
    log.info("pin %s published (pinterest id %s)", pin.id, created.pin_id)
    return published


def _record_failure(db: Session, pin: Pin, item: PublishingQueueItem, code: str, message: str, *, ambiguous: bool) -> None:
    pin.status = "failed"
    item.status, item.error_code, item.last_error = "failed", code, message[:500]
    item.ambiguous = ambiguous
    db.commit()


# ---- queue / scheduling -----------------------------------------------------------------------------------
def enqueue(db: Session, pin: Pin, settings: Settings, *, scheduled_for: datetime | None = None,
            auto_slot: bool = True, allow_demo: bool = False) -> PublishingQueueItem:
    if pin.status != "approved":
        raise PublishError("Only approved pins can be scheduled. Approve the pin first.")
    problems = check_destination(pin)
    if problems:
        raise PublishError("Affiliate destination check failed: " + "; ".join(problems))
    if pin.product.provider and pin.product.provider.is_demo and not allow_demo:
        raise PublishError("Demo-catalog pins can never be published to Pinterest")
    if scheduled_for is None and auto_slot:
        slots = next_slots(db, settings, 1)
        scheduled_for = slots[0] if slots else None
    item = pin.queue_item or PublishingQueueItem(pin_id=pin.id)
    item.destination, item.scheduled_for = "pinterest", scheduled_for
    item.status, item.attempts, item.last_error, item.error_code, item.ambiguous = "scheduled", 0, None, None, False
    pin.queue_item = item
    pin.status = "scheduled"
    db.add(item)
    db.commit()
    return item


def unschedule(db: Session, pin: Pin) -> None:
    if pin.status != "scheduled":
        raise PublishError("Pin is not scheduled")
    if pin.queue_item is not None:
        db.delete(pin.queue_item)
        pin.queue_item = None
    pin.status = "approved"
    db.commit()


def published_today(db: Session, settings: Settings, now: datetime | None = None) -> int:
    now = now or utcnow()
    start_local = to_local(now, settings).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return db.scalar(select(func.count(PublishedPin.id)).where(
        PublishedPin.mode == "api", PublishedPin.published_at >= start)) or 0


def process_due(db: Session, settings: Settings, conn_svc: ConnectionService, now: datetime | None = None) -> dict:
    """Scheduler step: publishes DUE scheduled pins through the same publish_pin() as the manual button.
    (The background worker only calls this when SCHEDULER_ENABLED=true; the dashboard button always may.)"""
    now = now or utcnow()
    report = {"published": 0, "failed": 0, "skipped": 0, "messages": []}
    due = db.scalars(select(PublishingQueueItem).where(
        PublishingQueueItem.status == "scheduled", PublishingQueueItem.destination == "pinterest",
        PublishingQueueItem.scheduled_for <= now).order_by(PublishingQueueItem.scheduled_for)).all()
    for item in due:
        pin = item.pin
        if published_today(db, settings, now) >= settings.max_pins_per_day:
            report["skipped"] += 1
            report["messages"].append("Daily pin limit reached; remaining pins wait until tomorrow")
            break
        try:
            publish_pin(db, pin, settings, conn_svc)
            report["published"] += 1
        except DuplicateWarning as ex:  # the scheduler never overrides duplicate protection
            _record_failure(db, pin, item, "duplicate", f"Possible duplicate, needs your confirmation: {ex}",
                            ambiguous=False)
            report["failed"] += 1
        except PublishFailed as ex:
            report["failed"] += 1
            if ex.error.needs_reconnect:
                report["messages"].append(str(ex))
                break
            if ex.error.retryable and item.attempts < MAX_ATTEMPTS and not ex.error.ambiguous:
                pin.status, item.status = "scheduled", "scheduled"  # automatic retry later
                item.scheduled_for = now + timedelta(minutes=30 * item.attempts)
                db.commit()
        except PublishError as ex:
            pin_status = pin.status
            if pin_status in ("scheduled", "approved"):
                _record_failure(db, pin, item, "blocked", str(ex), ambiguous=False)
            report["failed"] += 1
    return report


# ---- export fallback / manual ----------------------------------------------------------------------------
def export_pin(db: Session, pin: Pin) -> bytes:
    if pin.status not in ("approved", "scheduled", "published", "failed"):
        raise PublishError("Approve the pin before exporting it")
    problems = check_destination(pin)
    if problems:
        raise PublishError("Affiliate destination check failed: " + "; ".join(problems))
    return build_export_zip(pin, app_settings.get(db, "disclosure_text"))


def mark_published(db: Session, pin: Pin, pinterest_url: str | None = None) -> PublishedPin:
    """Record a pin you posted by hand (fallback when Pinterest is not connected)."""
    if pin.status not in ("approved", "scheduled", "failed"):
        raise PublishError("Only approved pins can be marked as published")
    if pinterest_url:
        from ..security import UnsafeURL, validate_http_url
        try:
            pinterest_url = validate_http_url(pinterest_url)
        except UnsafeURL as ex:
            raise PublishError(f"Pinterest URL invalid: {ex}") from ex
    asset = pin.current_asset
    published = PublishedPin(pin_id=pin.id, pinterest_url=pinterest_url, mode="manual", provider="manual",
                             title=pin.seo_title, description=pin.seo_description, destination_url=pin.destination_url,
                             asset_id=asset.id if asset else None, asset_sha256=asset.sha256 if asset else None,
                             board_id=pin.board_id, board_name=pin.board_name)
    db.add(published)
    pin.status = "published"
    if pin.queue_item is not None:
        pin.queue_item.status = "published"
    db.commit()
    return published
