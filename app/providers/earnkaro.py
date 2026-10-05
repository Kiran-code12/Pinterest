"""EarnKaro: import / manual-link adapter.

Research result: I found NO officially documented public API (or product feed) from EarnKaro for searching
products or generating Profit Links programmatically. Therefore this provider deliberately does NOT call any
EarnKaro endpoint, does not scrape the (authenticated) dashboard and does not automate the app/website.

Workflow: you create the Profit Link in EarnKaro as usual, then import it here (CSV upload or the manual
"add product" form). The adapter validates that the link really is an EarnKaro link (domain allow-list from
EARNKARO_LINK_DOMAINS) so a plain merchant URL can never be stored by mistake.

Extension point: if EarnKaro ever publishes an official API/feed, add a client class, return
{SEARCH, AFFILIATE_LINK} from `capabilities` when credentials exist and implement `search_products` /
`get_affiliate_link`. Nothing else in the application needs to change.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ..config import Settings
from .base import AffiliateProvider, Capability, ProductData, ProviderError, host_of


class EarnKaroProvider(AffiliateProvider):
    key, name, kind = "earnkaro", "EarnKaro", "import"

    def __init__(self, settings: Settings):
        self.s = settings

    def is_configured(self) -> bool:
        return True  # nothing to configure for the import workflow

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.IMPORT})

    def _allowed_host(self, host: str) -> bool:
        return any(host == d or host.endswith("." + d) for d in self.s.earnkaro_link_domains)

    def validate_affiliate_url(self, url: str) -> tuple[bool, str]:
        ok, msg = super().validate_affiliate_url(url)
        if not ok:
            return ok, msg
        if not self._allowed_host(host_of(url)):
            return False, ("Not an EarnKaro Profit Link (expected domain: "
                           f"{', '.join(self.s.earnkaro_link_domains)}). Create the Profit Link in EarnKaro first.")
        return True, "ok"

    def import_row(self, row: dict) -> ProductData:
        link = (row.get("affiliate_url") or row.get("profit_link") or "").strip()
        title = (row.get("title") or "").strip()
        if not link:
            raise ProviderError("EarnKaro rows need a Profit Link (affiliate_url)")
        if not title:
            raise ProviderError("EarnKaro rows need a title")
        price = None
        if row.get("price") not in (None, ""):
            try:
                price = Decimal(str(row["price"]).replace(",", "").replace("₹", "").strip())
            except InvalidOperation:
                price = None
        external = (row.get("external_id") or link).strip()
        return ProductData(
            external_id=external[:128], title=title, product_url=(row.get("product_url") or "").strip() or None,
            image_url=(row.get("image_url") or "").strip() or None, brand=(row.get("brand") or "").strip() or None,
            category=(row.get("category") or "").strip() or None,
            description=(row.get("description") or "").strip() or None, price=price, affiliate_url=link)
