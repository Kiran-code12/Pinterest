import json
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from PIL import Image

from app.ai.base import AIProvider, AIProviderError, parse_json_reply
from app.ai.local import LocalProvider
from app.ai.remote import AnthropicProvider, OpenAIProvider
from app.config import build_settings
from app.models import AffiliateProviderRow, Product
from app.services.content import CONCEPT_ORDER, generate_copy, price_for_display, with_disclosure
from app.services.grounding import validate_copy
from app.services.imagery import demo_image
from app.services.templates import TEMPLATES, H, TemplateContext, W, render_template

FACTS = {"title": "Velvet Matte Lipstick in Nude Rose", "brand": "Velora", "category": "Makeup",
         "description": "Creamy matte lipstick in a nude rose shade.", "price_value": 499.0, "provider_name": "Demo"}


def product(price="499", provider_key="demo", fetched=None):
    p = Product(title=FACTS["title"], brand="Velora", category="Makeup", description=FACTS["description"],
                price_value=Decimal(price) if price else None, price_fetched_at=fetched, dedupe_key="x", external_id="1")
    p.provider = AffiliateProviderRow(key=provider_key, name=provider_key.title(), kind="api")
    return p


def test_local_copy_passes_grounding_for_every_concept():
    for concept in CONCEPT_ORDER:
        c = LocalProvider().generate_pin_copy(FACTS, concept, 0)
        assert validate_copy(c, FACTS) == [], concept
        assert len(c["seo_title"]) <= 100 and len(c["seo_description"]) <= 500


def test_budget_wording_only_with_verified_low_price():
    expensive = {**FACTS, "price_value": 4999.0}
    c = LocalProvider().generate_pin_copy(expensive, "budget_pick", 0)
    assert "budget" not in c["headline"].lower()
    cheap = LocalProvider().generate_pin_copy(FACTS, "budget_pick", 0)
    assert "budget" in cheap["headline"].lower() and validate_copy(cheap, FACTS) == []


@pytest.mark.parametrize("bad", [
    {"headline": "Guaranteed glow", "seo_description": "ok"},
    {"headline": "Only ₹199 today", "seo_description": "ok"},
    {"headline": "ok", "seo_description": "50% off right now"},
    {"headline": "Clinically proven results", "seo_description": "ok"},
    {"headline": "Cheap pick", "seo_description": "ok"},
])
def test_grounding_rejects_invented_claims(bad):
    copy = {"supporting_text": "", "seo_title": "t", "keywords": [], "cta": "See", **bad}
    assert validate_copy(copy, {**FACTS, "price_value": 5000.0}) != []


def test_grounding_allows_numbers_present_in_facts():
    facts = {**FACTS, "title": "Serum 30 ml", "price_value": None}
    copy = {"headline": "Serum 30 ml for your routine", "supporting_text": "", "seo_title": "Serum 30 ml",
            "seo_description": "A 30 ml serum.", "keywords": [], "cta": "See"}
    assert validate_copy(copy, facts) == []


class FakeAI(AIProvider):
    name = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def generate_pin_copy(self, facts, concept_key, variation):
        if self.error:
            raise self.error
        return self.reply


GOOD = {"headline": "A Soft Nude Rose Lip", "supporting_text": "Creamy matte finish", "seo_title": "Velvet Matte Lipstick",
        "seo_description": "Creamy matte lipstick in a nude rose shade.", "keywords": ["lipstick"], "cta": "See details"}


def test_ai_copy_used_when_it_passes_checks():
    c = generate_copy(product(), FakeAI(GOOD), "spotlight")
    assert c.source == "fake" and c.headline == GOOD["headline"] and not c.warnings


def test_ai_hallucination_falls_back_to_local():
    bad = {**GOOD, "headline": "Best price ₹99, 70% off!"}
    c = generate_copy(product(), FakeAI(bad), "spotlight")
    assert c.source == "local-fallback" and c.warnings and "99" not in c.headline


def test_ai_failure_falls_back_to_local():
    c = generate_copy(product(), FakeAI(error=AIProviderError("down")), "spotlight")
    assert c.source == "local-fallback" and c.headline


