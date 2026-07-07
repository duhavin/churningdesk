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
from urllib.parse import urlparse
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models, source_quality
from ..benefit_normalization import is_benefit_noise, normalize_known_public_facts, normalize_public_benefits
from ..product_identity import reward_currency_for_product
from ..text_sanitize import clean_text, looks_like_scrape_junk
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
OFFICIAL_AUTO_COMMIT_CONFIDENCE = 0.86
GENERIC_REWARD_CURRENCIES = {"points", "miles", "cash", "cash back", "cashback"}
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
OFFICIAL_AUTO_COMMIT_FIELDS = {
    "annual_fee",
    "current_offer_points",
    "current_offer_cash",
    "current_offer_min_spend",
    "current_offer_window_months",
    "first_year_credit_value",
}
FIELD_EVIDENCE_ALIASES = {
    "annual_fee": ("annual_fee",),
    "current_offer_points": ("current_offer_points", "bonus_amount"),
    "current_offer_cash": ("current_offer_cash", "bonus_amount"),
    "current_offer_min_spend": ("current_offer_min_spend", "spend_requirement"),
    "current_offer_window_months": ("current_offer_window_months", "spend_window_months"),
    "first_year_credit_value": ("first_year_credit_value",),
    "peak_offer_points": ("peak_offer_points", "peak_bonus_amount", "current_offer_points", "bonus_amount"),
    "peak_offer_min_spend": (
        "peak_offer_min_spend",
        "peak_spend_requirement",
        "current_offer_min_spend",
        "spend_requirement",
    ),
}
FIELD_EVIDENCE_REQUIRED_FOR_COMMIT = set(FIELD_EVIDENCE_ALIASES)
CURRENT_OFFER_FIELDS = {
    "current_offer_points",
    "current_offer_cash",
    "current_offer_min_spend",
    "current_offer_window_months",
}
SUPPLEMENTAL_WRITE_FIELDS = {
    "earn_multipliers",
    "best_category_uses",
    "card_benefits",
    "downgrade_paths",
    "eligibility_tags",
    "reports_to_personal_credit",
}


@dataclass(slots=True)
class ProposalReview:
    action: str = "propose"  # propose | review | reject
    reason_code: str | None = None
    review_note: str | None = None
    risk_level: str = "medium"
    quality_score: float = 100.0


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _norm_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = [
            value.get(key)
            for key in (
                "name",
                "benefit",
                "title",
                "value",
                "frequency",
                "category",
                "description",
                "notes",
                "detail",
                "evidence",
                "source_snippet",
            )
        ]
        return " ".join(str(part or "").lower() for part in parts if part not in (None, "", [], {}))
    return " ".join(str(value or "").lower().split())


def _text_tokens(value: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "up",
        "with",
    }
    return {
        token
        for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
        if token and token not in stopwords
    }


def _text_covers(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    if needle == haystack or needle in haystack:
        return True
    needle_tokens = _text_tokens(needle)
    haystack_tokens = _text_tokens(haystack)
    return bool(needle_tokens) and needle_tokens.issubset(haystack_tokens)


def _is_raw_source_fragment(value: Any) -> bool:
    if is_benefit_noise(value):
        return True
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("benefit") or value.get("title") or "").strip()
        if name and len(name) <= 96:
            return False
    low = str(value or "").lower()
    text = str(value or "")
    if "[text]" in low:
        return True
    if len(text) > 220:
        return True
    return any(
        token in low
        for token in (
            "[json-ld]",
            "@context",
            "newsarticle",
            "articlesection",
            "schema.org",
            "[title]",
            "[meta]",
            "best current credit card",
            "best credit cards for students",
            "breadcrumblist",
            "aggregaterating",
            "credit card members may have the option",
            "pay over time",
            "payment plan",
            "doesn't include",
            "does not include",
            "while we don't cover all available",
            "we don't cover all available",
            "editorial content is not influenced",
            "not influenced by nor subject to review",
            "subject to review by any credit card company",
            "credit card company, bank or partner",
            "our editorial team creates and maintains",
        )
    )


