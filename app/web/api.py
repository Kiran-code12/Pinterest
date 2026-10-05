"""JSON API (same services as the dashboard). Session cookie auth; state-changing calls need X-CSRF-Token
(GET /api/csrf)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..models import AffiliateProviderRow, Pin, Product
from ..pinterest.errors import PinterestError
from ..providers.base import ProviderError
from ..services import drafts as draft_svc
from ..services import pins as pin_svc
from ..services import products as product_svc
from ..services import publishing as pub_svc
from ..services.templates import TEMPLATES
from . import deps

router = APIRouter()
auth = [Depends(deps.current_user)]
secure = [Depends(deps.current_user), Depends(deps.verify_csrf)]


def product_json(p: Product) -> dict:
    link = p.primary_link
    return {"id": p.id, "provider": p.provider.key, "external_id": p.external_id, "title": p.title,
            "brand": p.brand, "category": p.category, "status": p.status, "score": p.score,
            "score_breakdown": p.score_breakdown, "price": float(p.price_value) if p.price_value is not None else None,
            "currency": p.price_currency, "product_url": p.product_url,
            "affiliate_url": link.affiliate_url if link else None, "affiliate_link_valid": link.is_valid if link else None}


def pin_json(p: Pin) -> dict:
    return {"id": p.id, "product_id": p.product_id, "status": p.status, "template": p.template_key,
            "concept": p.concept_key, "headline": p.headline, "supporting_text": p.supporting_text,
            "seo_title": p.seo_title, "seo_description": p.seo_description, "keywords": p.keywords, "cta": p.cta,
            "destination_url": p.destination_url, "affiliate_link_id": p.affiliate_link_id,
            "show_price": p.show_price, "show_disclosure": p.show_disclosure, "ai_source": p.ai_source,
            "warnings": p.warnings or [], "destination_problems": pin_svc.check_destination(p),
            "image_url": f"/assets/pin/{p.id}" if p.current_asset else None,
            "queue": ({"status": p.queue_item.status, "scheduled_for": p.queue_item.scheduled_for.isoformat()
                       if p.queue_item.scheduled_for else None} if p.queue_item else None)}


@router.get("/health")
def health(request: Request):
    try:
        db_ok = request.app.state.db.ping()
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "version": __version__}


@router.get("/csrf", dependencies=auth)
def csrf(request: Request):
    return {"csrf_token": deps.csrf_token(request)}


@router.get("/providers", dependencies=auth)
def providers(request: Request):
    return [p.status() for p in request.app.state.registry.values()]


@router.get("/products", dependencies=auth)
def list_products(q: str = "", status: str = "", category: str = "", limit: int = 50, db: Session = Depends(deps.get_db)):
    stmt = select(Product)
    if q:
        stmt = stmt.where(Product.title.ilike(f"%{q[:80]}%"))
    if status:
        stmt = stmt.where(Product.status == status)
    if category:
        stmt = stmt.where(Product.category == category)
    return [product_json(p) for p in db.scalars(stmt.order_by(Product.score.desc()).limit(max(1, min(limit, 200))))]


@router.get("/products/{pid}", dependencies=auth)
def get_product(pid: int, db: Session = Depends(deps.get_db)):
    p = db.get(Product, pid)
    if p is None:
        raise HTTPException(404, "Product not found")
    return product_json(p)


class DiscoverBody(BaseModel):
    request: str = Field("", max_length=300)
    category: str | None = None
    providers: list[str] | None = None
    limit: int = Field(20, ge=1, le=100)


@router.post("/products/discover", dependencies=secure)
def discover(body: DiscoverBody, request: Request, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    rows = {r.key: r for r in db.scalars(select(AffiliateProviderRow))}
    out = product_svc.discover(db, request.app.state.registry, rows, body.request or f"{body.limit} products",
                               body.category, body.providers, body.limit)
    return {"messages": out.messages, "batch_id": out.batch_id, "products": [product_json(p) for p in out.products]}


class ImportBody(BaseModel):
    provider: str
    rows: list[dict] = Field(max_length=500)


@router.post("/products/import", dependencies=secure)
def import_rows(body: ImportBody, request: Request, db: Session = Depends(deps.get_db)):
    prov = request.app.state.registry.get(body.provider)
    row = db.scalar(select(AffiliateProviderRow).where(AffiliateProviderRow.key == body.provider))
    if prov is None or row is None or not hasattr(prov, "import_row"):
        raise HTTPException(400, "Provider does not support import")
    created, errors = 0, []
    for n, rec in enumerate(body.rows, start=1):
        try:
            res = product_svc.upsert_product(db, prov, row, prov.import_row({k: str(v) for k, v in rec.items()}), "import")
            created += res.created
        except ProviderError as ex:
            errors.append(f"Row {n}: {ex}")
    db.commit()
    return {"created": created, "errors": errors}


class StatusBody(BaseModel):
    status: str


@router.post("/products/{pid}/status", dependencies=secure)
def product_status(pid: int, body: StatusBody, db: Session = Depends(deps.get_db)):
    p = db.get(Product, pid)
    if p is None:
        raise HTTPException(404, "Product not found")
    if body.status not in ("discovered", "selected", "archived"):
        raise HTTPException(400, "Bad status")
    product_svc.set_status(db, p, body.status)
    return product_json(p)


class GenerateBody(BaseModel):
    product_id: int
    count: int = Field(3, ge=1, le=10)
    templates: list[str] = []
    extra_product_ids: list[int] = []
    show_price: bool = False
    show_disclosure: bool = True


@router.post("/pins/generate", dependencies=secure)
def generate(body: GenerateBody, request: Request, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    s = request.app.state
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    try:
        pins = pin_svc.create_pins(db, product, s.ai, s.settings, count=body.count, template_keys=body.templates,
                                   extra_product_ids=body.extra_product_ids, show_price=body.show_price,
                                   show_disclosure=body.show_disclosure, http=s.http)
    except pin_svc.PinError as ex:
        raise HTTPException(400, str(ex)) from ex
    return [pin_json(p) for p in pins]


@router.get("/pins", dependencies=auth)
def list_pins(status: str = "", product_id: int = 0, db: Session = Depends(deps.get_db)):
    stmt = select(Pin)
    if status:
        stmt = stmt.where(Pin.status == status)
    if product_id:
        stmt = stmt.where(Pin.product_id == product_id)
    return [pin_json(p) for p in db.scalars(stmt.order_by(Pin.id.desc()).limit(200))]


def _pin(db: Session, pid: int) -> Pin:
    pin = db.get(Pin, pid)
    if pin is None:
        raise HTTPException(404, "Pin not found")
    return pin


@router.get("/pins/{pid}", dependencies=auth)
def get_pin(pid: int, db: Session = Depends(deps.get_db)):
    return pin_json(_pin(db, pid))


class PinPatch(BaseModel):
    headline: str | None = Field(None, max_length=200)
    supporting_text: str | None = Field(None, max_length=300)
    seo_title: str | None = Field(None, max_length=200)
    seo_description: str | None = Field(None, max_length=1000)
    keywords: list[str] | None = None
    cta: str | None = Field(None, max_length=80)
    template_key: str | None = None
    show_price: bool | None = None
    show_disclosure: bool | None = None


@router.patch("/pins/{pid}", dependencies=secure)
def patch_pin(pid: int, body: PinPatch, request: Request, db: Session = Depends(deps.get_db)):
    pin, s = _pin(db, pid), request.app.state
    try:
        warnings = pin_svc.update_pin(db, pin, body.model_dump(exclude_none=True), s.settings, s.http)
    except pin_svc.PinError as ex:
        raise HTTPException(400, str(ex)) from ex
    return {**pin_json(pin), "edit_warnings": warnings}


def _action(db: Session, pid: int, fn):
    pin = _pin(db, pid)
    try:
        fn(pin)
    except (pin_svc.PinError, pub_svc.PublishError, ProviderError, PinterestError) as ex:
        raise HTTPException(400, str(ex)) from ex
    db.refresh(pin)
    return pin_json(pin)


@router.post("/pins/{pid}/approve", dependencies=secure)
def approve(pid: int, db: Session = Depends(deps.get_db)):
    return _action(db, pid, lambda p: pin_svc.approve(db, p))


@router.post("/pins/{pid}/reject", dependencies=secure)
def reject(pid: int, db: Session = Depends(deps.get_db)):
    return _action(db, pid, lambda p: pin_svc.reject(db, p))


@router.post("/pins/{pid}/regenerate-copy", dependencies=secure)
def regenerate(pid: int, request: Request, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    s = request.app.state
    return _action(db, pid, lambda p: pin_svc.regenerate_copy(db, p, s.ai, s.settings, s.http))


@router.post("/pins/{pid}/queue", dependencies=secure)
def queue(pid: int, request: Request, db: Session = Depends(deps.get_db)):
    s = request.app.state
    return _action(db, pid, lambda p: pub_svc.enqueue(db, p, s.settings, allow_demo=s.pinterest.provider.simulated))


class PublishBody(BaseModel):
    board_id: str | None = None
    force: bool = False


@router.post("/pins/{pid}/publish", dependencies=secure)
def publish(pid: int, request: Request, body: PublishBody | None = None, db: Session = Depends(deps.get_db)):
    """Publish an APPROVED pin to the connected Pinterest account. 409 = possible duplicate (retry with force)."""
    deps.rate_limit(request)
    body, st, pin = body or PublishBody(), request.app.state, _pin(db, pid)
    try:
        pub = pub_svc.publish_pin(db, pin, st.settings, st.pinterest, force=body.force, board_id=body.board_id)
    except pub_svc.DuplicateWarning as ex:
        raise HTTPException(409, {"duplicate": True, "message": "Possible duplicate: resend with force=true to publish anyway.",
                                  "findings": [f.reason for f in ex.findings]}) from ex
    except pub_svc.PublishFailed as ex:
        raise HTTPException(502 if not ex.error.needs_reconnect else 401,
                            {"error": str(ex), "code": ex.error.code, "needs_reconnect": ex.error.needs_reconnect,
                             "retryable": ex.error.retryable}) from ex
    except pub_svc.PublishError as ex:
        raise HTTPException(400, str(ex)) from ex
    db.refresh(pin)
    return {"pin": pin_json(pin), "pinterest_pin_id": pub.pinterest_pin_id, "pinterest_url": pub.pinterest_url,
            "board_id": pub.board_id, "simulated": pub.simulated, "published_at": pub.published_at.isoformat()}


@router.get("/pinterest/status", dependencies=auth)
def pinterest_status(request: Request, db: Session = Depends(deps.get_db)):
    st = request.app.state
    conn = st.pinterest.get(db)
    return {"provider": st.pinterest.provider.name, "simulated": st.pinterest.provider.simulated,
            "configured": st.pinterest.provider.is_configured(), "connected": bool(conn and conn.status == "connected"),
            "status": conn.status if conn else "disconnected", "username": conn.username if conn else None,
            "can_publish": st.pinterest.can_publish(conn), "default_board_id": conn.default_board_id if conn else None,
            "boards": [{"id": b.board_id, "name": b.name} for b in conn.boards] if conn else []}


@router.get("/pins/{pid}/texts", dependencies=auth)
def pin_texts(pid: int, db: Session = Depends(deps.get_db)):
    """Exactly what to paste into Pinterest: title, description (with disclosure), affiliate URL, alt text."""
    return draft_svc.final_texts(db, _pin(db, pid))


class ManualPublishBody(BaseModel):
    pinterest_url: str | None = None
    board_name: str | None = Field(None, max_length=200)


@router.post("/pins/{pid}/mark-published", dependencies=secure)
def mark_published_manually(pid: int, body: ManualPublishBody | None = None, db: Session = Depends(deps.get_db)):
    body = body or ManualPublishBody()
    return _action(db, pid, lambda p: draft_svc.mark_published_manually(db, p, body.pinterest_url, body.board_name))


@router.post("/pins/{pid}/move-to-draft", dependencies=secure)
def move_to_draft(pid: int, db: Session = Depends(deps.get_db)):
    return _action(db, pid, lambda p: draft_svc.move_back_to_draft(db, p))


@router.post("/pins/{pid}/archive", dependencies=secure)
def archive_pin(pid: int, db: Session = Depends(deps.get_db)):
    return _action(db, pid, lambda p: draft_svc.archive(db, p))


@router.post("/pins/{pid}/restore", dependencies=secure)
def restore_pin(pid: int, db: Session = Depends(deps.get_db)):
    return _action(db, pid, lambda p: draft_svc.restore(db, p))


@router.delete("/pins/{pid}", dependencies=secure)
def delete_pin(pid: int, request: Request, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    try:
        removed = draft_svc.delete(db, pin, request.app.state.settings)
    except pin_svc.PinError as ex:
        raise HTTPException(400, str(ex)) from ex
    return {"deleted": pid, "image_files_removed": removed}


@router.get("/templates", dependencies=auth)
def list_templates():
    return [{"key": t.key, "name": t.name, "description": t.description} for t in TEMPLATES.values()]


@router.get("/stats", dependencies=auth)
def stats(db: Session = Depends(deps.get_db)):
    from .pages import dashboard_stats
    return dashboard_stats(db)
