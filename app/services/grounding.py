"""Checks that generated copy only uses facts supplied by the provider (no invented prices/claims)."""
from __future__ import annotations

import re

BANNED = [
    r"#\s?1\b", r"\bnumber one\b", r"\bguarantee[sd]?\b", r"\b100\s?%", r"\bclinically\b", r"\bproven\b",
    r"\bmiracle\b", r"\bcure[sd]?\b", r"\bheal(s|ed|ing)?\b", r"\bpermanent(ly)?\b", r"\binstant(ly)? results?\b",
    r"\bdermatologist[- ](approved|recommended|tested)\b", r"\bbest in india\b", r"\blowest price\b",
    r"\blimited[- ]time\b", r"\bhurry\b", r"\bact now\b", r"\bwhile stocks? last\b", r"\bfree shipping\b",
    r"\b\d+\s?%\s?off\b", r"\bdiscount(ed)?\b", r"\b(rated|reviews?|bestseller|best[- ]seller)\b",
    r"\b(removes?|erases?|eliminates?|reverses?|fades?)\b", r"\banti[- ]aging\b",
]
BUDGET_WORDS = re.compile(r"\b(affordable|cheap|budget|inexpensive|pocket[- ]friendly|under\s?₹)", re.I)
NUM = re.compile(r"[₹$]?\s?\d[\d,]*(?:\.\d+)?\s?%?")
MAX_TITLE, MAX_DESC, MAX_HEADLINE, MAX_SUPPORT = 100, 500, 90, 140


def facts_text(facts: dict) -> str:
    return " ".join(str(v) for k, v in facts.items() if v not in (None, "") and k != "price_value").lower()


def _digits(text: str) -> set[str]:
    return {re.sub(r"[^\d.]", "", n).strip(".") for n in NUM.findall(text) if re.search(r"\d", n)}


def validate_copy(copy: dict, facts: dict, *, allowed_numbers: set[str] | None = None) -> list[str]:
    """Return a list of problems (empty = acceptable)."""
    problems: list[str] = []
    joined = " ".join(str(copy.get(k, "")) for k in ("headline", "supporting_text", "seo_title",
                                                       "seo_description", "cta"))
    joined += " " + " ".join(copy.get("keywords") or [])
    low = joined.lower()
    for pat in BANNED:
        if re.search(pat, low) and not re.search(pat, facts_text(facts)):
            problems.append(f"unverified claim: {re.search(pat, low).group(0)!r}")
    price = facts.get("price_value")
    if BUDGET_WORDS.search(joined) and not (price is not None and float(price) <= 1000):
        problems.append("price-related wording without a verified low price")
    ok_nums = _digits(facts_text(facts)) | (allowed_numbers or set())
    if price is not None:
        ok_nums.add(str(int(float(price))))
    for n in _digits(joined):
        if n and n not in ok_nums:
            problems.append(f"number not found in product facts: {n}")
    limits = {"headline": MAX_HEADLINE, "supporting_text": MAX_SUPPORT, "seo_title": MAX_TITLE,
              "seo_description": MAX_DESC}
    for key, limit in limits.items():
        if len(str(copy.get(key, ""))) > limit:
            problems.append(f"{key} longer than {limit} characters")
    for key in ("headline", "seo_title", "seo_description"):
        if not str(copy.get(key, "")).strip():
            problems.append(f"{key} is empty")
    return problems
