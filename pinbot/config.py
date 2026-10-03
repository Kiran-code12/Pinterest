import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "data" / "products.json"
CONTENT_DIR = ROOT / "content"
IMAGES_DIR = ROOT / "data" / "images"
QUEUE_FILE = ROOT / "data" / "queue.json"
SITE_DIR = ROOT / "docs"  # GitHub Pages can serve /docs


def load_env(path=ROOT / ".env"):
    """Minimal .env loader (no dependency); real env vars win."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get(name, default=""):
    load_env()
    return os.environ.get(name, default)
