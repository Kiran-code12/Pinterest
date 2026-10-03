"""Download product photos. Each product may set "image_source": a direct image URL or a product page URL
(for a page we use its og:image / twitter:image tag, the same one link previews use)."""
import io
import json
import re
import urllib.request
from urllib.parse import urljoin
from PIL import Image
from .config import IMAGES_DIR, PRODUCTS_FILE

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MAX_BYTES = 10 * 1024 * 1024
META = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\'][^>]*>', re.I)
CONTENT = re.compile(r'content=["\']([^"\']+)["\']', re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,image/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.headers.get_content_type(), r.read(MAX_BYTES + 1)


def find_image_url(page_url, html):
    for tag in META.findall(html):
        m = CONTENT.search(tag)
        if m:
            return urljoin(page_url, m.group(1).replace("&amp;", "&"))
    return None


def download(source):
    """Return image bytes for a direct image URL or a page URL."""
    ctype, data = get(source)
    if not ctype.startswith("image/"):
        img_url = find_image_url(source, data.decode("utf-8", "replace"))
        if not img_url:
            raise ValueError("no og:image found on page")
        ctype, data = get(img_url)
        if not ctype.startswith("image/"):
            raise ValueError(f"{img_url} is not an image")
    if len(data) > MAX_BYTES:
        raise ValueError("image larger than 10MB")
    return data


def fetch_all(products_file=PRODUCTS_FILE, images_dir=IMAGES_DIR, force=False):
    """Fill in photos for products with an image_source. Returns (ok, failed) lists of slugs."""
    products = json.loads(products_file.read_text())
    images_dir.mkdir(parents=True, exist_ok=True)
    ok, failed = [], []
    for p in products:
        src = p.get("image_source")
        if not src or (p.get("photo") and (images_dir / p["photo"]).exists() and not force):
            continue
        try:
            img = Image.open(io.BytesIO(download(src)))
            img.load()
            name = f"{p['slug']}.png" if img.mode in ("RGBA", "LA", "P") else f"{p['slug']}.jpg"
            img.save(images_dir / name)
            p["photo"] = name
            ok.append(p["slug"])
        except Exception as ex:  # keep going; report at the end
            print(f"  {p['slug']}: {ex}")
            failed.append(p["slug"])
    products_file.write_text(json.dumps(products, indent=2, ensure_ascii=False) + "\n")
    return ok, failed
