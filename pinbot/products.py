import json
from .config import PRODUCTS_FILE

REQUIRED = ("slug", "title", "description", "image_url", "affiliate_url")


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
    return products
