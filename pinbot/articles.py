"""Guides: markdown files in content/ with a small front-matter block, rendered to HTML (stdlib only)."""
import html
import re
from .config import CONTENT_DIR, get

REQUIRED = ("title", "hook", "description")


def parse(text):
    """Front matter is `key: value` lines between two `---` lines."""
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        raise ValueError("missing front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    meta["tags"] = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]
    meta["products"] = [t.strip() for t in meta.get("products", "").split(",") if t.strip()]
    return meta, m.group(2).strip()


def inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"\[(.+?)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="nofollow noopener">\1</a>', s)


def md_to_html(md):
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for block in re.split(r"\n\s*\n", md):
        lines = block.strip().splitlines()
        if all(l.startswith("- ") for l in lines):
            items.extend(l[2:] for l in lines)
            flush()
        elif block.startswith("## "):
            out.append(f"<h2>{inline(block[3:].strip())}</h2>")
        elif block.strip():
            out.append(f"<p>{inline(' '.join(lines))}</p>")
    return "\n".join(out)


def load_articles(directory=CONTENT_DIR):
    arts, seen = [], set()
    for f in sorted(directory.glob("*.md")):
        meta, body = parse(f.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if not meta.get(k)]
        if missing:
            raise ValueError(f"{f.name} missing {missing}")
        slug = meta.get("slug") or f.stem
        if slug in seen:
            raise ValueError(f"duplicate slug {slug}")
        seen.add(slug)
        meta.update(slug=slug, body=body, path="a", category=meta.get("category", "Skincare"))
        meta["image_url"] = meta.get("image_url") or f"{get('SITE_URL').rstrip('/')}/pins/{slug}.png"
        arts.append(meta)
    return arts
