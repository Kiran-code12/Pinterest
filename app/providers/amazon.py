"""Amazon Associates India via the official **Creators API** (successor of the retired PA-API 5.0).

Facts this adapter is built on (public Associates Central documentation, summarised):
  * Auth: OAuth 2.0 client-credentials against Login with Amazon.
        POST <token url>  JSON {grant_type: client_credentials, client_id, client_secret, scope: creatorsapi::default}
        India (www.amazon.in) uses the EU credential version 3.2 -> https://api.amazon.co.uk/auth/o2/token
  * Calls: POST https://creatorsapi.amazon/catalog/v1/searchItems | getItems
        headers: Authorization: Bearer <token>, x-marketplace: www.amazon.in
        body keys are lowerCamelCase (keywords, partnerTag, partnerType, marketplace, resources, itemIds ...)
  * PA-API 5.0 AWS-signature keys (the old AMAZON_ACCESS_KEY/AMAZON_SECRET_KEY) do NOT work with it.

LIMITATIONS (be honest): written from documentation, not verified against a live account (credentials
are issued only to eligible Associates accounts). Resource names and response paths are therefore
configurable/defensive. Amazon's terms limit how long prices and images may be cached: prices are only
rendered on pins while fresh (see services/templates.py).

Without credentials the provider still works for the IMPORT workflow: ASIN/URL -> affiliate link built
with your partner tag (a normal tagged Amazon link).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from ..config import Settings
from .base import AffiliateProvider, Capability, NotSupported, ProductData, ProviderError, ProviderNotConfigured, host_of

TOKEN_URLS = {  # credential version -> LwA token endpoint
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
}
RESOURCES = [
    "images.primary.large", "images.primary.medium", "itemInfo.title", "itemInfo.byLineInfo",
    "itemInfo.features", "itemInfo.classifications", "offersV2.listings.price", "offersV2.listings.availability",
]
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/gp/aw/d/|/product/)([A-Z0-9]{10})(?:[/?#]|$)")
BARE_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def parse_asin(text: str) -> str | None:
    text = (text or "").strip()
    if BARE_ASIN_RE.match(text.upper()) and text.upper() == text:
        return text
    m = ASIN_RE.search(urlsplit(text).path + "/") if text.startswith("http") else None
    return m.group(1) if m else None


def dig(data, path: str, default=None):
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur if cur is not None else default


class AmazonProvider(AffiliateProvider):
    key, name, kind = "amazon", "Amazon Associates India", "api"

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=20)
        self._token: str | None = None
        self._token_expiry = 0.0

    # ---- configuration -------------------------------------------------------------------------
    def is_configured(self) -> bool:
        return bool(self.s.amazon_credential_id and self.s.amazon_credential_secret and self.s.amazon_partner_tag)

    @property
    def capabilities(self) -> frozenset[Capability]:
        caps = {Capability.IMPORT}
        if self.is_configured():
            caps |= {Capability.SEARCH, Capability.GET_PRODUCT, Capability.AFFILIATE_LINK}
        elif self.s.amazon_partner_tag:
            caps.add(Capability.AFFILIATE_LINK)  # tagged links can be built from an ASIN without the API
        return frozenset(caps)

    # ---- links ---------------------------------------------------------------------------------
    def build_affiliate_url(self, asin: str) -> str | None:
        if not self.s.amazon_partner_tag:
            return None
        return f"https://{self.s.amazon_marketplace}/dp/{asin}?tag={self.s.amazon_partner_tag}"

    def get_affiliate_link(self, product: ProductData) -> str | None:
        if product.affiliate_url:
            return product.affiliate_url
        asin = parse_asin(product.external_id) or parse_asin(product.product_url or "")
        return self.build_affiliate_url(asin) if asin else None

    def validate_affiliate_url(self, url: str) -> tuple[bool, str]:
        ok, msg = super().validate_affiliate_url(url)
        if not ok:
            return ok, msg
        host = host_of(url)
        if not (host.endswith("amazon.in") or host in {"amzn.to", "amzn.in", "www.amzn.in"}):
            return False, "Amazon links must point to amazon.in (or an amzn short link)"
        tag = parse_qs(urlsplit(url).query).get("tag", [""])[0]
        if host.endswith("amazon.in") and not tag:
            return False, "Amazon link has no Associates tag (tag=...); commission would be lost"
        if self.s.amazon_partner_tag and host.endswith("amazon.in") and tag != self.s.amazon_partner_tag:
            return False, "Amazon link tag does not match AMAZON_PARTNER_TAG"
        return True, "ok"

    # ---- API ------------------------------------------------------------------------------------
    def _require(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Amazon Creators API is not configured. Set AMAZON_CREDENTIAL_ID, AMAZON_CREDENTIAL_SECRET and "
                "AMAZON_PARTNER_TAG, or use the CSV import workflow.")

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        url = self.s.amazon_token_url or TOKEN_URLS.get(self.s.amazon_credential_version, TOKEN_URLS["3.2"])
        try:
            r = self.http.post(url, json={
                "grant_type": "client_credentials", "client_id": self.s.amazon_credential_id,
                "client_secret": self.s.amazon_credential_secret, "scope": "creatorsapi::default"})
        except httpx.HTTPError as ex:
            raise ProviderError("Could not reach Amazon's token service") from ex
        if r.status_code != 200:
            raise ProviderError(f"Amazon rejected the credentials (HTTP {r.status_code}). Check AMAZON_CREDENTIAL_* "
                                "and that your Associates account is eligible for the Creators API.")
        body = r.json()
        self._token = body.get("access_token")
        if not self._token:
            raise ProviderError("Amazon token response had no access_token")
        self._token_expiry = time.time() + float(body.get("expires_in", 3600))
        return self._token

    def _post(self, op: str, payload: dict) -> dict:
        self._require()
        payload = {**payload, "partnerTag": self.s.amazon_partner_tag, "partnerType": "Associates",
                   "marketplace": self.s.amazon_marketplace, "resources": RESOURCES}
        headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json",
                   "x-marketplace": self.s.amazon_marketplace}
        try:
            r = self.http.post(f"{self.s.amazon_api_base}/catalog/v1/{op}", json=payload, headers=headers)
        except httpx.HTTPError as ex:
            raise ProviderError("Could not reach the Amazon Creators API") from ex
        if r.status_code == 429:
            raise ProviderError("Amazon rate limit reached; try again later")
        if r.status_code in (401, 403):
            self._token = None
            raise ProviderError(f"Amazon denied the request (HTTP {r.status_code}); check credentials/eligibility")
        if r.status_code >= 400:
            raise ProviderError(f"Amazon API error (HTTP {r.status_code})")
        return r.json()

    def search_products(self, query: str, *, category: str | None = None, limit: int = 20) -> list[ProductData]:
        out: list[ProductData] = []
        pages = min(10, -(-limit // 10))
        for page in range(1, pages + 1):
            body = {"keywords": query if not category else f"{query} {category}".strip(),
                    "itemCount": min(10, limit - len(out)), "itemPage": page}
            data = self._post("searchItems", body)
            items = dig(data, "searchResult.items", []) or []
            out.extend(self._parse_item(i, category) for i in items if isinstance(i, dict))
            if len(items) < body["itemCount"] or len(out) >= limit:
                break
        return [p for p in out if p.external_id][:limit]

    def get_product(self, external_id: str) -> ProductData:
        asin = parse_asin(external_id) or external_id
        data = self._post("getItems", {"itemIds": [asin]})
        items = dig(data, "itemsResult.items", []) or []
        if not items:
            raise ProviderError(f"Amazon returned no item for {asin}")
        return self._parse_item(items[0], None)

    def _parse_item(self, item: dict, category: str | None) -> ProductData:
        asin = item.get("asin", "")
        url = item.get("detailPageURL") or item.get("detailPageUrl")
        price = None
        for path in ("offersV2.listings.price.money.amount", "offersV2.listings.price.amount",
                     "offers.listings.price.amount"):
            raw = dig(item, path)
            if raw is not None:
                try:
                    price = Decimal(str(raw))
                    break
                except InvalidOperation:
                    pass
        features = dig(item, "itemInfo.features.displayValues", []) or []
        affiliate = url if url and self.validate_affiliate_url(url)[0] else self.build_affiliate_url(asin)
        return ProductData(
            external_id=asin,
            title=dig(item, "itemInfo.title.displayValue", "") or "",
            product_url=f"https://{self.s.amazon_marketplace}/dp/{asin}" if asin else url,
            image_url=dig(item, "images.primary.large.url") or dig(item, "images.primary.medium.url"),
            brand=dig(item, "itemInfo.byLineInfo.brand.displayValue"),
            category=category or dig(item, "itemInfo.classifications.productGroup.displayValue"),
            description=" ".join(features[:3]) if features else None,
            price=price, currency=dig(item, "offersV2.listings.price.money.currency", "INR") or "INR",
            availability=dig(item, "offersV2.listings.availability.type"),
            affiliate_url=affiliate,
            price_fetched_at=datetime.now(timezone.utc).replace(tzinfo=None) if price is not None else None,
            raw={"asin": asin},
        )

    # ---- import helper (no API needed) -----------------------------------------------------------
    def import_row(self, row: dict) -> ProductData:
        ident = (row.get("asin") or row.get("external_id") or row.get("product_url") or "").strip()
        asin = parse_asin(ident)
        if not asin:
            raise ProviderError("Amazon rows need a valid ASIN or an amazon.in product URL")
        url = (row.get("affiliate_url") or "").strip() or self.build_affiliate_url(asin)
        if url and "tag=" not in url and host_of(url).endswith("amazon.in") and self.s.amazon_partner_tag:
            parts = urlsplit(url)
            query = parts.query + ("&" if parts.query else "") + urlencode({"tag": self.s.amazon_partner_tag})
            url = urlunsplit(parts._replace(query=query))
        return ProductData(external_id=asin, title=(row.get("title") or "").strip(),
                           product_url=f"https://{self.s.amazon_marketplace}/dp/{asin}", affiliate_url=url,
                           image_url=(row.get("image_url") or "").strip() or None,
                           brand=(row.get("brand") or "").strip() or None,
                           category=(row.get("category") or "").strip() or None,
                           description=(row.get("description") or "").strip() or None,
                           price=_money(row.get("price")))

    def search_unavailable_reason(self) -> str:
        return "" if self.is_configured() else "Amazon Creators API credentials not set"


def _money(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation:
        return None


__all__ = ["AmazonProvider", "NotSupported", "parse_asin"]
