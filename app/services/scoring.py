"""Internal content-opportunity score (0-100).

This is a transparent heuristic about how well a product suits Pinterest *content*. It is NOT a prediction of
clicks, conversions or sales, and the UI says so.
"""
from __future__ import annotations

from datetime import datetime

SCORE_LABEL = "Content-opportunity score (internal heuristic, not a sales prediction)"

VISUAL_CATEGORIES = {"beauty": 20, "skincare": 20, "makeup": 20, "fashion": 19, "home": 18, "lifestyle": 17,
                     "electronics": 9}
SEASONAL = {  # month -> keywords that fit the season (India-centric, deliberately simple)
    10: {"festive", "diwali", "gift", "candle", "decor", "earrings", "lipstick"},
    11: {"festive", "diwali", "gift", "candle", "decor", "moisturizer", "lip"},
    12: {"winter", "moisturizer", "lip", "gift", "candle", "wedding"},
    1: {"winter", "moisturizer", "lip", "journal", "organizer"},
    2: {"gift", "lipstick", "blush", "valentine"},
    3: {"holi", "sunscreen", "spf", "summer"},
    4: {"sunscreen", "spf", "summer", "gel", "bottle"},
    5: {"sunscreen", "spf", "summer", "gel", "bottle"},
    6: {"monsoon", "organizer", "hair"},
    7: {"monsoon", "organizer", "hair", "journal"},
    8: {"festive", "gift", "earrings", "rakhi"},
    9: {"festive", "gift", "decor", "candle"},
}


def score_product(*, title: str, brand: str | None, category: str | None, description: str | None,
                  price: float | None, has_image: bool, link_state: str, is_duplicate: bool,
                  query: str | None = None, now: datetime | None = None) -> tuple[float, dict]:
    """link_state: 'valid' | 'invalid' | 'missing'."""
    now = now or datetime.utcnow()
    text = f"{title} {description or ''}".lower()
    cat = (category or "").lower()

    suit = float(VISUAL_CATEGORIES.get(cat, 12))
    visual = 15.0 if has_image else 0.0
    if query:
        qwords = [w for w in query.lower().split() if len(w) > 2]
        hits = sum(1 for w in qwords if w in text or w in cat)
        relevance = 15.0 * (hits / len(qwords)) if qwords else 10.0
    else:
        relevance = 10.0
    if price is None:
        price_pts = 5.0
    elif price <= 500:
        price_pts = 10.0
    elif price <= 1500:
        price_pts = 8.0
    elif price <= 3000:
        price_pts = 5.0
    else:
        price_pts = 3.0
    content = (6.0 if len((description or "").strip()) >= 40 else 0.0) + (4.0 if brand else 0.0) + \
              (5.0 if len(title.split()) >= 3 else 2.0)
    season_words = SEASONAL.get(now.month, set())
    seasonal = 10.0 if any(w in text for w in season_words) else 3.0
    affiliate = {"valid": 15.0, "invalid": 5.0, "missing": 0.0}[link_state]
    penalty = -20.0 if is_duplicate else 0.0

    breakdown = {
        "pinterest_suitability": round(suit, 1), "visual_appeal": round(visual, 1),
        "category_relevance": round(relevance, 1), "price_attractiveness": round(price_pts, 1),
        "content_potential": round(content, 1), "seasonal_relevance": round(seasonal, 1),
        "affiliate_availability": round(affiliate, 1), "duplicate_penalty": round(penalty, 1),
    }
    total = max(0.0, min(100.0, sum(breakdown.values())))
    return round(total, 1), breakdown
