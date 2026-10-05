"""Pin lifecycle: create (copy + design), edit, regenerate, approve. Affiliate destination is always guarded."""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from ..ai.base import AIProvider
from ..ai.local import first_sentence, short_name
from ..config import Settings
from ..models import AffiliateLink, Pin, PinAsset, PinVariation, Product, utcnow
from ..security import UnsafeURL, validate_http_url
from . import app_settings
from .content import CONCEPT_ORDER, PinCopy, generate_copy, price_for_display, product_facts
from .grounding import MAX_DESC, MAX_HEADLINE, MAX_SUPPORT, MAX_TITLE, validate_copy
from .imagery import ensure_product_image
from .templates import TEMPLATES, TemplateContext, render_template

EDITABLE_TEXT = {"headline": MAX_HEADLINE, "supporting_text": MAX_SUPPORT, "seo_title": MAX_TITLE,
                 "seo_description": MAX_DESC, "cta": 40}
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class PinError(Exception):
    """User-facing workflow error."""


def link_for(product: Product) -> AffiliateLink | None:
    return product.primary_link


def check_destination(pin: Pin) -> list[str]:
    """Problems with the pin's affiliate destination. Empty list = safe to approve/publish."""
    problems: list[str] = []
    link = pin.affiliate_link
    if link is None or not link.affiliate_url:
        return ["Affiliate destination is missing"]
    if not link.is_valid:
        problems.append(f"Affiliate link is flagged invalid: {link.validation_message or 'unknown reason'}")
    if pin.destination_url != link.affiliate_url:
        problems.append("Pin destination no longer matches its affiliate link")
    if link.original_url and pin.destination_url == link.original_url and not (link.link_kind == "demo"):
        problems.append("Pin points to the plain product URL instead of the affiliate URL")
    try:
        validate_http_url(pin.destination_url)
    except UnsafeURL as ex:
        problems.append(f"Destination URL invalid: {ex}")
    if pin.product_id != link.product_id:
        problems.append("Affiliate link belongs to a different product")
    return problems


def _sanitize(value, limit: int) -> str:
    return CTRL.sub("", str(value or "")).strip()[:limit]


