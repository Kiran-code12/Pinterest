"""Product ingestion: normalize, dedupe, store affiliate links, import CSV, discover via providers."""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AffiliateLink, AffiliateProviderRow, Product, ProductSource
from ..providers.base import AffiliateProvider, Capability, NotSupported, ProductData, ProviderError
from .scoring import score_product

log = logging.getLogger("engine.products")
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 500
CATEGORIES = ["Beauty", "Skincare", "Makeup", "Fashion", "Home", "Electronics", "Lifestyle"]


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower()).strip()


def dedupe_key(title: str, brand: str | None) -> str:
    tokens = sorted(set(normalize_text(f"{brand or ''} {title}").split()))
    return hashlib.sha1(" ".join(tokens).encode()).hexdigest()


def clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value)).strip()
    return value[:limit] or None


@dataclass
class UpsertResult:
    product: Product
    created: bool
    warnings: list[str] = field(default_factory=list)


def _link_state(product: Product) -> str:
    link = product.primary_link
    return "missing" if link is None else ("valid" if link.is_valid else "invalid")


def rescore(db: Session, product: Product, query: str | None = None) -> None:
    dup = db.scalar(select(Product.id).where(Product.dedupe_key == product.dedupe_key, Product.id != product.id,
                                             Product.status != "archived", Product.id < product.id).limit(1))
    price = float(product.price_value) if product.price_value is not None else None
    product.score, product.score_breakdown = score_product(
        title=product.title, brand=product.brand, category=product.category, description=product.description,
        price=price, has_image=bool(product.image_url or product.local_image_path), link_state=_link_state(product),
        is_duplicate=dup is not None, query=query)


def upsert_product(db: Session, provider: AffiliateProvider, row: AffiliateProviderRow, data: ProductData,
                   source_type: str, batch_id: str | None = None, query: str | None = None) -> UpsertResult:
    from ..security import UnsafeURL, validate_http_url
    title = clean(data.title, 500)
    if not title:
        raise ProviderError("Product has no title")
    ext = clean(data.external_id, 128)
    if not ext:
        raise ProviderError("Product has no external id")
    warnings: list[str] = []
    product_url = None
    if data.product_url:
        try:
            product_url = validate_http_url(data.product_url)
        except UnsafeURL:
            warnings.append("Product URL was invalid and was ignored")
    image_url = None
    if data.image_url:
        if data.image_url.startswith("demo://"):
            image_url = data.image_url
        else:
            try:
                image_url = validate_http_url(data.image_url)
            except UnsafeURL:
                warnings.append("Image URL was invalid and was ignored")

    product = db.scalar(select(Product).where(Product.provider_id == row.id, Product.external_id == ext))
    created = product is None
    if created:
        product = Product(provider_id=row.id, external_id=ext, title=title, dedupe_key=dedupe_key(title, data.brand))
        product.provider = row
        db.add(product)
    product.title = title
    product.brand = clean(data.brand, 120) or product.brand
    product.category = clean(data.category, 80) or product.category
    product.description = clean(data.description, 4000) or product.description
    product.product_url = product_url or product.product_url
    if image_url and image_url != product.image_url:
        product.image_url, product.local_image_path = image_url, None
    if data.price is not None:
        product.price_value, product.price_currency = data.price, data.currency or "INR"
        product.price_fetched_at = data.price_fetched_at or __import__("datetime").datetime.utcnow()
    product.availability = clean(data.availability, 64) or product.availability
    product.dedupe_key = dedupe_key(product.title, product.brand)
    db.flush()

    # ---- affiliate link: never dropped, never replaced by a plain product URL ----
    affiliate_url = data.affiliate_url
    if not affiliate_url and provider.can(Capability.AFFILIATE_LINK):
        try:
            affiliate_url = provider.get_affiliate_link(data)
        except ProviderError:
            affiliate_url = None
    existing = next((lnk for lnk in product.affiliate_links if lnk.provider_id == row.id), None)
    if affiliate_url:
        ok, msg = provider.validate_affiliate_url(affiliate_url)
        if affiliate_url == product_url and not provider.is_demo:
            ok, msg = False, "Affiliate URL is identical to the plain product URL"
        if existing is None:
            product.affiliate_links.append(AffiliateLink(
                provider_id=row.id, original_url=product_url, affiliate_url=affiliate_url,
                link_kind="demo" if provider.is_demo else ("api" if source_type == "api" else "import"),
                is_valid=ok, validation_message=None if ok else msg[:255]))
        elif existing.affiliate_url != affiliate_url:
            if ok or not existing.is_valid:  # a valid link is never overwritten by an invalid one
                existing.affiliate_url, existing.is_valid = affiliate_url, ok
                existing.validation_message = None if ok else msg[:255]
        if existing is not None and product_url:
            existing.original_url = product_url
        if not ok:
            warnings.append(f"Affiliate link problem: {msg}")
    elif existing is None:
        warnings.append("No affiliate link available: pins cannot be created until you add one")
    db.add(ProductSource(product_id=product.id, source_type=source_type, batch_id=batch_id,
                         raw={"external_id": ext, "title": title, "warnings": warnings}))
    db.flush()
    db.refresh(product)
    rescore(db, product, query)
    return UpsertResult(product, created, warnings)


