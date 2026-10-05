from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod


class AIProviderError(Exception):
    pass


CONCEPTS = {
    "everyday_essentials": "everyday essentials: how the product fits a routine",
    "worth_trying": "a pick worth trying, soft recommendation tone",
    "budget_pick": "a budget-friendly pick (ONLY if a verified price is given, otherwise use spotlight)",
    "spotlight": "a product spotlight: one product, what it is",
    "save_for_later": "an idea to save for later, inspiration tone",
}
COPY_KEYS = ("headline", "supporting_text", "seo_title", "seo_description", "keywords", "cta")


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def generate_pin_copy(self, facts: dict, concept_key: str, variation: int) -> dict:
        """Return dict with COPY_KEYS. Must only use `facts`."""


SYSTEM_PROMPT = (
    "You write Pinterest pin copy for affiliate products in India. Use ONLY the product facts provided. "
    "Never invent or mention prices, discounts, ratings, reviews, ingredients, results, medical or "
    "superlative claims (no 'best in India', '#1', 'guaranteed', 'proven', 'cure'). Do not include numbers that are "
    "not in the facts. Tone: helpful, specific, not spammy. Respond with JSON only."
)


def build_user_prompt(facts: dict, concept_key: str, variation: int) -> str:
    return json.dumps({
        "task": "Write one pin concept.", "concept": CONCEPTS.get(concept_key, concept_key), "variation": variation,
        "product_facts": {k: v for k, v in facts.items() if v not in (None, "")},
        "output_schema": {
            "headline": "<= 70 chars, shown on the pin image", "supporting_text": "<= 120 chars",
            "seo_title": "<= 100 chars, keyword-rich", "seo_description": "<= 450 chars, 1-3 sentences, natural keywords",
            "keywords": "list of 5-8 search keywords (no #)", "cta": "<= 25 chars, e.g. 'See details'"},
    })


def parse_json_reply(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AIProviderError("AI reply was not JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as ex:
        raise AIProviderError("AI reply was not valid JSON") from ex
    if not isinstance(data, dict) or not all(k in data for k in COPY_KEYS):
        raise AIProviderError("AI reply is missing fields")
    if not isinstance(data["keywords"], list):
        data["keywords"] = [str(data["keywords"])]
    return {k: (data[k] if k == "keywords" else str(data[k]).strip()) for k in COPY_KEYS}
