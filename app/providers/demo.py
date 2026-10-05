"""Demo provider: fictional products so the whole workflow can be tried offline.

Never publishable to Pinterest (is_demo=True): the affiliate URLs are placeholders under example.com.
Images use the `demo://<slug>` scheme and are drawn locally by services/imagery.py (no network).
"""
from __future__ import annotations

from decimal import Decimal

from .base import AffiliateProvider, Capability, ProductData

# (slug, title, brand, category, description, price INR, tags)
_CATALOG = [
    ("rose-glow-lip-oil", "Rose Glow Tinted Lip Oil", "Velora", "Beauty", "Lightweight tinted lip oil with a glossy finish.", 349, "lip gloss"),
    ("velvet-matte-lipstick", "Velvet Matte Lipstick in Nude Rose", "Velora", "Makeup", "Creamy matte lipstick in a everyday nude rose shade.", 499, "lipstick"),
    ("dewy-cushion-foundation", "Dewy Skin Cushion Foundation", "Lumio", "Makeup", "Cushion compact with a natural dewy finish.", 899, "foundation"),
    ("peach-blush-duo", "Peach Blush Duo Palette", "Lumio", "Makeup", "Two coordinating blush shades in one compact.", 599, "blush"),
    ("brow-sculpt-pencil", "Brow Sculpt Pencil Soft Brown", "Arcadia", "Makeup", "Fine-tip brow pencil with a spoolie.", 249, "brow"),
    ("hydra-gel-moisturizer", "Hydra Gel Daily Moisturizer", "Aqualis", "Skincare", "Lightweight gel moisturizer for daily use.", 449, "moisturizer"),
    ("gentle-foam-cleanser", "Gentle Foaming Face Cleanser", "Aqualis", "Skincare", "Soap-free foaming cleanser for everyday face washing.", 299, "cleanser"),
    ("daily-sunscreen-spf50", "Daily Sunscreen Gel SPF 50", "Solenne", "Skincare", "Light gel sunscreen designed for daily use.", 549, "sunscreen"),
    ("vitamin-c-brightening-serum", "Vitamin C Brightening Serum", "Solenne", "Skincare", "Serum with vitamin C for a morning routine.", 699, "serum"),
    ("niacinamide-clarity-serum", "Niacinamide Clarity Serum", "Aqualis", "Skincare", "Niacinamide serum for oily and combination skin routines.", 599, "serum"),
    ("overnight-lip-mask", "Overnight Berry Lip Mask", "Velora", "Skincare", "Overnight lip mask in a berry scent.", 399, "lip care"),
    ("clay-pore-mask", "Green Clay Face Mask", "Terra Bloom", "Skincare", "Clay mask for a weekly self-care routine.", 379, "mask"),
    ("linen-tote-bag", "Everyday Linen Tote Bag", "Marlow", "Fashion", "Roomy linen tote with an inner zip pocket.", 799, "bag"),
    ("pearl-hair-clips", "Pearl Hair Clip Set", "Marlow", "Fashion", "Set of faux pearl hair clips.", 299, "hair accessories"),
    ("gold-hoop-earrings", "Minimal Gold-Tone Hoop Earrings", "Marlow", "Fashion", "Lightweight hoop earrings with a gold-tone finish.", 349, "earrings"),
    ("ceramic-planter-set", "Ceramic Planter Set of 3", "Nestora", "Home", "Three small ceramic planters in neutral tones.", 899, "decor"),
    ("scented-soy-candle", "Soy Wax Candle Vanilla", "Nestora", "Home", "Soy wax candle in a vanilla scent.", 499, "candle"),
    ("bamboo-desk-organizer", "Bamboo Desk Organizer", "Nestora", "Home", "Multi-compartment desk organizer made of bamboo.", 749, "organizer"),
    ("led-vanity-mirror", "LED Vanity Makeup Mirror", "Glimmer", "Electronics", "Tabletop mirror with built-in LED lights.", 1299, "mirror"),
    ("mini-hair-straightener", "Mini Travel Hair Straightener", "Glimmer", "Electronics", "Compact straightener for travel.", 1099, "hair"),
    ("facial-roller-quartz", "Quartz Facial Roller", "Terra Bloom", "Lifestyle", "Facial roller for a self-care routine.", 449, "self care"),
    ("insulated-water-bottle", "Insulated Steel Water Bottle 750ml", "Marlow", "Lifestyle", "Steel bottle with a leak-proof lid.", 849, "bottle"),
    ("gratitude-journal", "Gratitude Journal Undated", "Paperly", "Lifestyle", "Undated journal with guided pages.", 399, "journal"),
    ("silk-sleep-mask", "Silk Sleep Eye Mask", "Terra Bloom", "Lifestyle", "Soft eye mask for sleep and travel.", 349, "sleep"),
]
_SYNONYMS = {"beauty": {"beauty", "makeup", "skincare"}, "makeup": {"makeup", "beauty"}, "skincare": {"skincare", "beauty"}}


class DemoProvider(AffiliateProvider):
    key, name, kind, is_demo = "demo", "Demo catalog (fictional)", "demo", True

    def is_configured(self) -> bool:
        return True

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.SEARCH, Capability.GET_PRODUCT, Capability.AFFILIATE_LINK, Capability.IMPORT})

    @staticmethod
    def _to_data(row) -> ProductData:
        slug, title, brand, category, desc, price, tags = row
        return ProductData(external_id=slug, title=title, brand=brand, category=category, description=desc,
                           price=Decimal(price), image_url=f"demo://{slug}",
                           product_url=f"https://example.com/demo/{slug}",
                           affiliate_url=f"https://example.com/demo-affiliate/{slug}", availability="demo",
                           raw={"demo": True, "tags": tags})

    def search_products(self, query: str, *, category: str | None = None, limit: int = 20) -> list[ProductData]:
        cats = _SYNONYMS.get((category or "").lower(), {(category or "").lower()} if category else set())
        words = [w for w in (query or "").lower().split() if len(w) > 2]
        rows = [r for r in _CATALOG if not cats or r[3].lower() in cats]
        keyword_hits = [r for r in rows if words and any(w in f"{r[1]} {r[4]} {r[6]}".lower() for w in words)]
        ordered = keyword_hits + [r for r in rows if r not in keyword_hits]
        return [self._to_data(r) for r in ordered[:limit]]

    def get_product(self, external_id: str) -> ProductData:
        for r in _CATALOG:
            if r[0] == external_id:
                return self._to_data(r)
        from .base import ProviderError
        raise ProviderError("Unknown demo product")

    def get_affiliate_link(self, product: ProductData) -> str | None:
        return product.affiliate_url or f"https://example.com/demo-affiliate/{product.external_id}"

    def validate_affiliate_url(self, url: str) -> tuple[bool, str]:
        ok, msg = super().validate_affiliate_url(url)
        if ok and "example.com" not in url:
            return False, "Demo links must stay on example.com"
        return ok, msg
