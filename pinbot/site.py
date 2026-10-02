"""Static site generator: index + one page per product, with affiliate disclosure."""
import html
import shutil
from .config import SITE_DIR, get
from .products import load_products

CSS = """body{font-family:system-ui,sans-serif;margin:0;color:#222;background:#fafafa}
header,main,footer{max-width:960px;margin:auto;padding:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}
.card{background:#fff;border-radius:12px;padding:12px;box-shadow:0 1px 4px #0002}
.card img{width:100%;border-radius:8px}a{color:#e60023}
.btn{display:inline-block;background:#e60023;color:#fff;padding:10px 18px;border-radius:24px;text-decoration:none}
.note{font-size:.8rem;color:#666}"""

DISCLOSURE = "As an affiliate, we may earn a commission from qualifying purchases at no extra cost to you."


def page(title, body, name, desc=""):
    e = html.escape
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} | {e(name)}</title><meta name="description" content="{e(desc)}">
<link rel="stylesheet" href="{{ROOT}}style.css"></head><body>
<header><a href="{{ROOT}}index.html"><h1>{e(name)}</h1></a></header>
<main>{body}</main><footer class="note">{e(DISCLOSURE)}</footer></body></html>"""


def build(out=SITE_DIR):
    name = get("SITE_NAME", "My Picks")
    products = load_products()
    if out.exists():
        shutil.rmtree(out)
    (out / "p").mkdir(parents=True)
    (out / "style.css").write_text(CSS)
    e = html.escape

    cards = "".join(
        f'<div class="card"><a href="p/{e(p["slug"])}.html"><img src="{e(p["image_url"])}" alt="{e(p["title"])}" loading="lazy">'
        f'<h3>{e(p["title"])}</h3></a><p class="note">{e(p.get("price", ""))}</p></div>'
        for p in products
    )
    (out / "index.html").write_text(
        page("Home", f'<div class="grid">{cards}</div>', name, "Curated product picks").replace("{ROOT}", "")
    )
    for p in products:
        body = (
            f'<img src="{e(p["image_url"])}" alt="{e(p["title"])}" style="max-width:100%;border-radius:12px">'
            f'<h2>{e(p["title"])}</h2><p>{e(p["description"])}</p><p><b>{e(p.get("price", ""))}</b></p>'
            f'<p><a class="btn" rel="sponsored nofollow noopener" target="_blank" href="{e(p["affiliate_url"])}">View product</a></p>'
            f'<p class="note">{e(DISCLOSURE)}</p>'
        )
        (out / "p" / f'{p["slug"]}.html').write_text(
            page(p["title"], body, name, p["description"]).replace("{ROOT}", "../")
        )
    site_url = get("SITE_URL").rstrip("/")
    items = "".join(
        f'<item><title>{e(p["title"])}</title><link>{e(site_url)}/p/{e(p["slug"])}.html</link>'
        f'<guid>{e(site_url)}/p/{e(p["slug"])}.html</guid><description>{e(p["description"])}</description>'
        f'<enclosure url="{e(p["image_url"])}" type="image/jpeg" length="0"/></item>'
        for p in products
    )
    (out / "feed.xml").write_text(
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>{e(name)}</title>'
        f'<link>{e(site_url)}</link><description>Curated picks</description>{items}</channel></rss>'
    )
    return len(products)
