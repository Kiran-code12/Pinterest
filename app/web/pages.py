"""Server-rendered dashboard (Jinja2). All routes require login; all POSTs require a CSRF token."""
from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    AffiliateLink,
    AffiliateProviderRow,
    AnalyticsRecord,
    Pin,
    PinTemplate,
    Product,
    PublishedPin,
    PublishingQueueItem,
    User,
)
from ..pinterest.errors import PinterestError
from ..providers.base import Capability, ProviderError
from ..security import UnsafeURL, hash_password, validate_http_url, verify_password
from ..services import analytics as analytics_svc
from ..services import app_settings
from ..services import drafts as draft_svc
from ..services import pins as pin_svc
from ..services import products as product_svc
from ..services import publishing as pub_svc
from ..services.content import price_for_display
from ..services.imagery import ensure_product_image
from ..services.scoring import SCORE_LABEL
from ..services.templates import TEMPLATES, TemplateContext, render_template
from . import deps

log = logging.getLogger("engine.pages")
BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()
PAGE = 24
_DUMMY_HASH = hash_password("dummy-password-for-timing")


# ---- helpers ------------------------------------------------------------------------------------------
def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append([kind, message])


def render(request: Request, name: str, status_code: int = 200, **ctx):
    settings = request.app.state.settings
    uid = request.session.get("uid")
    messages = request.session.pop("flash", [])
    pconn = request.app.state.pinterest
    ctx.update(csrf_token=deps.csrf_token(request), flash_messages=messages, logged_in=bool(uid),
               direct_publishing=settings.direct_publishing_enabled,
               simulation=settings.direct_publishing_enabled and pconn.provider.simulated,
               score_label=SCORE_LABEL, app_env=settings.env,
               local_dt=lambda dt: (pub_svc.to_local(dt, settings).strftime("%d %b %Y, %H:%M")
                                    if dt else "-"))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def render_error(request: Request, status: int, message: str):
    titles = {404: "Not found", 403: "Not allowed", 429: "Slow down", 500: "Something went wrong"}
    return render(request, "error.html", status_code=status, status=status, title=titles.get(status, "Error"),
                  message=message)


def get_or_404(db: Session, model, ident: int):
    obj = db.get(model, ident)
    if obj is None:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


auth = [Depends(deps.current_user)]
secure = [Depends(deps.current_user), Depends(deps.verify_csrf)]


# ---- auth ---------------------------------------------------------------------------------------------
@router.get("/")
def home(request: Request):
    return back("/dashboard" if request.session.get("uid") else "/login")


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login", dependencies=[Depends(deps.verify_csrf)])
def login(request: Request, username: str = Form(""), password: str = Form(""), db: Session = Depends(deps.get_db)):
    deps.rate_limit(request, "login")
    user = db.scalar(select(User).where(User.username == username.strip()))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH) and user is not None
    if not ok:
        flash(request, "Wrong username or password.", "error")
        return back("/login")
    request.session.clear()
    request.session["uid"] = user.id
    deps.csrf_token(request)
    return back("/dashboard")