def _keywords(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    out = []
    for k in items:
        k = _sanitize(k, 40).lstrip("#").strip().lower()
        if k and k not in out:
            out.append(k)
    return out[:12]


def detail_lines(product: Product) -> list[str]:
    """Up to three short detail lines taken verbatim from the provider's description (no invention)."""
    text = re.sub(r"\s+", " ", product.description or "")
    parts = [p.strip(" .;") for p in re.split(r"(?<=[.!?;])\s", text) if p.strip(" .;")]
    return [p[:70] for p in parts[:3]]


def build_context(db: Session, pin: Pin, settings: Settings, http: httpx.Client | None = None) -> TemplateContext:
    product = pin.product
    images: list[Image.Image | None] = []
    names: list[str] = []
    products = [product] + [db.get(Product, pid) for pid in (pin.collage_product_ids or []) if db.get(Product, pid)]
    for p in products[:5]:
        path = ensure_product_image(p, settings, http)
        images.append(Image.open(path) if path else None)
        names.append(short_name(p.title, p.brand))
    if len(products) == 1 and pin.template_key == "top_picks":
        names = [names[0]] + detail_lines(product)  # single product: name + verified detail lines
        images = images[:1]
    price = price_for_display(product) if pin.show_price else None
    return TemplateContext(
        headline=pin.headline, supporting_text=pin.supporting_text, brand=product.brand or "",
        product_name=short_name(product.title, product.brand), cta=pin.cta, price_text=price,
        disclosure=app_settings.get(db, "image_disclosure_label") if pin.show_disclosure else None, images=images, list_items=names,
        seed=f"{product.id}:{pin.template_key}")


def render_asset(db: Session, pin: Pin, settings: Settings, http: httpx.Client | None = None) -> PinAsset:
    ctx = build_context(db, pin, settings, http)
    img = render_template(pin.template_key, ctx).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    folder = settings.assets_dir / "pins"
    folder.mkdir(parents=True, exist_ok=True)
    n = len(pin.assets) + 1
    path = folder / f"pin_{pin.id}_{n}.png"
    path.write_bytes(data)
    asset = PinAsset(pin_id=pin.id, path=str(path), width=img.width, height=img.height,
                     sha256=hashlib.sha256(data).hexdigest(), template_key=pin.template_key)
    pin.assets.append(asset)
    db.flush()
    return asset


def _add_variation(pin: Pin, source: str, content: dict) -> None:
    pin.variations.append(PinVariation(pin_id=pin.id, version=len(pin.variations) + 1, source=source, content=content))


def _apply_copy(pin: Pin, copy: PinCopy, concept_key: str) -> None:
    pin.concept_key = concept_key
    pin.headline, pin.supporting_text = copy.headline, copy.supporting_text
    pin.seo_title, pin.cta, pin.keywords = copy.seo_title, copy.cta, copy.keywords
    pin.seo_description = copy.seo_description
    pin.ai_source, pin.warnings = copy.source, copy.warnings


def create_pins(db: Session, product: Product, ai: AIProvider, settings: Settings, *, count: int = 3,
                template_keys: list[str] | None = None, extra_product_ids: list[int] | None = None,
                show_price: bool = False, show_disclosure: bool = True,
                http: httpx.Client | None = None) -> list[Pin]:
    link = link_for(product)
    if link is None:
        raise PinError("This product has no affiliate link. Add one on the product page first.")
    if not link.is_valid:
        raise PinError(f"The affiliate link is invalid: {link.validation_message or 'unknown reason'}")
    count = max(1, min(int(count), 10))
    keys = [k for k in (template_keys or []) if k in TEMPLATES] or list(TEMPLATES)
    extras = [i for i in (extra_product_ids or []) if i != product.id][:4]
    pins: list[Pin] = []
    for i in range(count):
        concept = CONCEPT_ORDER[i % len(CONCEPT_ORDER)]
        copy = generate_copy(product, ai, concept, variation=i)
        template = keys[i % len(keys)]
        pin = Pin(product_id=product.id, affiliate_link_id=link.id, destination_url=link.affiliate_url,
                  concept_key=concept, template_key=template, headline=copy.headline,
                  supporting_text=copy.supporting_text, seo_title=copy.seo_title,
                  seo_description=copy.seo_description, keywords=copy.keywords, cta=copy.cta,
                  show_price=show_price, show_disclosure=show_disclosure,
                  collage_product_ids=extras if template in ("collage", "top_picks") else [],
                  ai_source=copy.source, warnings=copy.warnings, status="draft")
        pin.product, pin.affiliate_link = product, link
        db.add(pin)
        db.flush()
        _add_variation(pin, copy.source, copy.as_dict())
        render_asset(db, pin, settings, http)
        pin.status = "ready_for_review"  # generation finished: the pin is now waiting for the user's review
        pins.append(pin)
    db.commit()
    return pins


def update_pin(db: Session, pin: Pin, data: dict, settings: Settings, http: httpx.Client | None = None) -> list[str]:
    """Manual edit. Returns non-blocking grounding warnings. Resets approval (content changed)."""
    if pin.status in ("published", "publishing"):
        raise PinError("This pin is published or being published and cannot be edited")
    for key, limit in EDITABLE_TEXT.items():
        if key in data and data[key] is not None:
            setattr(pin, key, _sanitize(data[key], limit))
    if "keywords" in data and data["keywords"] is not None:
        pin.keywords = _keywords(data["keywords"])
    for flag in ("show_price", "show_disclosure"):
        if flag in data and data[flag] is not None:
            setattr(pin, flag, bool(data[flag]))
    if data.get("template_key"):
        if data["template_key"] not in TEMPLATES:
            raise PinError("Unknown template")
        pin.template_key = data["template_key"]
    if not pin.headline or not pin.seo_title or not pin.seo_description:
        raise PinError("Headline, SEO title and SEO description are required")
    warnings = validate_copy({"headline": pin.headline, "supporting_text": pin.supporting_text,
                              "seo_title": pin.seo_title, "seo_description": pin.seo_description,
                              "keywords": pin.keywords or [], "cta": pin.cta}, product_facts(pin.product))
    pin.warnings = warnings
    _add_variation(pin, "manual", {"headline": pin.headline, "supporting_text": pin.supporting_text,
                                   "seo_title": pin.seo_title, "seo_description": pin.seo_description,
                                   "keywords": pin.keywords, "cta": pin.cta})
    _reset_approval(db, pin)
    render_asset(db, pin, settings, http)
    db.commit()
    return warnings


def _reset_approval(db: Session, pin: Pin) -> None:
    if pin.status in ("approved", "scheduled", "failed", "rejected", "draft"):
        pin.status, pin.approved_at = "ready_for_review", None
        if pin.queue_item is not None:
            db.delete(pin.queue_item)
            pin.queue_item = None


def regenerate_copy(db: Session, pin: Pin, ai: AIProvider, settings: Settings,
                    http: httpx.Client | None = None) -> PinCopy:
    if pin.status in ("published", "publishing"):
        raise PinError("Published pins cannot be regenerated")
    n = len(pin.variations)
    concept = CONCEPT_ORDER[n % len(CONCEPT_ORDER)]
    copy = generate_copy(pin.product, ai, concept, variation=n)
    _apply_copy(pin, copy, concept)
    _add_variation(pin, copy.source, copy.as_dict())
    _reset_approval(db, pin)
    render_asset(db, pin, settings, http)
    db.commit()
    return copy


def regenerate_image(db: Session, pin: Pin, settings: Settings, template_key: str | None = None,
                     http: httpx.Client | None = None) -> PinAsset:
    if pin.status in ("published", "publishing"):
        raise PinError("Published pins cannot be changed")
    if template_key:
        if template_key not in TEMPLATES:
            raise PinError("Unknown template")
        pin.template_key = template_key
    _reset_approval(db, pin)
    asset = render_asset(db, pin, settings, http)
    db.commit()
    return asset


def approve(db: Session, pin: Pin) -> None:
    if pin.status not in ("ready_for_review", "rejected"):
        raise PinError(f"A pin in status '{pin.status.replace('_', ' ')}' cannot be approved")
    problems = check_destination(pin)
    if problems:
        raise PinError("Cannot approve: " + "; ".join(problems))
    if pin.current_asset is None:
        raise PinError("Cannot approve: the pin has no image yet")
    pin.status, pin.approved_at = "approved", utcnow()
    db.commit()


def reject(db: Session, pin: Pin) -> None:
    if pin.status in ("published", "publishing"):
        raise PinError("Published pins cannot be rejected")
    pin.status, pin.approved_at = "rejected", None
    if pin.queue_item is not None:
        db.delete(pin.queue_item)
        pin.queue_item = None
    db.commit()


def set_destination_from_link(db: Session, pin: Pin) -> None:
    """Re-sync after the product's affiliate link was legitimately changed (e.g. corrected profit link)."""
    pin.destination_url = pin.affiliate_link.affiliate_url
    _reset_approval(db, pin)
    db.commit()


_ = (datetime, first_sentence)  # re-exported for callers/tests