def _benefit_quality(value: Any) -> tuple[float, int, int]:
    if not isinstance(value, list):
        return 0.0, 0, 0
    raw_count = 0
    clean_count = 0
    for item in value:
        if not item:
            continue
        if _is_raw_source_fragment(item):
            raw_count += 1
        else:
            clean_count += 1
    total = raw_count + clean_count
    if total <= 0:
        return 0.0, raw_count, clean_count
    score = (clean_count / total) * 100.0
    if raw_count:
        score -= min(raw_count * 12.5, 40.0)
    return max(0.0, round(score, 2)), raw_count, clean_count


def _category_key(value: Any) -> str:
    key = " ".join(str(value or "").lower().replace("_", " ").split())
    aliases = {
        "restaurants": "dining",
        "restaurant": "dining",
        "gas stations": "gas",
        "gas station": "gas",
        "u s supermarkets": "groceries",
        "us supermarkets": "groceries",
        "supermarkets": "groceries",
        "grocery stores": "groceries",
        "travel booked through chase": "travel",
        "chase travel": "travel",
        "capital one travel": "travel",
        "local transit and commuting": "transit",
    }
    return aliases.get(key, key)


def _merge_ordered_text_lists(old: Any, new: Any) -> Any:
    if not isinstance(old, list) or not isinstance(new, list):
        return new
    merged: list = []
    seen: set[str] = set()
    for item in [*old, *new]:
        if _is_raw_source_fragment(item):
            continue
        key = _norm_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _extends_text_list(old: Any, new: Any) -> bool:
    if not isinstance(old, list) or not isinstance(new, list):
        return False
    old_keys = [_norm_text(item) for item in old if _norm_text(item)]
    new_keys = [_norm_text(item) for item in new if _norm_text(item)]
    if not old_keys or not new_keys:
        return False
    old_covered = all(any(_text_covers(old_key, new_key) for new_key in new_keys) for old_key in old_keys)
    has_added_value = any(not any(_text_covers(new_key, old_key) for old_key in old_keys) for new_key in new_keys)
    return old_covered and has_added_value


def _pending_change_covered(field: str, pending_value: str | None, committed_value: Any, serialized_value: str) -> bool:
    if pending_value == serialized_value:
        return True
    if field != "card_benefits":
        return False
    try:
        pending = json.loads(pending_value or "null")
    except json.JSONDecodeError:
        return False
    if not isinstance(pending, list) or not isinstance(committed_value, list):
        return False
    committed_keys = [_norm_text(item) for item in committed_value if _norm_text(item)]
    pending_keys = [_norm_text(item) for item in pending if _norm_text(item)]
    return bool(pending_keys) and all(
        any(_text_covers(pending_key, committed_key) for committed_key in committed_keys)
        for pending_key in pending_keys
    )


def _settle_pending_changes(
    db: Session,
    product: models.CardProduct,
    field: str,
    value: Any,
    source_url: str | None,
) -> None:
    serialized = json.dumps(value)
    for existing in db.scalars(
        select(models.ProposedChange).where(
            models.ProposedChange.target_table == "card_product",
            models.ProposedChange.target_id == product.id,
            models.ProposedChange.field == field,
            models.ProposedChange.status == "pending",
        )
    ).all():
        if _pending_change_covered(field, existing.new_value, value, serialized):
            existing.status = "approved"
        elif (
            field in {"earn_multipliers", "best_category_uses", "downgrade_paths"}
            and _normalize_url(existing.source_url) == _normalize_url(source_url)
        ):
            existing.status = "rejected"


def _matching_rejected_change_exists(
    db: Session,
    product: models.CardProduct,
    field: str,
    serialized_value: str,
) -> bool:
    """True when the same public product/field/value was already rejected."""
    if not product.id:
        return False
    return db.scalar(
        select(models.ProposedChange.id).where(
            models.ProposedChange.target_table == "card_product",
            models.ProposedChange.target_id == product.id,
            models.ProposedChange.field == field,
            models.ProposedChange.new_value == serialized_value,
            models.ProposedChange.status == "rejected",
        )
    ) is not None


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
    raw = value.strip()
    if not raw:
        return value
    return reward_currency_for_product(product.issuer, product.product_name, raw) or raw


