"""Analytics. Two clearly separated kinds of numbers:

  * PINTEREST-REPORTED: fetched from the official analytics endpoint (source='pinterest')  [or typed in by you,
    source='manual']. We never invent these.
  * CALCULATED BY THIS APP: ratios/counts derived from our own database and the numbers above.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AnalyticsRecord, Pin, PublishedPin
from ..pinterest.connection import ConnectionService
from ..pinterest.errors import PinterestError

MIN_PINS_FOR_INSIGHTS = 3


def record_metrics(db: Session, published: PublishedPin, *, impressions=0, saves=0, pin_clicks=0, outbound_clicks=0,
                   source="manual", raw: dict | None = None) -> AnalyticsRecord:
    vals = [max(0, int(v or 0)) for v in (impressions, saves, pin_clicks, outbound_clicks)]
    rec = AnalyticsRecord(published_pin_id=published.id, impressions=vals[0], saves=vals[1], pin_clicks=vals[2],
                          outbound_clicks=vals[3], source=source, raw=raw)
    db.add(rec)
    db.commit()
    return rec


def sync_from_pinterest(db: Session, conn_svc: ConnectionService) -> dict:
    out = {"synced": 0, "errors": []}
    if not conn_svc.is_connected(db):
        out["errors"].append("Pinterest is not connected (or authorization expired). Reconnect in Settings.")
        return out
    sim = conn_svc.provider.simulated
    for pp in db.scalars(select(PublishedPin).where(PublishedPin.mode == "api", PublishedPin.simulated == sim,
                                                    PublishedPin.pinterest_pin_id.is_not(None))):
        try:
            m = conn_svc.call(db, lambda t, pid=pp.pinterest_pin_id: conn_svc.provider.get_analytics(t, pid))
        except PinterestError as ex:
            out["errors"].append(f"pin {pp.pin_id}: {ex}")
            if ex.needs_reconnect:
                break
            continue
        record_metrics(db, pp, impressions=m.impressions, saves=m.saves, pin_clicks=m.pin_clicks,
                       outbound_clicks=m.outbound_clicks, source="pinterest", raw=m.raw)
        out["synced"] += 1
    return out


def latest_by_pin(db: Session) -> list[tuple[PublishedPin, AnalyticsRecord | None]]:
    """Latest record per published pin, preferring numbers reported by Pinterest over manual ones."""
    rows = []
    for pp in db.scalars(select(PublishedPin)):
        recs = sorted(pp.analytics, key=lambda r: r.captured_at)
        reported = [r for r in recs if r.source == "pinterest"]
        rows.append((pp, (reported or recs or [None])[-1]))
    return rows


def pinterest_reported(db: Session) -> dict:
    tot = {"impressions": 0, "saves": 0, "pin_clicks": 0, "outbound_clicks": 0, "pins_with_data": 0}
    for _, rec in latest_by_pin(db):
        if rec is not None:
            tot["pins_with_data"] += 1
            for k in ("impressions", "saves", "pin_clicks", "outbound_clicks"):
                tot[k] += getattr(rec, k)
    return tot


def calculated(db: Session) -> dict:
    """Numbers derived by this app (not reported by Pinterest)."""
    def count(*where):
        return db.scalar(select(func.count()).select_from(Pin).where(*where)) or 0
    generated, published, failed = count(), count(Pin.status == "published"), count(Pin.status == "failed")
    approved_ever = count(Pin.approved_at.is_not(None))
    rep = pinterest_reported(db)
    out = {"pins_generated": generated, "pins_published": published,
           "approval_rate": round(100 * approved_ever / generated, 1) if generated else None,
           "publish_success_rate": round(100 * published / (published + failed), 1) if published + failed else None,
           "outbound_click_rate": round(100 * rep["outbound_clicks"] / rep["impressions"], 2) if rep["impressions"] else None,
           "save_rate": round(100 * rep["saves"] / rep["impressions"], 2) if rep["impressions"] else None}
    return out


def insights(db: Session) -> list[str]:
    """Deterministic 'what worked' hints from YOUR data (correlation, not proof)."""
    groups: dict[str, dict[str, list]] = {"template": defaultdict(list), "concept": defaultdict(list)}
    n = 0
    for pp, rec in latest_by_pin(db):
        if rec is None or pp.simulated:
            continue
        n += 1
        score = rec.outbound_clicks * 3 + rec.saves * 2 + rec.pin_clicks
        groups["template"][pp.pin.template_key].append(score)
        groups["concept"][pp.pin.concept_key].append(score)
    if n < MIN_PINS_FOR_INSIGHTS:
        return [f"Not enough data yet: need metrics for at least {MIN_PINS_FOR_INSIGHTS} published pins."]
    tips = []
    for label, g in groups.items():
        ranked = sorted(((sum(v) / len(v), k, len(v)) for k, v in g.items()), reverse=True)
        if len(ranked) >= 2 and ranked[0][0] > ranked[-1][0]:
            tips.append(f"Best {label} so far: '{ranked[0][1]}' (avg engagement score {ranked[0][0]:.1f} over "
                        f"{ranked[0][2]} pins) vs '{ranked[-1][1]}' ({ranked[-1][0]:.1f}). Try more of the first.")
    tips.append("Small sample: treat this as a hint, not proof.")
    return tips
