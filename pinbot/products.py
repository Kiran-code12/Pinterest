import json
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from .config import PRODUCTS_FILE, get

REQUIRED = ("slug", "title", "description", "affiliate_url")


def load_products(path=PRODUCTS_FILE):
    products = json.loads(path.read_text())
    seen = set()
    for p in products:
        missing = [k for k in REQUIRED if not p.get(k)]
        if missing:
            raise ValueError(f"product {p.get('slug', '?')} missing {missing}")
        if p["slug"] in seen:
            raise ValueError(f"duplicate slug {p['slug']}")
        seen.add(p["slug"])
        p.setdefault("hook", p["title"])
        # Pin image is generated unless a custom image_url is supplied.
        p["image_url"] = p.get("image_url") or f"{get('SITE_URL').rstrip('/')}/pins/{p['slug']}.png"
        p["affiliate_url"] = tag_amazon(p["affiliate_url"])
    return products


def tag_amazon(url):
    """Add your Amazon Associates tag (AMAZON_TAG) to amazon.* links."""
    tag = get("AMAZON_TAG")
    u = urlparse(url)
    if not tag or "amazon." not in u.netloc:
        return url
    q = dict(parse_qsl(u.query))
    q["tag"] = tag
    return urlunparse(u._replace(query=urlencode(q)))