def _decode_json_value(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def classify_proposed_change(
    product: models.CardProduct,
    field: str,
    old_value: Any,
    new_value: Any,
    confidence: float | None = None,
) -> ProposalReview:
    """Classify whether a field change is reviewable or should be rejected.

    This is deliberately conservative. It rejects only structural degradations
    that are known to be bad for this domain: generic co-brand currencies, raw
    source fragments masquerading as benefits, and category maps that collapse
    existing coverage into a strict subset.
    """
    confidence = confidence or 0.0
    if field == "currency" and isinstance(new_value, str):
        expected = reward_currency_for_product(product.issuer, product.product_name, new_value)
        new_low = new_value.strip().lower()
        expected_low = expected.strip().lower() if isinstance(expected, str) and expected.strip() else None
        old_low = old_value.strip().lower() if isinstance(old_value, str) and old_value.strip() else None
        if expected_low and new_low in GENERIC_REWARD_CURRENCIES and new_low != expected_low:
            return ProposalReview(
                action="reject",
                reason_code="generic_currency_degradation",
                review_note=f"Rejected generic currency '{new_value}'; expected {expected}.",
                risk_level="high",
                quality_score=0.0,
            )
        if expected_low and old_low == expected_low and new_low != expected_low:
            return ProposalReview(
                action="reject",
                reason_code="currency_program_regression",
                review_note=f"Rejected currency change away from expected program {expected}.",
                risk_level="high",
                quality_score=0.0,
            )

    if field == "card_benefits":
        score, raw_count, clean_count = _benefit_quality(new_value)
        if raw_count and clean_count == 0:
            return ProposalReview(
                action="reject",
                reason_code="raw_benefit_fragments",
                review_note="Rejected benefits made only of raw source/title/meta fragments.",
                risk_level="high",
                quality_score=score,
            )
        if score < 50.0:
            return ProposalReview(
                action="reject",
                reason_code="low_benefit_quality",
                review_note="Rejected noisy benefit proposal; source text was not structured enough for catalog use.",
                risk_level="high",
                quality_score=score,
            )
        if raw_count and raw_count >= clean_count:
            return ProposalReview(
                action="review",
                reason_code="noisy_benefit_fragments",
                review_note=(
                    f"Contains {raw_count} raw source fragment(s); approve only if cleaned benefits are correct."
                ),
                risk_level="high",
                quality_score=score,
            )
        return ProposalReview(
            action="propose",
            reason_code="benefit_quality_ok" if clean_count else None,
            review_note=None,
            risk_level="medium",
            quality_score=score,
        )

    if field in {"earn_multipliers", "best_category_uses"} and isinstance(old_value, dict) and isinstance(new_value, dict):
        old_keys = {_category_key(key) for key, value in old_value.items() if key and value is not None}
        new_keys = {_category_key(key) for key, value in new_value.items() if key and value is not None}
        if old_keys and new_keys and new_keys < old_keys:
            return ProposalReview(
                action="reject",
                reason_code="category_map_regression",
                review_note="Rejected category map that removed existing categories without replacement.",
                risk_level="high",
                quality_score=25.0,
            )
        if old_keys and new_keys and len(new_keys) < len(old_keys):
            return ProposalReview(
                action="review",
                reason_code="category_map_shrink",
                review_note="New category map removes some existing categories; verify source before approving.",
                risk_level="high",
                quality_score=55.0,
            )

    return ProposalReview(
        action="propose",
        reason_code=None,
        review_note=None,
        risk_level="medium" if confidence < HIGH_CONFIDENCE else "low",
        quality_score=100.0,
    )


def _review_for_existing_change(db: Session, change: models.ProposedChange) -> ProposalReview | None:
    if change.target_table != "card_product":
        return None
    product = db.get(models.CardProduct, change.target_id)
    if product is None:
        return None
    return classify_proposed_change(
        product,
        change.field,
        _decode_json_value(change.old_value),
        _decode_json_value(change.new_value),
        change.confidence,
    )


def cleanup_bad_pending_changes(db: Session) -> dict:
    """Reject pending proposals that now fail hard quality gates."""
    rejected: list[int] = []
    reviewed: list[int] = []
    for change in db.scalars(
        select(models.ProposedChange).where(models.ProposedChange.status == "pending")
    ).all():
        review = _review_for_existing_change(db, change)
        if review is None:
            continue
        change.reason_code = review.reason_code or change.reason_code
        change.review_note = review.review_note or change.review_note
        change.risk_level = review.risk_level or change.risk_level
        change.quality_score = review.quality_score
        reviewed.append(change.id)
        if review.action == "reject":
            change.status = "rejected"
            rejected.append(change.id)
    db.commit()
    return {"reviewed_count": len(reviewed), "rejected_count": len(rejected), "rejected_ids": rejected}


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


def _offer_evidence_text(ext: OfferExtraction, *field_names: str) -> str:
    evidence = ext.evidence_snippets or {}
    pieces: list[str] = []
    for field_name in field_names:
        value = evidence.get(field_name)
        if isinstance(value, list):
            pieces.extend(str(item or "") for item in value)
        elif value:
            pieces.append(str(value))
    return " ".join(pieces).lower()


def _all_evidence_text(ext: OfferExtraction) -> str:
    pieces = [
        ext.source_url,
        ext.product_url,
        getattr(ext, "issuer", None),
        getattr(ext, "card_name", None),
        ext.eligibility_language,
        ext.needs_review_reason,
    ]
    for snippets in (ext.evidence_snippets or {}).values():
        if isinstance(snippets, list):
            pieces.extend(str(item or "") for item in snippets)
        elif snippets:
            pieces.append(str(snippets))
    return " ".join(str(part or "") for part in pieces)


def _has_field_evidence(ext: OfferExtraction, field: str) -> bool:
    evidence = ext.evidence_snippets or {}
    for key in FIELD_EVIDENCE_ALIASES.get(field, (field,)):
        value = evidence.get(key)
        if isinstance(value, list) and any(str(item or "").strip() for item in value):
            return True
        if value and str(value).strip():
            return True
    return False


def _trusted_official_auto_commit(
    product: models.CardProduct,
    ext: OfferExtraction,
    source_url: str,
    field: str,
    confidence: float,
) -> bool:
    if field not in OFFICIAL_AUTO_COMMIT_FIELDS:
        return False
    if confidence < OFFICIAL_AUTO_COMMIT_CONFIDENCE:
        return False
    if field in CURRENT_OFFER_FIELDS and ext.offer_status != "public":
        return False
    if not _has_field_evidence(ext, field):
        return False
    return source_quality.is_verified_auto_adopt_source(
        product.issuer,
        product.product_name,
        source_url or ext.source_url or ext.product_url,
        _all_evidence_text(ext),
    )


def _looks_like_non_welcome_bonus(ext: OfferExtraction, fields: dict) -> bool:
    if not fields.get("current_offer_points"):
        return False
    text = _offer_evidence_text(ext, "current_offer_points", "bonus_amount", "peak_offer_points")
    if not text:
        return False
    non_welcome_terms = (
        "anniversary",
        "cardholder anniversary",
        "account anniversary",
        "annual bonus miles",
        "retention",
    )
    welcome_terms = (
        "new card",
        "new applicant",
        "new venture",
        "after spending",
        "after you spend",
        "spend $",
        "within the first",
        "from account opening",
    )
    if any(term in text for term in non_welcome_terms) and not any(term in text for term in welcome_terms):
        return True

    # Amex/airline-style rebate caps often say "up to N points back".
    # Those are card benefits, not public welcome-offer amounts.
    rebate_cap_terms = (
        "points back",
        "pay with points",
        "airline bonus",
        "qualifying airline",
        "american express travel",
        "per calendar",
    )
    return "points back" in text and any(term in text for term in rebate_cap_terms if term != "points back")


def _normalize_offer_payload(
    product: models.CardProduct,
    ext: OfferExtraction,
    source_url: str,
    fields: dict,
) -> dict:
    fields = dict(fields)

    # Free-text fields carry scraped copy — sanitize before any write path.
    # (card_benefits goes through normalize_public_benefits separately.)
    if isinstance(fields.get("best_category_uses"), dict):
        cleaned_uses: dict = {}
        for key, value in fields["best_category_uses"].items():
            cat = clean_text(key).lower().replace(" ", "_")
            note = clean_text(value)
            if not cat or not note or len(note) > 120 or looks_like_scrape_junk(note):
                continue
            cleaned_uses[cat] = note
        if cleaned_uses:
            fields["best_category_uses"] = cleaned_uses
        else:
            fields.pop("best_category_uses", None)
    if isinstance(fields.get("downgrade_paths"), list):
        cleaned_paths = []
        for path in fields["downgrade_paths"]:
            cleaned = clean_text(path)
            if cleaned and len(cleaned) <= 120 and not looks_like_scrape_junk(cleaned):
                cleaned_paths.append(cleaned)
        if cleaned_paths:
            fields["downgrade_paths"] = cleaned_paths
        else:
            fields.pop("downgrade_paths", None)

    if "currency" in fields:
        fields["currency"] = _normalize_currency(product, fields["currency"])
    elif isinstance(product.currency, str) and product.currency.strip().lower() in GENERIC_REWARD_CURRENCIES:
        normalized_currency = reward_currency_for_product(product.issuer, product.product_name, product.currency)
        if normalized_currency and normalized_currency.strip().lower() != product.currency.strip().lower():
            fields["currency"] = normalized_currency

    for source_field in ("peak_offer_source", "targeted_peak_offer_source"):
        if source_field in fields:
            fields[source_field] = _normalize_url(fields[source_field])

    source = _normalize_url(source_url)
    if source_quality.has_variant_conflict(
        product.issuer,
        product.product_name,
        f"{source_url or ''} {ext.source_url or ''} {ext.product_url or ''} {_all_evidence_text(ext)}",
    ):
        fields = {
            field: value
            for field, value in fields.items()
            if field not in (_OFFER_WRITE_FIELDS | SUPPLEMENTAL_WRITE_FIELDS)
        }

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

    if _looks_like_non_welcome_bonus(ext, fields):
        removed_points = fields.pop("current_offer_points", None)
        fields.pop("current_offer_min_spend", None)
        fields.pop("current_offer_window_months", None)
        if removed_points and fields.get("peak_offer_points") == removed_points:
            fields.pop("peak_offer_points", None)
            fields.pop("peak_offer_source", None)

    if _is_non_offer_source(source_url):
        fields = {
            field: value
            for field, value in fields.items()
            if field not in _OFFER_WRITE_FIELDS
        }

    current_points = fields.get("current_offer_points")
    if (
        current_points
        and product.peak_offer_points
        and current_points > product.peak_offer_points
        and not fields.get("peak_offer_points")
        and ext.offer_status == "public"
        and source_quality.is_verified_auto_adopt_source(
            product.issuer,
            product.product_name,
            source or ext.source_url or ext.product_url,
            _all_evidence_text(ext),
        )
    ):
        # A current public offer above an already-verified public peak is itself
        # the new public high-water mark. Do not create first-sight peaks from a
        # current offer when no prior public peak exists.
        fields["peak_offer_points"] = int(current_points)
        fields["peak_offer_source"] = source or ext.source_url or ext.product_url

    if fields.get("peak_offer_points") and not fields.get("peak_offer_min_spend"):
        spend = fields.get("current_offer_min_spend") or product.current_offer_min_spend
        if spend:
            fields["peak_offer_min_spend"] = spend
    return fields


def _decision(field: str, old, new, confidence: float, *, trusted_auto_commit: bool = False) -> str:
    """Return 'commit', 'propose', or 'skip'."""
    if _empty(old):
        if confidence < FIRST_SIGHT_FIELD_CONFIDENCE.get(field, 0.65):
            return "propose"
        return "commit"  # first sight — nothing to overwrite
    if old == new:
        return "skip"
    if (
        field == "currency"
        and isinstance(old, str)
        and isinstance(new, str)
        and old.strip().lower() in GENERIC_REWARD_CURRENCIES
        and new.strip().lower() not in GENERIC_REWARD_CURRENCIES
    ):
        return "commit"
    if field in ELIG_FIELDS:
        return "propose"  # eligibility-rule change always reviewed
    # Peak is durable history: raise freely, but review any decrease.
    if field in PEAK_FIELDS and isinstance(old, (int, float)) and isinstance(new, (int, float)):
        if new > old:
            return "commit"
        return "propose"
    if trusted_auto_commit:
        return "commit"
    if field in OFFER_FIELDS and isinstance(old, (int, float)) and isinstance(new, (int, float)):
        delta = abs(new - old) / max(abs(old), 1)
        if delta > config.OFFER_DELTA_THRESHOLD:
            return "propose"
        # small change
        if config.AUTO_COMMIT_SMALL_CHANGES and confidence >= HIGH_CONFIDENCE:
            return "commit"
        return "propose"
    if field == "card_benefits" and _extends_text_list(old, new):
        if confidence >= FIRST_SIGHT_FIELD_CONFIDENCE.get(field, 0.75):
            return "commit"
        return "propose"
    if field in {"earn_multipliers", "best_category_uses", "downgrade_paths"} and confidence >= HIGH_CONFIDENCE:
        return "commit"
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
        "card_benefits": _jsonable(ext.card_benefits),
        "downgrade_paths": ext.downgrade_paths,
        "first_year_credit_value": ext.first_year_credit_value,
        "eligibility_tags": ext.eligibility_tags,
        "reports_to_personal_credit": ext.reports_to_personal_credit,
    }
    if ext.earn_multipliers:
        fields["earn_multipliers"] = {m.category: m.multiplier for m in ext.earn_multipliers}
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
    known_facts = normalize_known_public_facts(product.issuer, product.product_name, normalized_source)
    known_fact_fields = set(known_facts or {})
    if known_facts:
        new_fields.update({field: value for field, value in known_facts.items() if value not in (None, "", [], {})})
    if "card_benefits" in new_fields:
        merged_benefits = _merge_ordered_text_lists(
            product.card_benefits,
            new_fields["card_benefits"],
        )
        normalized_benefits = normalize_public_benefits(
            product.issuer,
            product.product_name,
            normalized_source,
            merged_benefits,
            allow_reference=False,
        )
        if normalized_benefits:
            new_fields["card_benefits"] = normalized_benefits
        else:
            new_fields.pop("card_benefits", None)
    evidence_recorded = _record_evidence(db, product, ext, source_url, new_fields)

    committed: list[str] = []
    proposed: list[str] = []
    rejected: list[str] = []

    for field, new_value in new_fields.items():
        old_value = getattr(product, field, None)
        review = classify_proposed_change(product, field, old_value, new_value, confidence)
        if review.action == "reject":
            rejected.append(field)
            continue
        trusted_auto_commit = _trusted_official_auto_commit(
            product,
            ext,
            normalized_source,
            field,
            confidence,
        )
        decision = _decision(
            field,
            old_value,
            new_value,
            confidence,
            trusted_auto_commit=trusted_auto_commit,
        )
        if review.action == "review" and decision == "commit":
            decision = "propose"
        if decision == "skip":
            _settle_pending_changes(db, product, field, old_value, normalized_source)
            continue
        if (
            decision == "commit"
            and field in FIELD_EVIDENCE_REQUIRED_FOR_COMMIT
            and field not in known_fact_fields
            and not _has_field_evidence(ext, field)
        ):
            decision = "propose"
        if decision == "commit":
            setattr(product, field, new_value)
            _settle_pending_changes(db, product, field, new_value, normalized_source)
            committed.append(field)
        else:  # propose
            serialized = json.dumps(new_value)
            if _matching_rejected_change_exists(db, product, field, serialized):
                rejected.append(field)
                continue
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
                        reason_code=review.reason_code,
                        review_note=review.review_note,
                        risk_level=review.risk_level,
                        quality_score=review.quality_score,
                        status="pending",
                    )
                )
            proposed.append(field)

    # A fresh public current offer supersedes any manual targeted override.
    if "current_offer_points" in committed and product.current_offer_override is not None:
        product.current_offer_override = None

    # Provenance: every touched product records where + when it was verified.
    if committed:
        if source_quality.is_safe_product_source(
            product.issuer,
            product.product_name,
            normalized_source,
            _all_evidence_text(ext),
        ):
            product.source_url = normalized_source
        product.last_verified = _utcnow()
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "committed": committed,
        "proposed": proposed,
        "rejected": rejected,
        "evidence_recorded": evidence_recorded,
    }


