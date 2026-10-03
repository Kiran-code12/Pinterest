"""Build a pin queue from products and publish to Pinterest API v5."""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from .config import QUEUE_FILE, get
from .articles import load_articles
from .products import load_products

API = "https://api.pinterest.com/v5/pins"


def build_pin(p, site_url):
    link = f"{site_url}/{p.get('path', 'p')}/{p['slug']}.html?" + urlencode(
        {"utm_source": "pinterest", "utm_medium": "social", "utm_campaign": p["slug"]}
    )
    tags = " ".join("#" + t.replace(" ", "") for t in p.get("tags", []))
    return {
        "slug": p["slug"],
        "title": p["title"][:100],
        "description": f"{p['description']} {tags}".strip()[:500],
        "link": link,
        "image_url": p["image_url"],
    }


def load_queue():
    return json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []


def save_queue(q):
    QUEUE_FILE.write_text(json.dumps(q, indent=2))


def enqueue():
    """Add pins for products not already queued/posted. Returns number added."""
    site_url = get("SITE_URL").rstrip("/")
    q = load_queue()
    known = {i["slug"] for i in q}
    new = [dict(build_pin(p, site_url), status="pending") for p in load_articles() + load_products() if p["slug"] not in known]
    save_queue(q + new)
    return len(new)


def create_pin(pin, token, board_id):
    body = {
        "board_id": board_id,
        "title": pin["title"],
        "description": pin["description"],
        "link": pin["link"],
        "media_source": {"source_type": "image_url", "url": pin["image_url"]},
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def publish(limit=5, dry_run=True, delay=30):
    token, board = get("PINTEREST_ACCESS_TOKEN"), get("PINTEREST_BOARD_ID")
    if not dry_run and not (token and board):
        raise SystemExit("Set PINTEREST_ACCESS_TOKEN and PINTEREST_BOARD_ID in .env")
    q, done = load_queue(), 0
    for pin in q:
        if done >= limit:
            break
        if pin["status"] != "pending":
            continue
        if dry_run:
            print(f"[dry-run] would post: {pin['title']} -> {pin['link']}")
        else:
            try:
                pin["pin_id"] = create_pin(pin, token, board).get("id")
                pin["status"] = "posted"
                print(f"posted {pin['slug']} ({pin['pin_id']})")
            except urllib.error.HTTPError as ex:
                pin["status"], pin["error"] = "failed", f"{ex.code} {ex.read().decode()[:200]}"
                print(f"failed {pin['slug']}: {pin['error']}")
            save_queue(q)
            time.sleep(delay)  # pace posts; avoid spam-like bursts
        done += 1
    return done