def test_parse_json_reply_handles_fences_and_garbage():
    assert parse_json_reply("```json\n" + json.dumps(GOOD) + "\n```")["headline"] == GOOD["headline"]
    with pytest.raises(AIProviderError):
        parse_json_reply("not json")
    with pytest.raises(AIProviderError):
        parse_json_reply('{"headline": "x"}')


def S(**kw):
    return build_settings({"DATA_DIR": "/tmp/engine-tests-ai", "ADMIN_PASSWORD": "pw-123456", "SECRET_KEY": "k" * 40, **kw})


def test_openai_provider_request_and_parse():
    seen = {}

    def handler(req):
        seen["req"] = req
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})
    ai = OpenAIProvider(S(OPENAI_API_KEY="sk-test-123456"), httpx.Client(transport=httpx.MockTransport(handler)))
    assert ai.generate_pin_copy(FACTS, "spotlight", 0)["headline"] == GOOD["headline"]
    assert seen["req"].headers["authorization"] == "Bearer sk-test-123456"
    body = json.loads(seen["req"].content)
    assert body["response_format"] == {"type": "json_object"} and "ONLY the product facts" in body["messages"][0]["content"]


def test_anthropic_provider_request_and_parse():
    seen = {}

    def handler(req):
        seen["req"] = req
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(GOOD)}]})
    ai = AnthropicProvider(S(ANTHROPIC_API_KEY="sk-ant-test-123"), httpx.Client(transport=httpx.MockTransport(handler)))
    assert ai.generate_pin_copy(FACTS, "spotlight", 0)["cta"] == "See details"
    assert seen["req"].headers["x-api-key"] == "sk-ant-test-123" and seen["req"].headers["anthropic-version"]
    with pytest.raises(AIProviderError):
        AnthropicProvider(S())


def test_remote_errors_become_ai_errors():
    ai = OpenAIProvider(S(OPENAI_API_KEY="sk-test-123456"),
                        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(AIProviderError):
        ai.generate_pin_copy(FACTS, "spotlight", 0)


def test_price_display_rules():
    assert price_for_display(product("499")) == "₹499"
    assert price_for_display(product(None)) is None
    now = datetime.utcnow()
    assert price_for_display(product("499", "amazon", now - timedelta(hours=2))) == "₹499"
    assert price_for_display(product("499", "amazon", now - timedelta(hours=30))) is None  # stale Amazon price hidden
    assert price_for_display(product("499", "amazon", None)) is None


def test_disclosure_helper():
    assert with_disclosure("Hello.", True, "Some links earn me commission.").endswith("Some links earn me commission.")
    assert with_disclosure("Hello.", False, "X") == "Hello."
    long = with_disclosure("a" * 800, True, "Disclosure text.")
    assert len(long) <= 800 and long.endswith("Disclosure text.")


@pytest.mark.parametrize("key", list(TEMPLATES))
@pytest.mark.parametrize("n_images", [0, 1, 3])
def test_every_template_renders(key, n_images):
    ctx = TemplateContext(headline="A Very Long Headline " * 6, supporting_text="Support text " * 12, brand="Brand",
                          product_name="Name", cta="See details", price_text="₹499", disclosure="Affiliate link",
                          images=[demo_image(f"s{i}", "x") for i in range(n_images)],
                          list_items=["One", "Two " * 20, "Three"], seed="s")
    img = render_template(key, ctx)
    assert img.size == (W, H) == (1000, 1500) and isinstance(img, Image.Image)
    assert len({img.getpixel((x, y)) for x in range(0, W, 97) for y in range(0, H, 97)}) > 3  # not blank


def test_template_with_transparent_png_and_no_texts():
    rgba = Image.new("RGBA", (300, 500), (0, 0, 0, 0))
    ctx = TemplateContext(headline="H", images=[rgba], seed="z")
    for key in TEMPLATES:
        render_template(key, ctx)


def test_five_templates_registered():
    assert set(TEMPLATES) == {"minimal_card", "beauty_editorial", "collage", "top_picks", "spotlight"}