# --- Review-queue actions ---------------------------------------------------
def _coerce(field: str, value):
    return value  # values are stored JSON-typed, so json.loads already gives the right type


PEAK_TRUSTED_HOSTS = (
    "doctorofcredit.com",
    "frequentmiler.com",
    "uscreditcardguide.com",
    "thepointsguy.com",
)

_PLAUSIBLE_RANGES = {
    "annual_fee": (0, 2500),
    "current_offer_points": (1000, 500000),
    "current_offer_cash": (25, 3000),
    "current_offer_min_spend": (0, 50000),
    "current_offer_window_months": (1, 12),
    "peak_offer_points": (1000, 500000),
    "targeted_peak_offer_points": (1000, 500000),
    "referral_bonus_points": (1000, 100000),
    "referral_bonus_cash": (25, 1000),
    "first_year_credit_value": (0, 3000),
}


def _plausible(field: str, value) -> bool:
    bounds = _PLAUSIBLE_RANGES.get(field)
    if bounds is None:
        return True
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    low, high = bounds
    return low <= number <= high


def _evidence_backs_value(
    db: Session,
    product_id: int,
    field: str,
    serialized_value: str,
    *,
    trusted_hosts: tuple[str, ...] | None = None,
    max_age_days: int = 60,
) -> bool:
    """True when a recent ingestion_evidence row records this exact value for
    the field (or a scan alias), optionally restricted to trusted hosts."""
    aliases = set(FIELD_EVIDENCE_ALIASES.get(field, (field,))) | {field}
    rows = db.scalars(
        select(models.IngestionEvidence).where(
            models.IngestionEvidence.product_id == product_id,
            models.IngestionEvidence.field.in_(sorted(aliases)),
        )
    ).all()
    cutoff = _utcnow() - dt.timedelta(days=max_age_days)
    for row in rows:
        if row.value_json != serialized_value:
            continue
        if (row.confidence or 0) < 0.6:
            continue
        created = row.created_at or cutoff
        if created < cutoff:
            continue
        if trusted_hosts:
            host = (urlparse(row.source_url or "").hostname or "").lower().removeprefix("www.")
            issuer_official = source_quality.is_official_issuer_host(host) if hasattr(source_quality, "is_official_issuer_host") else False
            if not (host.endswith(trusted_hosts) or issuer_official):
                continue
        return True
    return False


