"""Static site generator: index, guides and product pages, with affiliate disclosure."""
import html
import shutil
from .articles import load_articles, md_to_html
from .config import IMAGES_DIR, SITE_DIR, get
from .images import make_pin
from .products import load_products

CSS = """body{font-family:system-ui,sans-serif;margin:0;color:#222;background:#fafafa;line-height:1.6}
header,main,footer{max-width:960px;margin:auto;padding:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}
.card{background:#fff;border-radius:12px;padding:12px;box-shadow:0 1px 4px #0002}
.card img{width:100%;border-radius:8px}a{color:#e60023}
article{max-width:680px}.btn{display:inline-block;background:#e60023;color:#fff;padding:10px 18px;border-radius:24px;text-decoration:none}
.pick{background:#fff;border-radius:12px;padding:12px 16px;margin:12px 0;box-shadow:0 1px 4px #0002}
.note{font-size:.8rem;color:#666}
header a{color:#4a2837;text-decoration:none}.hero{max-height:320px;width:auto;max-width:100%;border-radius:12px;display:block}
.card h3{font-size:1rem;margin:.6em 0 .2em}.card a{text-decoration:none}"""

DISCLOSURE = "As an affiliate, we may earn a commission from qualifying purchases at no extra cost to you."
e = html.escape


def page(title, body, name, root, desc=""):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} | {e(name)}</title><meta name="description" content="{e(desc)}">
<link rel="stylesheet" href="{root}style.css"></head><body>
<header><a href="{root}index.html"><h1>{e(name)}</h1></a></header>
<main>{body}</main><footer class="note">{e(DISCLOSURE)}</footer></body></html>"""


def local_img(item):
    """Site pages use the relative generated image; remote image_url (custom) is kept as is."""
    return item["image_url"]


def rel(item, prefix):
    u = local_img(item)
    return e(u if u.startswith("http") else prefix + u)


def abs_img(item, site_url):
    u = item["image_url"]
    return u if u.startswith("http") else f"{site_url}/{u}"


def card(item, prefix=""):
    return (
        f'<div class="card"><a href="{prefix}{item["path"]}/{e(item["slug"])}.html">'
        f'<img src="{rel(item, prefix)}" alt="{e(item["title"])}" loading="lazy"><h3>{e(item["title"])}</h3></a></div>'
    )


def pick(p):
    return (
        f'<div class="pick"><strong>{e(p["title"])}</strong><br>{e(p["description"])}<br>'
        f'<a class="btn" rel="sponsored nofollow noopener" target="_blank" href="{e(p["affiliate_url"])}">View product</a></div>'
    )


def build(out=SITE_DIR, images_dir=IMAGES_DIR):
    name = get("SITE_NAME", "My Picks")
    products, articles = load_products(), load_articles()
    by_slug = {p["slug"]: p for p in products}
    for a in articles:
        unknown = [s for s in a["products"] if s not in by_slug]
        if unknown:
            raise ValueError(f"guide {a['slug']} references unknown products {unknown}")
    if out.exists():
        shutil.rmtree(out)
    (out / "p").mkdir(parents=True)
    (out / "a").mkdir()
    (out / "style.css").write_text(CSS)
    site_url = get("SITE_URL").rstrip("/")

    photo_of = {}
    for p in products:
        if p.get("photo"):
            f = images_dir / p["photo"]
            if not f.exists():
                raise ValueError(f"product {p['slug']}: photo {f} not found")
            photo_of[p["slug"]] = f

    def photos_for(it):
        if it["path"] == "p":
            return [photo_of[it["slug"]]] if it["slug"] in photo_of else []
        return [photo_of[s] for s in it["products"] if s in photo_of][:3]

    items = articles + products
    for i, it in enumerate(items):
        if it["image_url"].endswith(f"/pins/{it['slug']}.png"):
            make_pin(it, out / "pins" / f"{it['slug']}.png", i, photos_for(it))
            it["image_url"] = f"pins/{it['slug']}.png"  # relative for site pages; pins.py rebuilds the absolute URL

    home = (
        f'<h2>Guides</h2><div class="grid">{"".join(card(a) for a in articles)}</div>'
        f'<h2>Our picks</h2><div class="grid">{"".join(card(p) for p in products)}</div>'
    )
    (out / "index.html").write_text(page("Home", home, name, "", "Skincare guides and picks"))

    for a in articles:
        picks = "".join(pick(by_slug[s]) for s in a["products"])
        body = (
            f'<article><img src="{rel(a, "../")}" alt="{e(a["title"])}" class="hero">'
            f'<h1>{e(a["title"])}</h1>{md_to_html(a["body"])}'
            + (f"<h2>Products mentioned</h2>{picks}" if picks else "")
            + '<p class="note">This is general information, not medical advice.</p></article>'
        )
        (out / "a" / f'{a["slug"]}.html').write_text(page(a["title"], body, name, "../", a["description"]))

    for p in products:
        body = (
            f'<img src="{rel(p, "../")}" alt="{e(p["title"])}" class="hero">'
            f'<h2>{e(p["title"])}</h2><p>{e(p["description"])}</p>'
            f'<p><a class="btn" rel="sponsored nofollow noopener" target="_blank" href="{e(p["affiliate_url"])}">View product</a></p>'
        )
        (out / "p" / f'{p["slug"]}.html').write_text(page(p["title"], body, name, "../", p["description"]))

    feed = "".join(
        f'<item><title>{e(it["title"])}</title><link>{e(site_url)}/{it["path"]}/{e(it["slug"])}.html</link>'
        f'<guid>{e(site_url)}/{it["path"]}/{e(it["slug"])}.html</guid><description>{e(it["description"])}</description>'
        f'<enclosure url="{e(abs_img(it, site_url))}" type="image/png" length="0"/></item>'
        for it in items
    )
    (out / "feed.xml").write_text(
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>{e(name)}</title>'
        f'<link>{e(site_url)}</link><description>Skincare guides and picks</description>{feed}</channel></rss>'
    )
    return len(items)
