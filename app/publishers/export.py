"""Export workflow (always available, no credentials): a ZIP with the image and copy-paste text."""
from __future__ import annotations

import io
import json
import zipfile

from ..models import Pin
from ..services.content import with_disclosure


def pin_payload(pin: Pin, disclosure_text: str = "") -> dict:
    return {"title": pin.seo_title, "description": with_disclosure(pin.seo_description, pin.show_disclosure, disclosure_text),
            "link": pin.destination_url, "alt_text": f"{pin.headline} - {pin.product.title}"[:500],
            "keywords": pin.keywords or [], "headline": pin.headline, "product": pin.product.title,
            "provider": pin.product.provider.name if pin.product.provider else None}


def build_export_zip(pin: Pin, disclosure_text: str = "") -> bytes:
    asset = pin.current_asset
    if asset is None:
        raise ValueError("Pin has no image")
    payload = pin_payload(pin, disclosure_text)
    text = (f"TITLE\n{payload['title']}\n\nDESCRIPTION\n{payload['description']}\n\n"
            f"DESTINATION LINK (affiliate)\n{payload['link']}\n\nALT TEXT\n{payload['alt_text']}\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        with open(asset.path, "rb") as f:
            z.writestr(f"pin_{pin.id}.png", f.read())
        z.writestr(f"pin_{pin.id}.json", json.dumps(payload, indent=2, ensure_ascii=False))
        z.writestr(f"pin_{pin.id}.txt", text)
    return buf.getvalue()
