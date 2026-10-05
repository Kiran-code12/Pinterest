from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AffiliateProviderRow
from .amazon import AmazonProvider
from .base import AffiliateProvider
from .demo import DemoProvider
from .earnkaro import EarnKaroProvider


def build_registry(settings: Settings, http: httpx.Client | None = None) -> dict[str, AffiliateProvider]:
    """Register providers here. Adding a network = one class + one line."""
    providers: list[AffiliateProvider] = [AmazonProvider(settings, http), EarnKaroProvider(settings)]
    if settings.enable_demo_provider:
        providers.append(DemoProvider())
    return {p.key: p for p in providers}


def sync_provider_rows(db: Session, registry: dict[str, AffiliateProvider]) -> dict[str, AffiliateProviderRow]:
    rows = {r.key: r for r in db.scalars(select(AffiliateProviderRow))}
    for p in registry.values():
        row = rows.get(p.key)
        if row is None:
            row = AffiliateProviderRow(key=p.key, name=p.name, kind=p.kind, is_demo=p.is_demo)
            db.add(row)
            rows[p.key] = row
        else:
            row.name, row.kind, row.is_demo = p.name, p.kind, p.is_demo
    db.commit()
    return rows
