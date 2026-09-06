import datetime as dt
import json

from backend import models


def add_current_offer_evidence(db, products):
    """Attach explicit fictional public evidence to legacy decision fixtures."""
    db.flush()
    fetched_at = dt.datetime.combine(dt.date.today(), dt.time(12, 0)).isoformat()
    for product in products:
        values = {
            "current_offer_points": product.current_offer_effective,
            "current_offer_cash": product.current_offer_cash,
            "current_offer_min_spend": product.current_offer_min_spend,
            "current_offer_window_months": product.current_offer_window_months,
        }
        for field, value in values.items():
            if value is None:
                continue
            db.add(
                models.IngestionEvidence(
                    product_id=product.id,
                    field=field,
                    value_json=json.dumps(value),
                    source_url=product.source_url,
                    fetched_at=fetched_at,
                    content_hash=f"fixture-{product.id}-{field}",
                    confidence=0.95,
                    evidence_snippets={field: [f"{product.product_name} {field} fixture."]},
                    offer_status="public",
                )
            )
