"""Product image acquisition: SSRF-safe download of provider-supplied image URLs, or local demo art."""
from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageOps

from ..config import Settings
from ..models import Product
from ..security import UnsafeURL, validate_fetch_url

log = logging.getLogger("engine.imagery")
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
PALETTES = [((233, 150, 170), (250, 205, 215)), ((214, 168, 120), (245, 220, 190)),
            ((140, 190, 160), (205, 230, 212)), ((150, 160, 215), (210, 215, 245))]


def demo_image(slug: str, label: str) -> Image.Image:
    """A simple drawn bottle/jar so demo pins look like pins. Fictional, offline."""
    idx = int(hashlib.sha1(slug.encode()).hexdigest(), 16) % len(PALETTES)
    dark, light = PALETTES[idx]
    img = Image.new("RGB", (800, 800), (252, 249, 246))
    d = ImageDraw.Draw(img)
    d.ellipse([140, 560, 660, 720], fill=(235, 228, 222))
    d.rounded_rectangle([290, 200, 510, 640], radius=60, fill=dark)
    d.rounded_rectangle([320, 230, 480, 600], radius=40, fill=light)
    d.rounded_rectangle([340, 110, 460, 215], radius=22, fill=(70, 55, 60))
    from .templates import load_font
    f = load_font(34, bold=True)
    d.text((400, 430), (label or "")[:14].upper(), font=f, fill=(70, 55, 60), anchor="mm")
    return img


def _download(url: str, settings: Settings, http: httpx.Client | None) -> bytes:
    client = http or httpx.Client(timeout=15)
    current = url
    for _ in range(4):  # follow a few redirects manually so every hop is validated
        validate_fetch_url(current, allow_private=settings.allow_private_urls)
        with client.stream("GET", current, follow_redirects=False,
                           headers={"Accept": "image/*", "User-Agent": "AffiliateEngine/0.1"}) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = str(httpx.URL(current).join(r.headers["location"]))
                continue
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                raise ValueError("URL did not return an image")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise ValueError("Image is larger than 8MB")
            return bytes(buf)
    raise ValueError("Too many redirects")


def ensure_product_image(product: Product, settings: Settings, http: httpx.Client | None = None) -> Path | None:
    """Return a local image path for the product (downloading once), or None if unavailable."""
    if product.local_image_path and Path(product.local_image_path).exists():
        return Path(product.local_image_path)
    folder = settings.assets_dir / "products"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{product.id}.png"
    try:
        url = product.image_url or ""
        if url.startswith("demo://"):
            demo_image(url[7:], product.brand or product.title).save(target)
        elif url:
            data = _download(url, settings, http)
            img = Image.open(io.BytesIO(data))
            if img.width * img.height > MAX_PIXELS:
                raise ValueError("Image dimensions too large")
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            img.save(target)
        else:
            return None
    except (UnsafeURL, ValueError, OSError, httpx.HTTPError) as ex:
        log.warning("image unavailable for product %s: %s", product.id, type(ex).__name__)
        return None
    product.local_image_path = str(target)
    return target
