"""LocalProvider: deterministic, free, offline copy. Uses only product facts, so it cannot hallucinate."""
from __future__ import annotations

import re

from .base import AIProvider

CTAS = ["See details", "Take a look", "View this pick", "Check it out", "Learn more"]
CATEGORY_KEYWORDS = {
    "beauty": ["beauty finds", "beauty routine", "self care"],
    "skincare": ["skincare routine", "skincare tips", "glowing skin"],
    "makeup": ["makeup ideas", "everyday makeup", "makeup essentials"],
    "fashion": ["style ideas", "outfit accessories", "fashion finds"],
    "home": ["home decor ideas", "home finds", "cozy home"],
    "electronics": ["useful gadgets", "tech finds"],
    "lifestyle": ["lifestyle finds", "daily routine", "self care ideas"],
}


def short_name(title: str, brand: str | None) -> str:
    """Trim marketplace-style titles ("Brand Item 50ml | Pack of 2 | ...") to a readable name."""
    name = re.split(r"\s[|–—]\s|,\s|\(", title)[0].strip()
    words = name.split()
    if len(words) > 7:
        name = " ".join(words[:7])
    return name or title[:60]


def first_sentence(text: str | None, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s", text)[0]
    return sentence[:limit].rstrip(" ,;:-") if len(sentence) > limit else sentence


class LocalProvider(AIProvider):
    name = "local"

    def generate_pin_copy(self, facts: dict, concept_key: str, variation: int) -> dict:
        title, brand = facts.get("title", ""), facts.get("brand")
        cat = (facts.get("category") or "").strip()
        cat_l = cat.lower()
        name = short_name(title, brand)
        shown = name if not brand or brand.lower() in name.lower() else f"{brand} {name}"
        price = facts.get("price_value")
        if concept_key == "budget_pick" and not (price is not None and float(price) <= 1000):
            concept_key = "spotlight"
        cat_word = cat or "Beauty"
        headlines = {
            "everyday_essentials": f"{shown} for Your Everyday {cat_word} Routine",
            "worth_trying": f"A {cat_word} Pick Worth a Look: {name}",
            "budget_pick": f"Budget-Friendly {cat_word} Pick: {name}",
            "spotlight": f"Product Spotlight: {shown}",
            "save_for_later": f"{cat_word} Finds to Save for Later: {name}",
        }
        headline = headlines.get(concept_key, headlines["spotlight"])
        if len(headline) > 90:
            headline = headline[:89].rstrip() + "…"
        detail = first_sentence(facts.get("description"), 110)
        supporting = detail or f"A {cat_l or 'beauty'} idea to save for later."
        seo_title = f"{shown} | {cat_word} Pick"[:100] if cat else shown[:100]
        kws = [w for w in dict.fromkeys(
            [name.lower(), (brand or "").lower(), f"{cat_l} {name.split()[-1].lower()}" if cat and name.split() else "",
             *CATEGORY_KEYWORDS.get(cat_l, ["product ideas"])]) if w]
        desc_parts = [headline + ".", detail or f"{shown} is a {cat_l or 'product'} idea worth saving.",
                      "Tap through to view the details."]
        tags = " ".join("#" + re.sub(r"\W+", "", k) for k in kws[:3] if re.sub(r"\W+", "", k))
        description = (" ".join(desc_parts) + (f" {tags}" if tags else ""))[:450]
        return {"headline": headline, "supporting_text": supporting[:140], "seo_title": seo_title,
                "seo_description": description, "keywords": kws[:8], "cta": CTAS[variation % len(CTAS)]}
