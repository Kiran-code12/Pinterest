"""Pin copy generation: ask the AI provider, verify against product facts, fall back to deterministic copy."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..ai.base import AIProvider, AIProviderError
from ..ai.local import LocalProvider
from ..models import Product
from .grounding import validate_copy

log = logging.getLogger("engine.content")
CONCEPT_ORDER = ["everyday_essentials", "worth_trying", "budget_pick", "spotlight", "save_for_later"]


@dataclass
class PinCopy:
    concept_key: str
    headline: str
    supporting_text: str
    seo_title: str
    seo_description: str
    keywords: list[str]
    cta: str
    source: str = "local"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"concept_key": self.concept_key, "headline": self.headline, "supporting_text": self.supporting_text,
                "seo_title": self.seo_title, "seo_description": self.seo_description, "keywords": self.keywords,
                "cta": self.cta}


def product_facts(product: Product) -> dict:
    """The ONLY information the AI may use. Price is included only if known."""
    return {"title": product.title, "brand": product.brand, "category": product.category,
            "description": product.description, "availability": product.availability,
            "provider_name": product.provider.name if product.provider else None,
            "price_value": float(product.price_value) if product.price_value is not None else None,
            "currency": product.price_currency if product.price_value is not None else None}


def generate_copy(product: Product, ai: AIProvider, concept_key: str, variation: int = 0) -> PinCopy:
    facts = product_facts(product)
    source = ai.name
    try:
        raw = ai.generate_pin_copy(facts, concept_key, variation)
        problems = validate_copy(raw, facts)
    except AIProviderError as ex:
        log.warning("AI generation failed (%s); using local copy", ex)
        raw, problems, source = None, [f"AI unavailable: {ex}"], ai.name
    except Exception as ex:  # never let a provider bug break the workflow
        log.exception("unexpected AI error")
        raw, problems = None, [f"AI error: {type(ex).__name__}"]
    warnings: list[str] = []
    if raw is None or problems:
        warnings = problems
        raw = LocalProvider().generate_pin_copy(facts, concept_key, variation)
        source = "local" if ai.name == "local" else "local-fallback"
        local_problems = validate_copy(raw, facts)
        if local_problems:  # should not happen; keep visible if it does
            warnings += [f"local copy: {p}" for p in local_problems]
    return PinCopy(concept_key=concept_key, source=source, warnings=warnings, **{
        k: raw[k] for k in ("headline", "supporting_text", "seo_title", "seo_description", "keywords", "cta")})


def with_disclosure(description: str, show: bool, text: str) -> str:
    """Append the user's configured disclosure (kept intact; the description is trimmed to make room)."""
    if not show or not text or text in description:
        return description[:800]
    room = 800 - len(text) - 1
    return description[:room].rstrip() + " " + text


def price_for_display(product: Product, now: datetime | None = None, max_age_hours: int = 24) -> str | None:
    """Amazon terms: only show prices that are fresh. Others (import/manual/demo): shown when known."""
    if product.price_value is None:
        return None
    if product.provider and product.provider.key == "amazon":
        now = now or datetime.utcnow()
        if not product.price_fetched_at or (now - product.price_fetched_at).total_seconds() > max_age_hours * 3600:
            return None
    amount = float(product.price_value)
    symbol = "₹" if (product.price_currency or "INR") == "INR" else (product.price_currency + " ")
    return f"{symbol}{amount:,.0f}" if amount == int(amount) else f"{symbol}{amount:,.2f}"
