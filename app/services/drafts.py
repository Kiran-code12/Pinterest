"""Draft Library: the MVP workflow.  Product -> pin generation -> DRAFT -> edit/regenerate -> copy + download ->
you post it on Pinterest yourself -> mark as published manually.  Nothing here calls Pinterest."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

from PIL import Image
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import Pin, PublishedPin
from ..publishers.export import pin_payload
from ..security import UnsafeURL, validate_http_url
from . import app_settings
from .pins import PinError, check_destination

PAGE_SIZE = 24
ACTIVE_VIEWS = {
    "drafts": ("draft", "rejected", "approved", "scheduled", "failed", "publishing"),
    "published": ("published_manually", "published"),
    "archived": ("archived",),
}
MANUAL_ALLOWED = ("draft", "rejected", "approved", "scheduled", "failed")  # statuses that can be marked as posted


# ---- listing ---------------------------------------------------------------------------------------------
def library_query(view: str = "drafts", q: str = "", product_id: int = 0, template: str = ""):
    stmt = select(Pin).options(selectinload(Pin.assets), selectinload(Pin.product))
    if view in ACTIVE_VIEWS:
        stmt = stmt.where(Pin.status.in_(ACTIVE_VIEWS[view]))
    if q:
        like = f"%{q.strip()[:80]}%"
        from ..models import Product
        stmt = stmt.join(Product, Product.id == Pin.product_id).where(
            or_(Pin.headline.ilike(like), Pin.seo_title.ilike(like), Product.title.ilike(like)))
    if product_id:
        stmt = stmt.where(Pin.product_id == product_id)
    if template:
        stmt = stmt.where(Pin.template_key == template)
    return stmt


def list_library(db: Session, view: str = "drafts", q: str = "", product_id: int = 0, template: str = "",
                 page: int = 1) -> tuple[list[Pin], int]:
    stmt = library_query(view, q, product_id, template)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = db.scalars(stmt.order_by(Pin.id.desc()).limit(PAGE_SIZE).offset((max(1, page) - 1) * PAGE_SIZE)).all()
    return list(items), total


def library_counts(db: Session) -> dict[str, int]:
    out = {}
    for view, statuses in ACTIVE_VIEWS.items():
        out[view] = db.scalar(select(func.count()).select_from(Pin).where(Pin.status.in_(statuses))) or 0
    out["all"] = db.scalar(select(func.count()).select_from(Pin)) or 0
    return out


# ---- texts and files you copy / download -----------------------------------------------------------------
def final_texts(db: Session, pin: Pin) -> dict[str, str]:
    """Exactly what to paste into Pinterest (the description already includes the configured disclosure)."""
    p = pin_payload(pin, app_settings.get(db, "disclosure_text"))
    return {"title": p["title"], "description": p["description"], "url": p["link"], "alt": p["alt_text"],
            "all": f"{p['title']}\n\n{p['description']}\n\n{p['link']}"}


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit] or "pin"


def asset_path(pin: Pin, settings: Settings) -> Path:
    asset = pin.current_asset
    if asset is None:
        raise PinError("This pin has no image yet. Re-render the design.")
    path, root = Path(asset.path).resolve(), settings.assets_dir.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise PinError("The pin image file is missing. Re-render the design.")
    return path


def image_download(pin: Pin, settings: Settings, fmt: str = "png") -> tuple[bytes, str, str]:
    """Pinterest-ready image (1000x1500, 2:3). Returns (bytes, filename, media_type)."""
    path = asset_path(pin, settings)
    name = f"pin-{pin.id}-{_slug(pin.product.title)}"
    if fmt in ("jpg", "jpeg"):
        buf = io.BytesIO()
        Image.open(path).convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
        return buf.getvalue(), f"{name}.jpg", "image/jpeg"
    return path.read_bytes(), f"{name}.png", "image/png"


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in exported text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_zip(db: Session, pins: list[Pin], settings: Settings) -> bytes:
    """Images + a CSV (title, description, affiliate URL...) for one or many drafts."""
    if not pins:
        raise PinError("Select at least one draft.")
    buf = io.BytesIO()
    rows = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for pin in pins:
            data, filename, _ = image_download(pin, settings, "png")
            z.writestr(filename, data)
            t = final_texts(db, pin)
            rows.append([pin.id, pin.product.title, t["title"], t["description"], t["url"], t["alt"], filename,
                         pin.status])
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["draft_id", "product", "title", "description", "affiliate_url", "alt_text", "image_file", "status"])
        for r in rows:
            w.writerow([r[0]] + [_csv_safe(str(c)) for c in r[1:]])
        z.writestr("drafts.csv", out.getvalue())
        z.writestr("README.txt", "Each image is 1000x1500 (2:3), ready to upload to Pinterest.\n"
                                 "drafts.csv holds the title, description and affiliate URL for each image.\n")
    return buf.getvalue()


# ---- lifecycle -------------------------------------------------------------------------------------------
def mark_published_manually(db: Session, pin: Pin, pinterest_url: str | None = None,
                            board_name: str | None = None) -> PublishedPin:
    """Record that YOU posted this draft on Pinterest (no API involved)."""
    if pin.status not in MANUAL_ALLOWED:
        raise PinError({"published_manually": "This pin is already marked as published.",
                        "archived": "Restore the pin before marking it as published.",
                        "published": "This pin was already published."}.get(pin.status, "This pin cannot be marked as published."))
    problems = check_destination(pin)
    if problems:
        raise PinError("Affiliate destination check failed: " + "; ".join(problems))
    if pinterest_url:
        try:
            pinterest_url = validate_http_url(pinterest_url)
        except UnsafeURL as ex:
            raise PinError(f"Pinterest URL invalid: {ex}") from ex
    asset = pin.current_asset
    t = final_texts(db, pin)
    pub = PublishedPin(pin_id=pin.id, mode="manual", provider="manual", simulated=False, pinterest_url=pinterest_url or None,
                       board_name=(re.sub(r"[\x00-\x1f]", "", board_name or "").strip()[:200] or None),
                       title=t["title"], description=t["description"], destination_url=pin.destination_url,
                       asset_id=asset.id if asset else None, asset_sha256=asset.sha256 if asset else None)
    db.add(pub)
    if pin.queue_item is not None:
        db.delete(pin.queue_item)
        pin.queue_item = None
    pin.status = "published_manually"
    db.commit()
    return pub


def move_back_to_draft(db: Session, pin: Pin) -> None:
    """Undo 'published manually' (e.g. clicked by mistake). The manual record and its metrics are removed."""
    if pin.status != "published_manually":
        raise PinError("Only pins marked as published manually can be moved back to drafts.")
    for pub in db.scalars(select(PublishedPin).where(PublishedPin.pin_id == pin.id, PublishedPin.mode == "manual")):
        db.delete(pub)
    pin.status = "draft"
    db.commit()


def archive(db: Session, pin: Pin) -> None:
    if pin.status in ("archived", "publishing"):
        raise PinError("This pin is already archived." if pin.status == "archived" else "This pin is being published.")
    pin.status_before_archive = pin.status
    pin.status = "archived"
    if pin.queue_item is not None:
        db.delete(pin.queue_item)
        pin.queue_item = None
    db.commit()


def restore(db: Session, pin: Pin) -> None:
    if pin.status != "archived":
        raise PinError("Only archived pins can be restored.")
    before = pin.status_before_archive
    pin.status = before if before in ("published_manually", "published") else "draft"
    pin.status_before_archive = None
    db.commit()


def delete(db: Session, pin: Pin, settings: Settings) -> int:
    """Permanently delete a draft with its images and history. Returns number of image files removed."""
    if pin.status in ("publishing", "published"):
        raise PinError("Pins published through the Pinterest API cannot be deleted. Archive them instead.")
    root = settings.assets_dir.resolve()
    paths = [Path(a.path) for a in pin.assets]
    for pub in db.scalars(select(PublishedPin).where(PublishedPin.pin_id == pin.id)):
        db.delete(pub)  # manual records (and their metrics) go with the draft
    db.flush()
    db.delete(pin)
    db.commit()
    removed = 0
    for p in paths:  # files only after the DB commit, and only inside our assets folder
        try:
            rp = p.resolve()
            if rp.is_relative_to(root) and rp.is_file():
                rp.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def similar_published(db: Session, pin: Pin) -> list[dict]:
    """Other pins of the same product that are already published (manually or via API): a gentle duplicate hint."""
    out = []
    rows = db.execute(select(PublishedPin, Pin).join(Pin, Pin.id == PublishedPin.pin_id)
                      .where(Pin.product_id == pin.product_id, Pin.id != pin.id)).all()
    asset = pin.current_asset
    for pub, other in rows:
        same_variation = other.concept_key == pin.concept_key
        same_image = bool(asset and pub.asset_sha256 == asset.sha256)
        out.append({"pin_id": other.id, "published_at": pub.published_at, "same_variation": same_variation,
                    "same_image": same_image, "url": pub.pinterest_url, "mode": pub.mode})
    return out
