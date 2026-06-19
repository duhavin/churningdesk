"""Validation + delta-gating → commit or ProposedChange queue (§4.4).

  * First sight of a value (nothing to overwrite) commits with provenance.
  * A subsequent change beyond threshold (offer Δ > OFFER_DELTA_THRESHOLD, or
    ANY eligibility-rule change) requires human approval.
  * Small high-confidence updates may auto-commit when AUTO_COMMIT_SMALL_CHANGES.

This module writes only to PUBLIC tables (CardProduct, ProposedChange).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models
from .extract import OfferExtraction


def _utcnow() -> dt.datetime:
    # Naive UTC (utcnow() is deprecated on 3.12+); matches naive DateTime columns.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

# Offer fields whose change is gated by the fractional delta threshold.
OFFER_FIELDS = {
    "current_offer_points",
    "current_offer_cash",
    "current_offer_min_spend",
    "current_offer_window_months",
    "peak_offer_points",
    "peak_offer_min_spend",
    "targeted_peak_offer_points",
    "targeted_peak_offer_cash",
    "referral_bonus_points",
    "referral_bonus_cash",
    "first_year_credit_value",
    "annual_fee",
}
# Peak high-water marks are durable history — a *decrease* always needs review.
PEAK_FIELDS = {"peak_offer_points", "targeted_peak_offer_points"}
# Any change here is an eligibility-rule change → always needs approval.
ELIG_FIELDS = {"eligibility_tags", "reports_to_personal_credit"}
# Valuation pages must never write offer/peak fields (they aren't offer sources).
NON_OFFER_SOURCE_TERMS = (
    "monthly-valuations",
    "point-valuations",
    "points-valuations",
    "valuations-of-points",
)

HIGH_CONFIDENCE = 0.8
FIRST_SIGHT_FIELD_CONFIDENCE = {
    "currency": 0.7,
    "annual_fee": 0.7,
    "current_offer_points": 0.7,
    "current_offer_cash": 0.7,
    "current_offer_min_spend": 0.7,
    "current_offer_window_months": 0.7,
    "peak_offer_points": 0.75,
    "peak_offer_min_spend": 0.75,
    "targeted_peak_offer_points": 0.75,
    "targeted_peak_offer_cash": 0.75,
    "referral_bonus_points": 0.75,
    "referral_bonus_cash": 0.75,
    "first_year_credit_value": 0.75,
    "earn_multipliers": 0.75,
    "best_category_uses": 0.75,
    "card_benefits": 0.75,
    "downgrade_paths": 0.75,
    "eligibility_tags": 0.8,
    "reports_to_personal_credit": 0.8,
}
TARGETED_TERMS = (
    "targeted",
    "invite only",
    "invite-only",
    "incognito",
    "pre-qualified",
    "prequalified",
    "pre-qualify",
    "as high as",
    "phone offer",
    "mail offer",
    "email offer",
)


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _is_non_offer_source(source_url: str | None) -> bool:
    if not source_url:
        return False
    low = source_url.lower()
    return any(term in low for term in NON_OFFER_SOURCE_TERMS)


def _normalize_url(value: str | None) -> str | None:
    if not value:
        return value
    raw = value.strip()
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _normalize_currency(product: models.CardProduct, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    low = value.strip().lower()
    issuer = (product.issuer or "").strip().lower()
    if not low:
        return value
    if "cash" in low or "statement credit" in low:
        return "cash back"
    if "ultimate reward" in low or issuer == "chase":
        return "Chase Ultimate Rewards"
    if "membership reward" in low or issuer in {"american express", "amex"}:
        return "Amex Membership Rewards"
    if "thankyou" in low or "thank you" in low or issuer == "citi":
        return "Citi ThankYou Points"
    if "capital one" in low or issuer == "capital one":
        return "Capital One Miles"
    if "bilt" in low or issuer == "bilt":
        return "Bilt Rewards"
    if "wells fargo" in low or issuer == "wells fargo":
        return "Wells Fargo Rewards"
    return value.strip()


def _looks_targeted(ext: OfferExtraction, fields: dict) -> bool:
    if ext.is_targeted or ext.offer_status == "targeted":
        return True
    haystack = " ".join(
        str(part or "")
        for part in (
            ext.needs_review_reason,
            ext.eligibility_language,
            ext.source_url,
            ext.product_url,
            fields.get("targeted_peak_offer_source"),
        )
    ).lower()
    for snippets in (ext.evidence_snippets or {}).values():
        if isinstance(snippets, list):
            haystack += " " + " ".join(str(s) for s in snippets).lower()
    return any(term in haystack for term in TARGETED_TERMS)


def _normalize_offer_payload(
    product: models.CardProduct,
    ext: OfferExtraction,
    source_url: str,
    fields: dict,
) -> dict:
    fields = dict(fields)
    if "currency" in fields:
        fields["currency"] = _normalize_currency(product, fields["currency"])

    for source_field in ("peak_offer_source", "targeted_peak_offer_source"):
        if source_field in fields:
            fields[source_field] = _normalize_url(fields[source_field])

    source = _normalize_url(source_url)
    targeted = _looks_targeted(ext, fields)
    if targeted:
        if fields.get("current_offer_points") and not fields.get("targeted_peak_offer_points"):
            fields["targeted_peak_offer_points"] = fields.pop("current_offer_points")
            fields["targeted_peak_offer_source"] = (
                fields.get("targeted_peak_offer_source") or source or ext.source_url
            )
            fields["targeted_peak_offer_date"] = (
                fields.get("targeted_peak_offer_date") or dt.date.today().isoformat()
            )
            fields.pop("current_offer_min_spend", None)
            fields.pop("current_offer_window_months", None)
        if fields.get("current_offer_cash") and not fields.get("targeted_peak_offer_cash"):
            fields["targeted_peak_offer_cash"] = fields.pop("current_offer_cash")
            fields.pop("current_offer_min_spend", None)
            fields.pop("current_offer_window_months", None)

    # Point offers and cash offers are separate. A points offer with thousands
    # of "cash" dollars is almost always a parser mixing points and dollars.
    if fields.get("current_offer_points") and (fields.get("current_offer_cash") or 0) > 1000:
        fields.pop("current_offer_cash", None)

    if _is_non_offer_source(source_url):
        fields = {
            field: value
            for field, value in fields.items()
            if field not in _OFFER_WRITE_FIELDS
        }

    if fields.get("peak_offer_points") and not fields.get("peak_offer_min_spend"):
        spend = fields.get("current_offer_min_spend") or product.current_offer_min_spend
        if spend:
            fields["peak_offer_min_spend"] = spend
    return fields


def _decision(field: str, old, new, confidence: float) -> str:
    """Return 'commit', 'propose', or 'skip'."""
    if _empty(old):
        if confidence < FIRST_SIGHT_FIELD_CONFIDENCE.get(field, 0.65):
            return "propose"
        return "commit"  # first sight — nothing to overwrite
    if old == new:
        return "skip"
    if field in ELIG_FIELDS:
        return "propose"  # eligibility-rule change always reviewed
    # Peak is durable history: raise freely, but review any decrease.
    if field in PEAK_FIELDS and isinstance(old, (int, float)) and isinstance(new, (int, float)):
        if new > old:
            return "commit"
        return "propose"
    if field in OFFER_FIELDS and isinstance(old, (int, float)) and isinstance(new, (int, float)):
        delta = abs(new - old) / max(abs(old), 1)
        if delta > config.OFFER_DELTA_THRESHOLD:
            return "propose"
        # small change
        if config.AUTO_COMMIT_SMALL_CHANGES and confidence >= HIGH_CONFIDENCE:
            return "commit"
        return "propose"
    # other fields
    if config.AUTO_COMMIT_SMALL_CHANGES and confidence >= HIGH_CONFIDENCE:
        return "commit"
    return "propose"


def _extraction_fields(ext: OfferExtraction) -> dict:
    """Map an OfferExtraction to CardProduct field values."""
    fields: dict = {
        "currency": ext.currency,
        "annual_fee": ext.annual_fee,
        "current_offer_points": ext.current_offer_points,
        "current_offer_cash": ext.current_offer_cash,
        "current_offer_min_spend": ext.current_offer_min_spend,
        "current_offer_window_months": ext.current_offer_window_months,
        "peak_offer_points": ext.peak_offer_points,
        "peak_offer_min_spend": ext.peak_offer_min_spend,
        "peak_offer_source": ext.peak_offer_source,
        "peak_offer_date": ext.peak_offer_date,
        "targeted_peak_offer_points": ext.targeted_peak_offer_points,
        "targeted_peak_offer_cash": ext.targeted_peak_offer_cash,
        "targeted_peak_offer_source": ext.targeted_peak_offer_source,
        "targeted_peak_offer_date": ext.targeted_peak_offer_date,
        "referral_bonus_points": ext.referral_bonus_points,
        "referral_bonus_cash": ext.referral_bonus_cash,
        "best_category_uses": ext.best_category_uses,
        "card_benefits": ext.card_benefits,
        "downgrade_paths": ext.downgrade_paths,
        "first_year_credit_value": ext.first_year_credit_value,
        "eligibility_tags": ext.eligibility_tags,
        "reports_to_personal_credit": ext.reports_to_personal_credit,
    }
    if ext.earn_multipliers:
        fields["earn_multipliers"] = {m.category: m.multiplier for m in ext.earn_multipliers}
    # A fresh public current offer also raises the public peak (peak is a max).
    if ext.current_offer_points and (
        ext.peak_offer_points is None or ext.current_offer_points > ext.peak_offer_points
    ):
        fields["peak_offer_points"] = max(ext.current_offer_points, ext.peak_offer_points or 0)
        fields["peak_offer_source"] = fields.get("peak_offer_source") or ext.source_url or ext.product_url
    # Drop unknown (null) extracted values so we never overwrite with nothing.
    return {k: v for k, v in fields.items() if v is not None}


# Offer/peak fields that a non-offer source (valuation page) must never write.
_OFFER_WRITE_FIELDS = OFFER_FIELDS | PEAK_FIELDS | {"peak_offer_source", "peak_offer_date", "current_offer_window_months"}


def _record_evidence(
    db: Session,
    product: models.CardProduct,
    ext: OfferExtraction,
    source_url: str,
    new_fields: dict,
) -> int:
    evidence = ext.evidence_snippets or {}
    recorded = 0
    for field, value in new_fields.items():
        snippets = evidence.get(field) or evidence.get(_scan_field_name(field)) or []
        db.add(
            models.IngestionEvidence(
                product_id=product.id,
                field=field,
                value_json=json.dumps(value),
                source_url=source_url,
                fetched_at=ext.fetched_at,
                content_hash=ext.content_hash,
                confidence=ext.confidence,
                evidence_snippets=snippets,
                offer_status=ext.offer_status,
            )
        )
        recorded += 1
    return recorded


def _scan_field_name(field: str) -> str:
    """Map CardProduct field names back to strict scan-row field names."""
    return {
        "current_offer_points": "bonus_amount",
        "current_offer_cash": "bonus_amount",
        "current_offer_min_spend": "spend_requirement",
        "current_offer_window_months": "spend_window_months",
    }.get(field, field)


def apply_extraction(
    db: Session,
    product: models.CardProduct,
    ext: OfferExtraction,
    source_url: str,
    commit: bool = True,
) -> dict:
    """Apply an extraction: commit first-sight/small changes, queue the rest.

    Pass ``commit=False`` to defer the flush so a batch refresh can commit once
    at the end (one SQLite fsync instead of one per product).
    """
    confidence = ext.confidence or 0.0
    normalized_source = _normalize_url(source_url) or source_url
    new_fields = _normalize_offer_payload(product, ext, normalized_source, _extraction_fields(ext))
    evidence_recorded = _record_evidence(db, product, ext, source_url, new_fields)

    committed: list[str] = []
    proposed: list[str] = []

    for field, new_value in new_fields.items():
        old_value = getattr(product, field, None)
        decision = _decision(field, old_value, new_value, confidence)
        if decision == "skip":
            continue
        if decision == "commit":
            setattr(product, field, new_value)
            committed.append(field)
        else:  # propose
            serialized = json.dumps(new_value)
            existing = db.scalar(
                select(models.ProposedChange).where(
                    models.ProposedChange.target_table == "card_product",
                    models.ProposedChange.target_id == product.id,
                    models.ProposedChange.field == field,
                    models.ProposedChange.new_value == serialized,
                    models.ProposedChange.status == "pending",
                )
            )
            if existing is None:
                db.add(
                    models.ProposedChange(
                        target_table="card_product",
                        target_id=product.id,
                        field=field,
                        old_value=json.dumps(old_value),
                        new_value=serialized,
                        source_url=normalized_source,
                        confidence=confidence,
                        status="pending",
                    )
                )
            proposed.append(field)

    # A fresh public current offer supersedes any manual targeted override.
    if "current_offer_points" in committed and product.current_offer_override is not None:
        product.current_offer_override = None

    # Provenance: every touched product records where + when it was verified.
    if committed:
        product.source_url = normalized_source
        product.last_verified = _utcnow()
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "committed": committed,
        "proposed": proposed,
        "evidence_recorded": evidence_recorded,
    }


# --- Review-queue actions ---------------------------------------------------
def _coerce(field: str, value):
    return value  # values are stored JSON-typed, so json.loads already gives the right type


def approve_change(db: Session, change: models.ProposedChange) -> None:
    if change.status != "pending":
        return
    if change.target_table == "card_product":
        product = db.get(models.CardProduct, change.target_id)
        if product is not None:
            value = json.loads(change.new_value) if change.new_value is not None else None
            setattr(product, change.field, _coerce(change.field, value))
            product.source_url = change.source_url or product.source_url
            product.last_verified = _utcnow()
    change.status = "approved"
    db.commit()


def reject_change(db: Session, change: models.ProposedChange) -> None:
    if change.status != "pending":
        return
    change.status = "rejected"
    db.commit()
