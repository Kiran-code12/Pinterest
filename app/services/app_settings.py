"""User-editable settings stored in the database (never secrets)."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import AppSetting

DEFAULTS = {
    "disclosure_text": "Some links may earn me a commission at no extra cost to you.",
    "image_disclosure_label": "Affiliate link",
}
LIMITS = {"disclosure_text": 160, "image_disclosure_label": 30}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def get(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row is not None else DEFAULTS[key]


def set_value(db: Session, key: str, value: str) -> str:
    if key not in DEFAULTS:
        raise KeyError(key)
    value = _CTRL.sub(" ", value or "").strip()[: LIMITS[key]]
    if not value:
        raise ValueError("The text cannot be empty. Untick the disclosure option on a pin to omit it.")
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()
    return value
