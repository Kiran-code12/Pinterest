"""Pin design system: modular, template-based rendering with Pillow (free, offline, deterministic).

Add a template: write `def render_x(ctx) -> Image` and decorate it with @register("key", "Name", "description").
Canvas is Pinterest's recommended 2:3 vertical format (1000x1500).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1000, 1500
STATIC_FONTS = Path(__file__).resolve().parent.parent / "static" / "fonts"
SANS_BOLD = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf",
             "NotoSans-Bold.ttf", "Helvetica.ttc"]
SANS = ["DejaVuSans.ttf", "arial.ttf", "segoeui.ttf", "Arial.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf"]
SERIF_BOLD = ["DejaVuSerif-Bold.ttf", "georgiab.ttf", "timesbd.ttf", "LiberationSerif-Bold.ttf", "NotoSerif-Bold.ttf"]
FONT_DIRS = [STATIC_FONTS, Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/liberation"),
             Path("/usr/share/fonts/truetype/noto"), Path("C:/Windows/Fonts"), Path("/Library/Fonts"),
             Path("/System/Library/Fonts"), Path("/usr/share/fonts/dejavu")]

PALETTES = [  # bg1, bg2, ink, accent, soft
    ((255, 238, 233), (250, 205, 205), (74, 40, 55), (196, 82, 111), (255, 250, 247)),
    ((255, 244, 228), (245, 214, 178), (80, 52, 40), (190, 110, 60), (255, 251, 244)),
    ((233, 243, 235), (190, 218, 200), (36, 62, 52), (70, 128, 100), (248, 253, 249)),
    ((236, 238, 252), (200, 205, 238), (44, 48, 90), (98, 104, 190), (250, 250, 255)),
    ((250, 236, 244), (232, 196, 220), (80, 36, 70), (170, 70, 150), (255, 249, 253)),
]


@lru_cache(maxsize=64)
def load_font(size: int, bold: bool = True, serif: bool = False) -> ImageFont.FreeTypeFont:
    names = SERIF_BOLD if serif else (SANS_BOLD if bold else SANS)
    for d in FONT_DIRS:
        for n in names:
            p = d / n
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
    return ImageFont.load_default(size)


@dataclass
class TemplateContext:
    headline: str
    supporting_text: str = ""
    brand: str = ""
    product_name: str = ""
    cta: str = "Shop now"
    price_text: str | None = None
    disclosure: str | None = None
    images: list[Image.Image | None] = field(default_factory=list)  # primary first
    list_items: list[str] = field(default_factory=list)
    seed: str = ""

    @property
    def palette(self):
        return PALETTES[int(hashlib.sha1(self.seed.encode()).hexdigest(), 16) % len(PALETTES)]


# ---- drawing helpers ----------------------------------------------------------------------------------
def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def fit_text(draw, text: str, width: int, max_lines: int, start: int, min_size: int, *, bold=True, serif=False):
    """Largest font (>= min_size) whose wrapped text fits max_lines; ellipsizes as a last resort."""
    size = start
    while True:
        font = load_font(size, bold, serif)
        lines = wrap_lines(draw, text, font, width)
        if len(lines) <= max_lines or size <= min_size:
            break
        size -= 4
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return font, lines


def draw_lines(draw, lines, font, x, y, fill, *, anchor="ma", gap=1.18) -> int:
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill, anchor=anchor)
        y += int(font.size * gap)
    return y


def shadowed_card(canvas: Image.Image, box, radius=44, fill=(255, 255, 255)):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([box[0], box[1] + 14, box[2], box[3] + 14], radius, fill=(60, 40, 50, 70))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(22))).convert("RGB"))
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, fill=fill)


def placeholder(size: tuple[int, int], label: str, color) -> Image.Image:
    img = Image.new("RGB", size, tuple(min(255, c + 40) for c in color))
    d = ImageDraw.Draw(img)
    letters = "".join(w[0] for w in (label or "?").split()[:2]).upper() or "?"
    d.text((size[0] // 2, size[1] // 2), letters, font=load_font(max(40, size[1] // 4)), fill=color, anchor="mm")
    return img


def paste_fit(canvas: Image.Image, img: Image.Image | None, box, *, cover=False, radius=0, label="", color=(150, 120, 130)):
    x0, y0, x1, y1 = [int(v) for v in box]
    size = (x1 - x0, y1 - y0)
    if img is None:
        img = placeholder(size, label, color)
    img = img.convert("RGBA")
    fitted = ImageOps.fit(img, size, Image.LANCZOS) if cover else ImageOps.contain(img, size, Image.LANCZOS)
    mask = Image.new("L", fitted.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, fitted.width, fitted.height], radius, fill=255)
    if "A" in img.getbands():
        mask = Image.composite(mask, Image.new("L", fitted.size, 0), fitted.getchannel("A"))
    canvas.paste(fitted.convert("RGB"), (x0 + (size[0] - fitted.width) // 2, y0 + (size[1] - fitted.height) // 2), mask)


def cta_pill(draw, text: str, cx: int, cy: int, fill, ink=(255, 255, 255), size=36):
    font = load_font(size)
    w = int(draw.textlength(text, font=font)) + 90
    draw.rounded_rectangle([cx - w // 2, cy - 42, cx + w // 2, cy + 42], 42, fill=fill)
    draw.text((cx, cy), text, font=font, fill=ink, anchor="mm")


def footer(draw, ctx: TemplateContext, ink, y: int = H - 48):
    if ctx.disclosure:
        draw.text((W // 2, y), ctx.disclosure, font=load_font(24, bold=False), fill=ink, anchor="mm")


# ---- template registry --------------------------------------------------------------------------------
@dataclass(frozen=True)
class TemplateSpec:
    key: str
    name: str
    description: str
    render: Callable[[TemplateContext], Image.Image]


TEMPLATES: dict[str, TemplateSpec] = {}


def register(key: str, name: str, description: str):
    def deco(fn):
        TEMPLATES[key] = TemplateSpec(key, name, description, fn)
        return fn
    return deco


def render_template(key: str, ctx: TemplateContext) -> Image.Image:
    spec = TEMPLATES.get(key) or TEMPLATES["minimal_card"]
    return spec.render(ctx)


def img_at(ctx: TemplateContext, i: int):
    return ctx.images[i] if i < len(ctx.images) else None


@register("minimal_card", "Minimal product card", "Clean white card with the product photo, headline and button.")
def minimal_card(ctx: TemplateContext) -> Image.Image:
    bg1, bg2, ink, accent, soft = ctx.palette
    canvas = Image.new("RGB", (W, H), (250, 248, 245))
    d = ImageDraw.Draw(canvas)
    if ctx.brand:
        d.text((W // 2, 92), ctx.brand.upper()[:32], font=load_font(30), fill=accent, anchor="mm")
    shadowed_card(canvas, (80, 150, 920, 900))
    paste_fit(canvas, img_at(ctx, 0), (130, 195, 870, 855), radius=24, label=ctx.brand or ctx.product_name, color=accent)
    d = ImageDraw.Draw(canvas)
    font, lines = fit_text(d, ctx.headline, 820, 3, 64, 40, serif=True)
    y = draw_lines(d, lines, font, W // 2, 950, ink)
    if ctx.supporting_text:
        f2, l2 = fit_text(d, ctx.supporting_text, 760, 2, 30, 24, bold=False)
        y = draw_lines(d, l2, f2, W // 2, y + 12, (110, 100, 105))
    if ctx.price_text:
        d.text((W // 2, y + 28), ctx.price_text, font=load_font(40), fill=ink, anchor="mm")
    cta_pill(d, ctx.cta, W // 2, 1360, accent)
    footer(d, ctx, (150, 140, 145))
    return canvas


@register("beauty_editorial", "Beauty editorial", "Soft gradient, large serif headline and an arched product frame.")
def beauty_editorial(ctx: TemplateContext) -> Image.Image:
    bg1, bg2, ink, accent, soft = ctx.palette
    canvas = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(canvas)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(bg1[i] + (bg2[i] - bg1[i]) * t) for i in range(3)))
    d.rectangle([40, 40, W - 40, H - 40], outline=(255, 255, 255), width=4)
    d.text((90, 105), (ctx.brand or ctx.product_name).upper()[:34], font=load_font(28), fill=accent)
    font, lines = fit_text(d, ctx.headline, 820, 4, 92, 52, serif=True)
    y = draw_lines(d, lines, font, 90, 160, ink, anchor="la", gap=1.12)
    top = max(y + 30, 560)
    box = (190, top, 810, top + 680)
    arch = Image.new("L", canvas.size, 0)
    ad = ImageDraw.Draw(arch)
    radius = (box[2] - box[0]) // 2
    ad.pieslice([box[0], box[1], box[2], box[1] + 2 * radius], 180, 360, fill=255)
    ad.rectangle([box[0], box[1] + radius, box[2], box[3] - 30], fill=255)
    ad.rounded_rectangle([box[0], box[3] - 80, box[2], box[3]], 30, fill=255)
    canvas.paste(Image.new("RGB", canvas.size, soft), (0, 0), arch)
    paste_fit(canvas, img_at(ctx, 0), (box[0] + 40, box[1] + 170, box[2] - 40, box[3] - 40), radius=16,
              label=ctx.brand or ctx.product_name, color=accent)
    d = ImageDraw.Draw(canvas)
    if ctx.supporting_text:
        f2, l2 = fit_text(d, ctx.supporting_text, 540, 2, 28, 22, bold=False)
        draw_lines(d, l2, f2, 90, box[3] + 36, ink, anchor="la")
    cta_pill(d, ctx.cta, 790, box[3] + 60, accent, size=30)
    if ctx.price_text:
        d.text((90, box[3] + 120), ctx.price_text, font=load_font(36), fill=ink)
    footer(d, ctx, ink, y=H - 78)
    return canvas


@register("collage", "Product collage", "Up to three product photos in a tiled layout under a headline.")
def collage(ctx: TemplateContext) -> Image.Image:
    bg1, bg2, ink, accent, soft = ctx.palette
    canvas = Image.new("RGB", (W, H), bg1)
    d = ImageDraw.Draw(canvas)
    font, lines = fit_text(d, ctx.headline, 840, 3, 76, 46, serif=True)
    y = draw_lines(d, lines, font, W // 2, 90, ink)
    n = max(1, min(3, len(ctx.images)))
    top, bottom = max(y + 30, 400), 1250
    if n == 1:
        boxes = [(100, top, 900, bottom)]
    elif n == 2:
        boxes = [(100, top, 490, bottom), (510, top, 900, bottom)]
    else:
        mid = (top + bottom) // 2
        boxes = [(100, top, 540, bottom), (560, top, 900, mid - 10), (560, mid + 10, 900, bottom)]
    labels = ctx.list_items or [ctx.product_name]
    for i, box in enumerate(boxes):
        shadowed_card(canvas, box, radius=32, fill=soft)
        paste_fit(canvas, img_at(ctx, i), (box[0] + 20, box[1] + 20, box[2] - 20, box[3] - 70), radius=18,
                  label=labels[i] if i < len(labels) else ctx.brand, color=accent)
        d = ImageDraw.Draw(canvas)
        if i < len(labels):
            f, ls = fit_text(d, labels[i], box[2] - box[0] - 40, 1, 26, 18)
            draw_lines(d, ls, f, (box[0] + box[2]) // 2, box[3] - 52, ink)
    d = ImageDraw.Draw(canvas)
    if ctx.supporting_text:
        f2, l2 = fit_text(d, ctx.supporting_text, 820, 2, 28, 22, bold=False)
        draw_lines(d, l2, f2, W // 2, 1262, ink)
    cta_pill(d, ctx.cta, W // 2, 1388, accent, size=32)
    footer(d, ctx, ink)
    return canvas


@register("top_picks", "Top picks / listicle", "Numbered list of picks with thumbnails (names and verified details only).")
def top_picks(ctx: TemplateContext) -> Image.Image:
    bg1, bg2, ink, accent, soft = ctx.palette
    canvas = Image.new("RGB", (W, H), soft)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, W, 330], fill=bg2)
    d.text((W // 2, 80), "TOP PICKS", font=load_font(32), fill=accent, anchor="mm")
    font, lines = fit_text(d, ctx.headline, 840, 3, 70, 44, serif=True)
    draw_lines(d, lines, font, W // 2, 125, ink)
    items = (ctx.list_items or [ctx.product_name])[:5]
    row_h = min(230, (1230 - 380) // max(1, len(items)))
    y = 380
    for i, text in enumerate(items):
        d.rounded_rectangle([70, y, 930, y + row_h - 20], 28, fill=(255, 255, 255))
        d.ellipse([95, y + row_h // 2 - 38, 171, y + row_h // 2 + 38 - 20], fill=accent)
        d.text((133, y + row_h // 2 - 10), str(i + 1), font=load_font(38), fill=(255, 255, 255), anchor="mm")
        tx = 205
        if i < len(ctx.images) and ctx.images[i] is not None:
            paste_fit(canvas, ctx.images[i], (190, y + 12, 190 + row_h - 44, y + row_h - 32), radius=14, color=accent)
            tx = 190 + row_h - 20
            d = ImageDraw.Draw(canvas)
        f, ls = fit_text(d, text, 900 - tx, 2, 38, 24, bold=False)
        draw_lines(d, ls, f, tx, y + row_h // 2 - int(f.size * 0.7) * len(ls) // 1 + 20, ink, anchor="la")
        y += row_h
    if ctx.supporting_text:
        f2, l2 = fit_text(d, ctx.supporting_text, 820, 2, 28, 22, bold=False)
        draw_lines(d, l2, f2, W // 2, min(max(y + 20, 1000), 1262), (110, 100, 105))
    cta_pill(d, ctx.cta, W // 2, 1388, accent, size=32)
    footer(d, ctx, (150, 140, 145))
    return canvas


@register("spotlight", "Product spotlight", "Bold dark layout that puts one product in the spotlight.")
def spotlight(ctx: TemplateContext) -> Image.Image:
    bg1, bg2, ink, accent, soft = ctx.palette
    canvas = Image.new("RGB", (W, H), ink)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 70, W, 150], fill=accent)
    d.text((W // 2, 110), "PRODUCT SPOTLIGHT", font=load_font(34), fill=(255, 255, 255), anchor="mm")
    shadowed_card(canvas, (110, 220, 890, 930), radius=48, fill=(255, 255, 255))
    paste_fit(canvas, img_at(ctx, 0), (160, 265, 840, 885), radius=24, label=ctx.brand or ctx.product_name, color=accent)
    d = ImageDraw.Draw(canvas)
    if ctx.price_text:
        d.ellipse([740, 190, 900, 350], fill=accent)
        d.text((820, 270), ctx.price_text, font=load_font(40), fill=(255, 255, 255), anchor="mm")
    if ctx.brand:
        d.text((W // 2, 990), ctx.brand.upper()[:32], font=load_font(28), fill=bg2, anchor="mm")
    font, lines = fit_text(d, ctx.headline, 820, 3, 62, 40, serif=True)
    y = draw_lines(d, lines, font, W // 2, 1030, (255, 255, 255))
    if ctx.supporting_text:
        f2, l2 = fit_text(d, ctx.supporting_text, 780, 2, 30, 24, bold=False)
        draw_lines(d, l2, f2, W // 2, y + 10, bg2)
    cta_pill(d, ctx.cta, W // 2, 1380, accent, size=34)
    footer(d, ctx, (190, 180, 190))
    return canvas


def seed_templates(db) -> None:
    from sqlalchemy import select

    from ..models import PinTemplate
    existing = {t.key: t for t in db.scalars(select(PinTemplate))}
    for spec in TEMPLATES.values():
        row = existing.get(spec.key)
        if row is None:
            db.add(PinTemplate(key=spec.key, name=spec.name, description=spec.description, width=W, height=H))
        else:
            row.name, row.description = spec.name, spec.description
    db.commit()