# ---- parsing a natural-language discovery request ----------------------------------------------------
@dataclass
class DiscoveryRequest:
    count: int
    category: str | None
    query: str


def parse_request(text: str, category: str | None = None, default_count: int = 20) -> DiscoveryRequest:
    text = (text or "").strip()
    m = re.search(r"\b(\d{1,3})\b", text)
    count = max(1, min(int(m.group(1)), 100)) if m else default_count
    cat = category if category in CATEGORIES else None
    if not cat:
        for c in CATEGORIES:
            if re.search(rf"\b{c.lower()}\b", text.lower()):
                cat = c
                break
    stop = {"find", "show", "get", "products", "product", "suitable", "for", "pinterest", "the", "and", "some",
            "good", "best", "items", "item", "with", "that", "are", "me", "pins", "pin"}
    words = [w for w in re.findall(r"[A-Za-z]{3,}", text.lower()) if w not in stop and w != (cat or "").lower()]
    return DiscoveryRequest(count, cat, " ".join(words))


@dataclass
class DiscoveryOutcome:
    products: list[Product] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    batch_id: str = ""


def discover(db: Session, registry: dict[str, AffiliateProvider], rows: dict[str, AffiliateProviderRow],
             request_text: str, category: str | None = None, provider_keys: list[str] | None = None,
             limit: int | None = None) -> DiscoveryOutcome:
    req = parse_request(request_text, category)
    if limit:
        req.count = max(1, min(limit, 100))
    outcome = DiscoveryOutcome(batch_id=uuid.uuid4().hex[:12])
    keys = provider_keys or list(registry)
    for key in keys:
        provider = registry.get(key)
        if provider is None:
            outcome.messages.append(f"Unknown provider '{key}' skipped")
            continue
        if not provider.can(Capability.SEARCH):
            outcome.messages.append(f"{provider.name}: no official search available "
                                    f"({'not configured' if provider.kind == 'api' else 'use import'}); skipped")
            continue
        try:
            found = provider.search_products(req.query or (req.category or ""), category=req.category,
                                             limit=req.count)
        except NotSupported as ex:
            outcome.messages.append(str(ex))
            continue
        except ProviderError as ex:
            outcome.messages.append(f"{provider.name}: {ex}")
            continue
        added = 0
        for data in found:
            try:
                res = upsert_product(db, provider, rows[key], data, "demo" if provider.is_demo else "api",
                                     outcome.batch_id, req.query or None)
            except ProviderError as ex:
                outcome.messages.append(f"{provider.name}: skipped a product ({ex})")
                continue
            outcome.products.append(res.product)
            added += 1
        outcome.messages.append(f"{provider.name}: {added} products processed")
    db.commit()
    outcome.products.sort(key=lambda p: p.score, reverse=True)
    return outcome


# ---- CSV import ---------------------------------------------------------------------------------------
@dataclass
class ImportOutcome:
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def import_csv(db: Session, provider: AffiliateProvider, row: AffiliateProviderRow, raw: bytes) -> ImportOutcome:
    if len(raw) > MAX_CSV_BYTES:
        raise ProviderError("CSV is larger than 1MB")
    if not hasattr(provider, "import_row"):
        raise ProviderError(f"{provider.name} does not support CSV import")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as ex:
        raise ProviderError("CSV must be UTF-8 encoded") from ex
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ProviderError("CSV has no header row")
    reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
    out, batch = ImportOutcome(), uuid.uuid4().hex[:12]
    for n, rec in enumerate(reader, start=2):
        if n - 1 > MAX_CSV_ROWS:
            out.errors.append(f"Stopped after {MAX_CSV_ROWS} rows")
            break
        try:
            data = provider.import_row({k: (v or "") for k, v in rec.items() if k})
            res = upsert_product(db, provider, row, data, "import", batch)
        except ProviderError as ex:
            out.errors.append(f"Row {n}: {ex}")
            continue
        out.created += res.created
        out.updated += not res.created
        out.warnings += [f"Row {n}: {w}" for w in res.warnings]
    db.commit()
    return out


def set_status(db: Session, product: Product, status: str) -> None:
    if status not in {"discovered", "selected", "archived"}:
        raise ValueError("bad status")
    product.status = status
    db.commit()