@router.post("/logout", dependencies=[Depends(deps.verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return back("/login")


# ---- dashboard ----------------------------------------------------------------------------------------
def dashboard_stats(db: Session) -> dict:
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0
    return {
        "products_found": count(Product, Product.status != "archived"),
        "products_selected": count(Product, Product.status == "selected"),
        "pins_generated": count(Pin),
        "drafts": count(Pin, Pin.status.in_(draft_svc.ACTIVE_VIEWS["drafts"])),
        "published_manually": count(Pin, Pin.status == "published_manually"),
        "archived": count(Pin, Pin.status == "archived"),
        "pins_approved": count(Pin, Pin.status == "approved"),
        "pins_scheduled": count(Pin, Pin.status == "scheduled"),
        "pins_published": count(Pin, Pin.status == "published"),
        "pins_failed": count(Pin, Pin.status == "failed"),
    }


@router.get("/dashboard", dependencies=auth)
def dashboard(request: Request, db: Session = Depends(deps.get_db)):
    s = request.app.state.settings
    recent = db.scalars(select(Pin).options(selectinload(Pin.assets), selectinload(Pin.product))
                        .where(Pin.status != "archived").order_by(Pin.id.desc()).limit(8)).all()
    queue = db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.status == "scheduled")
                       .order_by(PublishingQueueItem.scheduled_for).limit(6)).all()
    registry = request.app.state.registry
    notices = []
    if s.enable_demo_provider:
        notices.append("Demo catalog is enabled: demo products/pins are fictional and can never be published.")
    if not registry["amazon"].is_configured():
        notices.append("Amazon Creators API is not configured: use CSV import or add credentials (see Settings).")
    conn = request.app.state.pinterest.get(db)
    if not s.direct_publishing_enabled:
        notices.append("Manual workflow: pins are saved as drafts. Download the image, copy the title, description and "
                       "affiliate link from the Draft Library and post on Pinterest yourself.")
    return render(request, "dashboard.html", stats=dashboard_stats(db), recent=recent, queue=queue,
                  perf=analytics_svc.pinterest_reported(db), notices=notices, conn=conn,
                  published_today=pub_svc.published_today(db, s), max_per_day=s.max_pins_per_day)


# ---- products -----------------------------------------------------------------------------------------
@router.get("/products", dependencies=auth)
def products_page(request: Request, q: str = "", provider: str = "", category: str = "", status: str = "",
                  page: int = 1, db: Session = Depends(deps.get_db)):
    stmt = select(Product).options(selectinload(Product.affiliate_links), selectinload(Product.provider))
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Product.title.ilike(like), Product.brand.ilike(like)))
    if provider:
        stmt = stmt.join(AffiliateProviderRow).where(AffiliateProviderRow.key == provider)
    if category:
        stmt = stmt.where(Product.category == category)
    stmt = stmt.where(Product.status == status) if status else stmt.where(Product.status != "archived")
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    page = max(1, page)
    items = db.scalars(stmt.order_by(Product.score.desc(), Product.id.desc()).limit(PAGE).offset((page - 1) * PAGE)).all()
    registry = request.app.state.registry
    return render(request, "products.html", products=items, total=total, page=page, pages=-(-total // PAGE) or 1,
                  q=q, provider_filter=provider, category_filter=category, status_filter=status,
                  categories=product_svc.CATEGORIES, providers=[p.status() for p in registry.values()],
                  importable=[k for k, p in registry.items() if hasattr(p, "import_row")])


@router.post("/products/discover", dependencies=secure)
def discover(request: Request, request_text: str = Form(""), category: str = Form(""),
             providers: list[str] = Form(default=[]), limit: int = Form(20), db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    s = request.app.state
    rows = {r.key: r for r in db.scalars(select(AffiliateProviderRow))}
    out = product_svc.discover(db, s.registry, rows, request_text or f"{limit} {category} products",
                               category or None, providers or None, limit)
    for m in out.messages:
        flash(request, m, "info")
    flash(request, f"Found {len(out.products)} products (sorted by content-opportunity score).", "ok")
    return back("/products")


@router.post("/products/import", dependencies=secure)
async def import_products(request: Request, provider: str = Form(...), file: UploadFile = File(...),
                          db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    prov = request.app.state.registry.get(provider)
    row = db.scalar(select(AffiliateProviderRow).where(AffiliateProviderRow.key == provider))
    if prov is None or row is None or not hasattr(prov, "import_row"):
        flash(request, "Choose a provider that supports import.", "error")
        return back("/products")
    raw = await file.read(product_svc.MAX_CSV_BYTES + 1)
    try:
        out = product_svc.import_csv(db, prov, row, raw)
    except ProviderError as ex:
        flash(request, str(ex), "error")
        return back("/products")
    flash(request, f"Import finished: {out.created} new, {out.updated} updated, {len(out.errors)} rows skipped.", "ok")
    for msg in (out.errors + out.warnings)[:8]:
        flash(request, msg, "warn")
    return back("/products")


@router.post("/products/add", dependencies=secure)
def add_product(request: Request, provider: str = Form(...), title: str = Form(""), brand: str = Form(""),
                category: str = Form(""), description: str = Form(""), image_url: str = Form(""),
                product_url: str = Form(""), affiliate_url: str = Form(""), price: str = Form(""),
                asin: str = Form(""), db: Session = Depends(deps.get_db)):
    prov = request.app.state.registry.get(provider)
    row = db.scalar(select(AffiliateProviderRow).where(AffiliateProviderRow.key == provider))
    if prov is None or row is None or not hasattr(prov, "import_row"):
        flash(request, "Choose Amazon or EarnKaro for manual entry.", "error")
        return back("/products")
    try:
        data = prov.import_row({"title": title, "brand": brand, "category": category, "description": description,
                                "image_url": image_url, "product_url": product_url, "affiliate_url": affiliate_url,
                                "price": price, "asin": asin})
        res = product_svc.upsert_product(db, prov, row, data, "manual")
        db.commit()
    except ProviderError as ex:
        flash(request, str(ex), "error")
        return back("/products")
    flash(request, f"{'Added' if res.created else 'Updated'}: {res.product.title}", "ok")
    for w in res.warnings:
        flash(request, w, "warn")
    return back(f"/products/{res.product.id}")


@router.get("/products/sample.csv", dependencies=auth)
def sample_csv(provider: str = "earnkaro"):
    if provider == "amazon":
        body = ("asin,title,brand,category,description,image_url,price\n"
                "B0XXXXXXXX,Example product title,Example brand,Skincare,Short factual description,"
                "https://m.media-amazon.com/images/I/example.jpg,499\n")
    else:
        body = ("title,affiliate_url,brand,category,description,image_url,product_url,price\n"
                "Example product title,https://ekaro.in/enkr2020/xxxxx,Example brand,Beauty,Short factual description,"
                "https://example.com/image.jpg,https://example.com/product,499\n")
    return Response(body, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sample.csv"})


@router.get("/products/{pid}", dependencies=auth)
def product_detail(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    p = get_or_404(db, Product, pid)
    ensure_product_image(p, request.app.state.settings, request.app.state.http)
    db.commit()
    pins = db.scalars(select(Pin).where(Pin.product_id == pid).options(selectinload(Pin.assets))
                      .order_by(Pin.id.desc())).all()
    return render(request, "product_detail.html", p=p, pins=pins, price_shown=price_for_display(p))


@router.post("/products/{pid}/status", dependencies=secure)
def product_status(request: Request, pid: int, status: str = Form(...), db: Session = Depends(deps.get_db)):
    p = get_or_404(db, Product, pid)
    if status not in ("discovered", "selected", "archived"):
        raise HTTPException(status_code=400, detail="Bad status")
    if status == "selected":
        ensure_product_image(p, request.app.state.settings, request.app.state.http)
    product_svc.set_status(db, p, status)
    flash(request, f"Product marked {status}.", "ok")
    return back(f"/products/{pid}")


@router.post("/products/{pid}/link", dependencies=secure)
def product_link(request: Request, pid: int, affiliate_url: str = Form(""), db: Session = Depends(deps.get_db)):
    p = get_or_404(db, Product, pid)
    prov = request.app.state.registry.get(p.provider.key)
    try:
        url = validate_http_url(affiliate_url)
    except UnsafeURL as ex:
        flash(request, f"Link rejected: {ex}", "error")
        return back(f"/products/{pid}")
    ok, msg = prov.validate_affiliate_url(url)
    if not ok:
        flash(request, f"Link rejected: {msg}", "error")
        return back(f"/products/{pid}")
    link = p.primary_link
    if link is None:
        link = AffiliateLink(product_id=p.id, provider_id=p.provider_id, original_url=p.product_url,
                             affiliate_url=url, link_kind="manual")
        p.affiliate_links.append(link)
    else:
        link.affiliate_url, link.link_kind = url, "manual"
    link.is_valid, link.validation_message = True, None
    db.flush()
    reset = 0
    for pin in db.scalars(select(Pin).where(Pin.product_id == pid, Pin.status != "published")):
        if pin.affiliate_link_id == link.id:
            pin_svc.set_destination_from_link(db, pin)
            reset += 1
    product_svc.rescore(db, p)
    db.commit()
    flash(request, "Affiliate link saved." + (f" {reset} unpublished pins were reset to 'awaiting approval'." if reset else ""), "ok")
    return back(f"/products/{pid}")


# ---- create workflow ----------------------------------------------------------------------------------
@router.get("/create", dependencies=auth)
def create_page(request: Request, product_id: int = 0, db: Session = Depends(deps.get_db)):
    prods = db.scalars(select(Product).options(selectinload(Product.affiliate_links))
                       .where(Product.status != "archived").order_by(Product.status.desc(), Product.score.desc())
                       .limit(200)).all()
    return render(request, "create.html", products=prods, selected_id=product_id, templates_list=list(TEMPLATES.values()),
                  ai_name=request.app.state.ai.name)


@router.post("/create", dependencies=secure)
def create_submit(request: Request, product_id: int = Form(...), count: int = Form(3),
                  template_keys: list[str] = Form(default=[]), extra_product_ids: list[int] = Form(default=[]),
                  show_price: str = Form(""), show_disclosure: str = Form(""), db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    s = request.app.state
    product = get_or_404(db, Product, product_id)
    try:
        created = pin_svc.create_pins(db, product, s.ai, s.settings, count=count, template_keys=template_keys,
                                      extra_product_ids=extra_product_ids, show_price=bool(show_price),
                                      show_disclosure=bool(show_disclosure), http=s.http)
    except pin_svc.PinError as ex:
        flash(request, str(ex), "error")
        return back(f"/create?product_id={product_id}")
    if product.status == "discovered":
        product.status = "selected"
        db.commit()
    flash(request, f"Saved {len(created)} drafts. Review them here, then download the image and copy the text "
                   "to post on Pinterest.", "ok")
    return back(f"/drafts?product_id={product_id}")


# ---- Draft Library (MVP: you publish manually) ---------------------------------------------------------------
def _pin(db: Session, pid: int) -> Pin:
    return get_or_404(db, Pin, pid)


@router.get("/pins", dependencies=auth)
def pins_redirect():
    return back("/drafts")


@router.get("/pins/{pid}", dependencies=auth)
def pin_redirect(pid: int):
    return back(f"/drafts/{pid}")


@router.get("/drafts", dependencies=auth)
def drafts_page(request: Request, view: str = "drafts", q: str = "", product_id: int = 0, template: str = "",
                page: int = 1, db: Session = Depends(deps.get_db)):
    view = view if view in ("drafts", "published", "archived", "all") else "drafts"
    pins, total = draft_svc.list_library(db, view, q, product_id, template, page)
    texts = {p.id: draft_svc.final_texts(db, p) for p in pins}
    return render(request, "drafts.html", pins=pins, texts=texts, total=total, view=view, q=q, product_id=product_id,
                  template_filter=template, page=max(1, page), pages=-(-total // draft_svc.PAGE_SIZE) or 1,
                  counts=draft_svc.library_counts(db), templates_list=list(TEMPLATES.values()))


@router.get("/drafts/{pid}", dependencies=auth)
def draft_detail(request: Request, pid: int, confirm: int = 0, board_id: str = "", db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    st = request.app.state
    direct = st.settings.direct_publishing_enabled
    published = db.scalar(select(PublishedPin).where(PublishedPin.pin_id == pid))
    ctx = dict(pin=pin, problems=pin_svc.check_destination(pin), templates_list=list(TEMPLATES.values()),
               texts=draft_svc.final_texts(db, pin), published=published, similar=draft_svc.similar_published(db, pin),
               can_edit=pin.status not in ("published", "publishing", "published_manually", "archived"),
               conn=None, boards=[], findings=[], blockers=[], confirm_board=board_id, simulated=False,
               effective_board=None, next_slot=None)
    if direct:  # Pinterest panels (kept for when direct publishing is enabled later)
        conn = st.pinterest.get(db)
        ctx.update(conn=conn, boards=list(conn.boards) if conn else [], simulated=st.pinterest.provider.simulated,
                   findings=pub_svc.find_duplicates(db, pin, st.pinterest, board_id or None) if confirm else [],
                   blockers=pub_svc.preflight(db, pin, st.pinterest, board_id or None)
                   if pin.status in pub_svc.PUBLISHABLE else [],
                   effective_board=pub_svc.resolve_board(db, pin, st.pinterest)[1] if conn else None,
                   next_slot=(pub_svc.next_slots(db, st.settings, 1) or [None])[0])
    return render(request, "draft_detail.html", **ctx)


@router.get("/drafts/{pid}/image.{ext}", dependencies=auth)
def draft_image(request: Request, pid: int, ext: str, db: Session = Depends(deps.get_db)):
    if ext not in ("png", "jpg"):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data, filename, media = draft_svc.image_download(_pin(db, pid), request.app.state.settings, ext)
    except pin_svc.PinError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/drafts/export.zip", dependencies=secure)
def drafts_export(request: Request, ids: list[int] = Form(default=[]), db: Session = Depends(deps.get_db)):
    pins = [p for p in (db.get(Pin, i) for i in ids[:100]) if p is not None]
    try:
        data = draft_svc.export_zip(db, pins, request.app.state.settings)
    except pin_svc.PinError as ex:
        flash(request, str(ex), "error")
        return back("/drafts")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="pinterest-drafts.zip"'})


@router.post("/drafts/bulk-archive", dependencies=secure)
def drafts_bulk_archive(request: Request, ids: list[int] = Form(default=[]), db: Session = Depends(deps.get_db)):
    done = 0
    for i in ids[:200]:
        pin = db.get(Pin, i)
        if pin is not None:
            try:
                draft_svc.archive(db, pin)
                done += 1
            except pin_svc.PinError:
                pass
    flash(request, f"Archived {done} drafts." if done else "Nothing selected.", "ok" if done else "warn")
    return back("/drafts")


@router.post("/drafts/{pid}/mark-published", dependencies=secure)
def draft_mark_published(request: Request, pid: int, pinterest_url: str = Form(""), board_name: str = Form(""),
                         db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: draft_svc.mark_published_manually(db, pin, pinterest_url or None,
                                                                           board_name or None),
                  "Marked as published manually. It now lives under 'Published' in the Draft Library.", db)


@router.post("/drafts/{pid}/move-to-draft", dependencies=secure)
def draft_move_back(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: draft_svc.move_back_to_draft(db, pin), "Moved back to drafts.", db)


@router.post("/drafts/{pid}/archive", dependencies=secure)
def draft_archive(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: draft_svc.archive(db, pin), "Archived. Find it under 'Archived'.", db)


@router.post("/drafts/{pid}/restore", dependencies=secure)
def draft_restore(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: draft_svc.restore(db, pin), "Restored.", db)


@router.post("/drafts/{pid}/delete", dependencies=secure)
def draft_delete(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    try:
        draft_svc.delete(db, pin, request.app.state.settings)
    except pin_svc.PinError as ex:
        flash(request, str(ex), "error")
        return back(f"/drafts/{pid}")
    flash(request, "Draft deleted permanently.", "ok")
    return back("/drafts")


def _guard(request: Request, pid: int, fn, ok_message: str, db: Session):
    try:
        fn()
        flash(request, ok_message, "ok")
    except (pin_svc.PinError, pub_svc.PublishError, ProviderError, PinterestError) as ex:
        flash(request, str(ex), "error")
    return back(f"/drafts/{pid}")


@router.post("/pins/{pid}/edit", dependencies=secure)
def pin_edit(request: Request, pid: int, headline: str = Form(""), supporting_text: str = Form(""),
             seo_title: str = Form(""), seo_description: str = Form(""), keywords: str = Form(""),
             cta: str = Form(""), template_key: str = Form(""), show_price: str = Form(""),
             show_disclosure: str = Form(""), db: Session = Depends(deps.get_db)):
    pin, s = _pin(db, pid), request.app.state
    try:
        warns = pin_svc.update_pin(db, pin, {
            "headline": headline, "supporting_text": supporting_text, "seo_title": seo_title,
            "seo_description": seo_description, "keywords": keywords, "cta": cta, "template_key": template_key,
            "show_price": bool(show_price), "show_disclosure": bool(show_disclosure)}, s.settings, s.http)
    except pin_svc.PinError as ex:
        flash(request, str(ex), "error")
        return back(f"/drafts/{pid}")
    flash(request, "Saved. Approval was reset: review and approve again.", "ok")
    for w in warns:
        flash(request, f"Check wording: {w}", "warn")
    return back(f"/drafts/{pid}")


@router.post("/pins/{pid}/regenerate-copy", dependencies=secure)
def pin_regen_copy(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    pin, s = _pin(db, pid), request.app.state
    return _guard(request, pid, lambda: pin_svc.regenerate_copy(db, pin, s.ai, s.settings, s.http),
                  "New copy generated (previous version kept in history).", db)


@router.post("/pins/{pid}/regenerate-image", dependencies=secure)
def pin_regen_image(request: Request, pid: int, template_key: str = Form(""), db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    pin, s = _pin(db, pid), request.app.state
    return _guard(request, pid, lambda: pin_svc.regenerate_image(db, pin, s.settings, template_key or None, s.http),
                  "Design re-rendered.", db)


@router.post("/pins/{pid}/approve", dependencies=secure)
def pin_approve(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: pin_svc.approve(db, pin), "Pin approved.", db)


@router.post("/pins/{pid}/reject", dependencies=secure)
def pin_reject(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: pin_svc.reject(db, pin), "Pin rejected.", db)


@router.post("/pins/{pid}/schedule", dependencies=secure)
def pin_schedule(request: Request, pid: int, when: str = Form(""), db: Session = Depends(deps.get_db)):
    pin, s = _pin(db, pid), request.app.state
    scheduled = None
    if when:
        try:  # <input type=datetime-local> is in the configured schedule timezone
            from datetime import timezone
            local = datetime.fromisoformat(when).replace(tzinfo=pub_svc._tz(s.settings))
            scheduled = local.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            flash(request, "Invalid date/time.", "error")
            return back(f"/drafts/{pid}")
    return _guard(request, pid, lambda: pub_svc.enqueue(db, pin, s.settings, scheduled_for=scheduled,
                                                         allow_demo=s.pinterest.provider.simulated),
                  "Pin scheduled. It is published at that time only when the scheduler runs (see Settings).", db)


@router.post("/pins/{pid}/unschedule", dependencies=secure)
def pin_unschedule(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: pub_svc.unschedule(db, pin), "Pin returned to approved.", db)


@router.post("/pins/{pid}/export", dependencies=secure)
def pin_export(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    try:
        data = draft_svc.export_zip(db, [pin], request.app.state.settings)
    except pin_svc.PinError as ex:
        flash(request, str(ex), "error")
        return back(f"/drafts/{pid}")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="pin-{pid}.zip"'})


@router.post("/pins/{pid}/board", dependencies=secure)
def pin_board(request: Request, pid: int, board_id: str = Form(""), db: Session = Depends(deps.get_db)):
    pin = _pin(db, pid)
    return _guard(request, pid, lambda: pub_svc.set_pin_board(db, pin, request.app.state.pinterest, board_id or None),
                  "Board saved for this pin.", db)


@router.post("/pins/{pid}/publish", dependencies=secure)
def pin_publish(request: Request, pid: int, board_id: str = Form(""), force: str = Form(""),
                db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    pin, st = _pin(db, pid), request.app.state
    try:
        pub = pub_svc.publish_pin(db, pin, st.settings, st.pinterest, force=bool(force), board_id=board_id or None)
        flash(request, ("SIMULATED publish (mock provider, nothing was sent to Pinterest). " if pub.simulated else
                        "Published successfully. ") + f"Pinterest pin ID: {pub.pinterest_pin_id}", "ok")
    except pub_svc.DuplicateWarning:
        flash(request, "Possible duplicate: please review and confirm below.", "warn")
        return back(f"/drafts/{pid}?confirm=1&board_id={board_id}")
    except pub_svc.PublishFailed as ex:
        flash(request, f"Publishing failed: {ex}", "error")
        if ex.error.needs_reconnect:
            flash(request, "Please reconnect Pinterest in Settings, then press Retry.", "warn")
    except pub_svc.PublishError as ex:
        flash(request, str(ex), "error")
    return back(f"/drafts/{pid}")


@router.post("/pins/schedule-approved", dependencies=secure)
def schedule_approved(request: Request, db: Session = Depends(deps.get_db)):
    s = request.app.state
    approved = db.scalars(select(Pin).where(Pin.status == "approved").order_by(Pin.approved_at)).all()
    slots = pub_svc.next_slots(db, s.settings, len(approved))
    done = 0
    for pin, slot in zip(approved, slots, strict=False):
        try:
            pub_svc.enqueue(db, pin, s.settings, scheduled_for=slot, allow_demo=s.pinterest.provider.simulated)
            done += 1
        except pub_svc.PublishError as ex:
            flash(request, f"Pin {pin.id}: {ex}", "error")
    flash(request, f"Scheduled {done} approved pins ({s.settings.max_pins_per_day} per day).", "ok")
    return back("/queue")


@router.post("/queue/process", dependencies=secure)
def queue_process(request: Request, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    s = request.app.state
    report = pub_svc.process_due(db, s.settings, s.pinterest)
    flash(request, f"Scheduler run: {report['published']} published, {report['failed']} failed, "
                   f"{report['skipped']} skipped.", "ok" if not report["failed"] else "warn")
    for m in report["messages"]:
        flash(request, m, "info")
    return back("/queue")


@router.get("/queue", dependencies=auth)
def queue_page(request: Request, db: Session = Depends(deps.get_db)):
    if not request.app.state.settings.direct_publishing_enabled:
        flash(request, "The publishing queue is only used when direct publishing is enabled. Use the Draft Library.", "info")
        return back("/drafts")
    items = db.scalars(select(PublishingQueueItem).options(selectinload(PublishingQueueItem.pin)
                                                           .selectinload(Pin.product))
                       .order_by(PublishingQueueItem.id.desc()).limit(200)).all()
    ready = db.scalars(select(Pin).options(selectinload(Pin.product)).where(Pin.status.in_(["draft", "approved"]))
                       .order_by(Pin.id.desc()).limit(100)).all()
    return render(request, "queue.html", items=items, ready=ready, scheduler_on=request.app.state.settings.scheduler_enabled)


# ---- assets (authenticated, path always comes from the database) ---------------------------------------
def _safe_file(request: Request, path: str | None) -> Path:
    if not path:
        raise HTTPException(status_code=404, detail="Not found")
    p, root = Path(path).resolve(), request.app.state.settings.assets_dir.resolve()
    if not p.is_relative_to(root) or not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return p


@router.get("/assets/pin/{pid}", dependencies=auth)
def pin_asset(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    asset = _pin(db, pid).current_asset
    return FileResponse(_safe_file(request, asset.path if asset else None), media_type="image/png")


@router.get("/assets/product/{pid}", dependencies=auth)
def product_asset(request: Request, pid: int, db: Session = Depends(deps.get_db)):
    return FileResponse(_safe_file(request, get_or_404(db, Product, pid).local_image_path), media_type="image/png")


# ---- templates ----------------------------------------------------------------------------------------
_SAMPLES: dict[str, bytes] = {}


@router.get("/templates", dependencies=auth)
def templates_page(request: Request, db: Session = Depends(deps.get_db)):
    rows = db.scalars(select(PinTemplate).order_by(PinTemplate.id)).all()
    return render(request, "templates.html", rows=rows)


@router.get("/templates/{key}/sample.png", dependencies=auth)
def template_sample(key: str):
    if key not in TEMPLATES:
        raise HTTPException(status_code=404, detail="Not found")
    if key not in _SAMPLES:
        from ..services.imagery import demo_image
        ctx = TemplateContext(headline="Your Everyday Skincare Routine Pick", brand="Sample Brand",
                              supporting_text="Short factual detail about the product goes here.",
                              product_name="Sample product", cta="See details", price_text="₹499",
                              disclosure="Affiliate link", images=[demo_image("s1", "Sample"), demo_image("s2", "Two"),
                                                                    demo_image("s3", "Three")],
                              list_items=["Sample product", "Second pick", "Third pick"], seed=key)
        buf = io.BytesIO()
        render_template(key, ctx).resize((500, 750)).save(buf, "PNG")
        _SAMPLES[key] = buf.getvalue()
    return Response(_SAMPLES[key], media_type="image/png")


# ---- settings / Pinterest connection -----------------------------------------------------------------------
@router.get("/settings", dependencies=auth)
def settings_page(request: Request, db: Session = Depends(deps.get_db)):
    s, st = request.app.state.settings, request.app.state
    conn = st.pinterest.get(db)
    env_checks = [
        ("PINTEREST_CLIENT_ID", bool(s.pinterest_client_id)), ("PINTEREST_CLIENT_SECRET", bool(s.pinterest_client_secret)),
        ("PINTEREST_REDIRECT_URI", bool(s.pinterest_redirect_uri)), ("AMAZON_CREDENTIAL_ID", bool(s.amazon_credential_id)),
        ("AMAZON_CREDENTIAL_SECRET", bool(s.amazon_credential_secret)), ("AMAZON_PARTNER_TAG", bool(s.amazon_partner_tag)),
        ("OPENAI_API_KEY", bool(s.openai_api_key)), ("ANTHROPIC_API_KEY", bool(s.anthropic_api_key))]
    return render(request, "settings.html", providers=[p.status() for p in st.registry.values()],
                  ai_name=st.ai.name, ai_setting=s.ai_provider, env_checks=env_checks, s=s, conn=conn,
                  boards=list(conn.boards) if conn else [], can_publish=st.pinterest.can_publish(conn),
                  pin_provider=st.pinterest.provider, pin_configured=st.pinterest.provider.is_configured(),
                  disclosure_text=app_settings.get(db, "disclosure_text"),
                  image_label=app_settings.get(db, "image_disclosure_label"))


@router.post("/pinterest/connect", dependencies=secure)
def pinterest_connect(request: Request):
    st = request.app.state
    try:
        url = st.pinterest.begin(request.session)
    except PinterestError as ex:
        flash(request, str(ex), "error")
        return back("/settings")
    return RedirectResponse(url, status_code=303)


@router.get("/pinterest/callback", dependencies=auth)
def pinterest_callback(request: Request, code: str = "", state: str = "", error: str = "",
                       db: Session = Depends(deps.get_db)):
    st = request.app.state
    if error:
        request.session.pop("pinterest_state", None)
        flash(request, "Pinterest authorization was cancelled or denied. Nothing was connected.", "warn")
        return back("/settings")
    try:
        conn = st.pinterest.complete(db, request.session, code, state)
    except PinterestError as ex:
        flash(request, f"Could not connect Pinterest: {ex}", "error")
        return back("/settings")
    flash(request, f"Pinterest connected: @{conn.username}. Choose a default board below.", "ok")
    if not st.pinterest.can_publish(conn):
        flash(request, "Connected, but publishing permission (pins:write / boards:read) was not granted. "
                       "Reconnect and allow all permissions.", "warn")
    return back("/settings")


@router.get("/pinterest/mock-authorize", dependencies=auth)
def mock_authorize_page(request: Request, state: str = ""):
    if not request.app.state.pinterest.provider.simulated:
        raise HTTPException(status_code=404, detail="Not found")
    return render(request, "mock_authorize.html", state=state)


@router.post("/pinterest/mock-authorize", dependencies=secure)
def mock_authorize_submit(request: Request, state: str = Form(""), decision: str = Form("deny")):
    if not request.app.state.pinterest.provider.simulated:
        raise HTTPException(status_code=404, detail="Not found")
    if decision == "approve":
        return back(f"/pinterest/callback?code=mock-code&state={state}")
    return back("/pinterest/callback?error=access_denied")


@router.post("/pinterest/disconnect", dependencies=secure)
def pinterest_disconnect(request: Request, db: Session = Depends(deps.get_db)):
    revoked = request.app.state.pinterest.disconnect(db)
    flash(request, "Pinterest disconnected and the authorization was revoked at Pinterest." if revoked else
          "Pinterest disconnected here and stored tokens were deleted. Pinterest did not confirm revocation: you "
          "can also remove this app in Pinterest > Settings > Authorized apps.", "ok" if revoked else "warn")
    return back("/settings")


@router.post("/pinterest/refresh-boards", dependencies=secure)
def pinterest_refresh_boards(request: Request, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    try:
        boards = request.app.state.pinterest.refresh_boards(db)
        flash(request, f"Loaded {len(boards)} boards from Pinterest.", "ok")
    except PinterestError as ex:
        flash(request, str(ex) + (" Please reconnect Pinterest." if ex.needs_reconnect else ""), "error")
    return back("/settings")


@router.post("/pinterest/default-board", dependencies=secure)
def pinterest_default_board(request: Request, board_id: str = Form(...), db: Session = Depends(deps.get_db)):
    try:
        board = request.app.state.pinterest.set_default_board(db, board_id)
        flash(request, f"Default board: {board.name}", "ok")
    except PinterestError as ex:
        flash(request, str(ex), "error")
    return back("/settings")


@router.post("/settings/disclosure", dependencies=secure)
def save_disclosure(request: Request, disclosure_text: str = Form(""), image_label: str = Form(""),
                    db: Session = Depends(deps.get_db)):
    try:
        app_settings.set_value(db, "disclosure_text", disclosure_text)
        app_settings.set_value(db, "image_disclosure_label", image_label)
    except ValueError as ex:
        flash(request, str(ex), "error")
        return back("/settings")
    flash(request, "Disclosure text saved. Re-render pins to update images; descriptions pick it up on publish.", "ok")
    return back("/settings")


# ---- analytics ----------------------------------------------------------------------------------------
@router.get("/analytics", dependencies=auth)
def analytics_page(request: Request, db: Session = Depends(deps.get_db)):
    rows = analytics_svc.latest_by_pin(db)
    return render(request, "analytics.html", rows=rows, reported=analytics_svc.pinterest_reported(db),
                  calc=analytics_svc.calculated(db), insights=analytics_svc.insights(db),
                  published=[pp for pp, _ in rows])


@router.post("/analytics/record", dependencies=secure)
def analytics_record(request: Request, published_pin_id: int = Form(...), impressions: int = Form(0),
                     saves: int = Form(0), pin_clicks: int = Form(0), outbound_clicks: int = Form(0),
                     db: Session = Depends(deps.get_db)):
    pp = get_or_404(db, PublishedPin, published_pin_id)
    analytics_svc.record_metrics(db, pp, impressions=impressions, saves=saves, pin_clicks=pin_clicks,
                                 outbound_clicks=outbound_clicks, source="manual")
    flash(request, "Metrics recorded (marked as entered by you, not reported by Pinterest).", "ok")
    return back("/analytics")


@router.post("/analytics/sync", dependencies=secure)
def analytics_sync(request: Request, db: Session = Depends(deps.get_db)):
    deps.rate_limit(request)
    out = analytics_svc.sync_from_pinterest(db, request.app.state.pinterest)
    flash(request, f"Synced {out['synced']} pins from Pinterest.", "ok" if not out["errors"] else "warn")
    for e in out["errors"][:5]:
        flash(request, e, "warn")
    return back("/analytics")


_ = (Capability, AnalyticsRecord)
