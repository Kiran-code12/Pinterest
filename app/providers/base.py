"""Provider abstraction. Amazon, EarnKaro (and any future network) all produce `ProductData`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlsplit

from ..security import UnsafeURL, validate_http_url


class Capability(StrEnum):
    SEARCH = "search"            # search_products via an official API/feed
    GET_PRODUCT = "get_product"  # fetch one product by id via an official API/feed
    AFFILIATE_LINK = "affiliate_link"  # generate an affiliate link programmatically
    IMPORT = "import"            # CSV/manual import workflow


class ProviderError(Exception):
    """Safe-to-display error (never contains secrets)."""


class NotSupported(ProviderError):
    pass


class ProviderNotConfigured(ProviderError):
    pass


@dataclass
class ProductData:
    """Normalized product shared by every provider."""
    external_id: str
    title: str
    product_url: str | None = None
    image_url: str | None = None
    brand: str | None = None
    category: str | None = None
    description: str | None = None
    price: Decimal | None = None
    currency: str = "INR"
    availability: str | None = None
    affiliate_url: str | None = None
    price_fetched_at: datetime | None = None
    raw: dict = field(default_factory=dict)


class AffiliateProvider(ABC):
    key: str = ""
    name: str = ""
    kind: str = "api"  # api | import | demo
    is_demo: bool = False

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.IMPORT})

    @abstractmethod
    def is_configured(self) -> bool:
        """True when credentials needed for the API capabilities are present."""

    def can(self, cap: Capability) -> bool:
        return cap in self.capabilities

    # ---- capability methods (override what the provider genuinely supports) ----
    def search_products(self, query: str, *, category: str | None = None, limit: int = 20) -> list[ProductData]:
        raise NotSupported(f"{self.name} has no official search integration; use import instead.")

    def get_product(self, external_id: str) -> ProductData:
        raise NotSupported(f"{self.name} cannot fetch products by id.")

    def get_affiliate_link(self, product: ProductData) -> str | None:
        raise NotSupported(f"{self.name} cannot generate affiliate links automatically.")

    def get_product_image(self, product: ProductData) -> str | None:
        return product.image_url

    def get_product_metadata(self, product: ProductData) -> dict:
        return {"title": product.title, "brand": product.brand, "category": product.category,
                "description": product.description, "availability": product.availability}

    def validate_affiliate_url(self, url: str) -> tuple[bool, str]:
        """Provider-specific sanity check of an affiliate URL. Returns (ok, message)."""
        try:
            validate_http_url(url)
        except UnsafeURL as ex:
            return False, str(ex)
        return True, "ok"

    def status(self) -> dict:
        return {"key": self.key, "name": self.name, "kind": self.kind, "configured": self.is_configured(),
                "capabilities": sorted(c.value for c in self.capabilities), "is_demo": self.is_demo}


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