def auto_resolve_pending_changes(db: Session, *, commit: bool = True) -> dict:
    """The system reviews its own queue so the household never has to.

    Approval requires the same things a careful human checks: recent recorded
    evidence for the exact value, plausible magnitude, and for PEAK fields a
    peak-trusted or official source plus peak >= the known current offer.
    Structured text fields (benefits/uses/multipliers/downgrades) approve on
    the strength of the hardened normalization gates that already run at
    apply time. Anything that fails a hard check is rejected with a note;
    genuinely unverifiable rows stay pending (rare).
    """
    pending = db.scalars(
        select(models.ProposedChange).where(
            models.ProposedChange.target_table == "card_product",
            models.ProposedChange.status == "pending",
        )
    ).all()
    approved: list[dict] = []
    rejected: list[dict] = []
    kept: list[dict] = []
    for change in pending:
        product = db.get(models.CardProduct, change.target_id)
        if product is None:
            change.status = "rejected"
            change.review_note = "Product no longer exists."
            rejected.append({"id": change.id, "field": change.field, "reason": "orphaned"})
            continue
        field = change.field
        try:
            value = json.loads(change.new_value) if change.new_value is not None else None
        except (TypeError, json.JSONDecodeError):
            reject_change(db, change)
            change.review_note = "Unparseable proposed value."
            rejected.append({"id": change.id, "field": field, "reason": "unparseable"})
            continue

        display = product_display_name_safe(product)
        # 1. Hard plausibility gate.
        if not _plausible(field, value) and field in _PLAUSIBLE_RANGES:
            reject_change(db, change)
            change.review_note = f"Auto-rejected: {value!r} outside plausible range for {field}."
            rejected.append({"id": change.id, "product": display, "field": field, "reason": "implausible"})
            continue

        # 2. Peak accuracy rules (the reference scale must be right).
        if field in PEAK_FIELDS:
            current = product.current_offer_effective or product.current_offer_points or 0
            if current and isinstance(value, (int, float)) and value < current:
                reject_change(db, change)
                change.review_note = (
                    f"Auto-rejected: proposed peak {value} is below the current offer {current}."
                )
                rejected.append({"id": change.id, "product": display, "field": field, "reason": "peak_below_current"})
                continue
            if _evidence_backs_value(db, product.id, field, change.new_value, trusted_hosts=PEAK_TRUSTED_HOSTS):
                approve_change(db, change)
                approved.append({"id": change.id, "product": display, "field": field, "basis": "trusted_peak_evidence"})
            else:
                kept.append({"id": change.id, "product": display, "field": field, "reason": "peak_needs_trusted_evidence"})
            continue

        # 3. Offer/fee/referral numbers: recent recorded evidence for the value.
        if field in OFFER_FIELDS or field in ("annual_fee", "first_year_credit_value", "peak_offer_min_spend"):
            if _evidence_backs_value(db, product.id, field, change.new_value):
                approve_change(db, change)
                approved.append({"id": change.id, "product": display, "field": field, "basis": "recorded_evidence"})
            else:
                kept.append({"id": change.id, "product": display, "field": field, "reason": "no_matching_evidence"})
            continue

        # 4. Structured text fields: the hardened gates ARE the verification.
        if field in SUPPLEMENTAL_WRITE_FIELDS or field in (
            "peak_offer_source",
            "peak_offer_date",
            "targeted_peak_offer_source",
            "targeted_peak_offer_date",
            "currency",
        ):
            approve_change(db, change)
            approved.append({"id": change.id, "product": display, "field": field, "basis": "normalization_gates"})
            continue

        # 5. Eligibility and anything else unrecognized stays for the rare
        # human look — these change recommendation legality.
        kept.append({"id": change.id, "product": display, "field": field, "reason": "manual_domain"})

    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "approved": approved,
        "rejected": rejected,
        "pending": kept,
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "pending_count": len(kept),
    }


def product_display_name_safe(product: models.CardProduct) -> str:
    try:
        from ..product_identity import product_display_name

        return product_display_name(product.issuer, product.product_name) or product.product_name
    except Exception:
        return product.product_name


def approve_change(db: Session, change: models.ProposedChange) -> None:
    if change.status != "pending":
        return
    review = _review_for_existing_change(db, change)
    if review and review.action == "reject":
        change.reason_code = review.reason_code
        change.review_note = review.review_note
        change.risk_level = review.risk_level
        change.quality_score = review.quality_score
        change.status = "rejected"
        db.commit()
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
    if not change.reason_code:
        change.reason_code = "user_rejected"
    change.status = "rejected"
    db.commit()
