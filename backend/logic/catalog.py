"""Shared catalog helpers: effective catalog, valuations, and scored catalog.

Used by the Card Plan tab and the Application Pipeline so both see the same
(discovered ∪ watchlisted) − blacklisted view with identical scoring.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import config, models, source_quality
from ..benefit_normalization import is_protection_benefit, normalize_public_benefits
from ..crypto import MissingKeyError
from ..ingestion.validate import FIELD_EVIDENCE_ALIASES, FIRST_SIGHT_FIELD_CONFIDENCE
from ..product_identity import canonical_product_key, derive_product_family, product_display_name, product_variant_key
from . import eligibility as elig
from . import scoring

if TYPE_CHECKING:
    from .decision_context import DecisionContext

CRITICAL_DECISION_FIELDS = {
    "annual_fee",
    "currency",
    "current_offer_points",
    "current_offer_cash",
    "current_offer_min_spend",
    "current_offer_window_months",
    "offer_expiration",
}

_CURRENT_OFFER_VALUE_FIELDS = (
    "current_offer_points",
    "current_offer_cash",
    "current_offer_min_spend",
    "current_offer_window_months",
)
_UNKNOWN_EXPIRATION_VALUES = {"unknown", "unspecified", "n/a", "na", "none", "not stated"}


def valuation_map(db: Session) -> dict[str, float]:
    """{currency_lower: effective cpp}."""
    out: dict[str, float] = {}
    for v in db.scalars(select(models.Valuation)).all():
        if v.currency and v.cpp_effective is not None:
            out[v.currency.strip().lower()] = v.cpp_effective
    return out


def blacklisted_keys(db: Session) -> set[tuple[str, str]]:
    return {
        ((b.issuer or "").strip().lower(), (b.product_name or "").strip().lower())
        for b in db.scalars(select(models.CardBlacklist)).all()
    }


def _catalog_identity_key(p: models.CardProduct) -> tuple[str, str, str]:
    variant = product_variant_key(p.issuer, p.product_name)
    if variant:
        return ("variant", variant[0], variant[1])
    return (
        "exact",
        (p.issuer or "").strip().lower(),
        (p.product_name or "").strip().lower(),
    )


def _product_completeness_score(p: models.CardProduct) -> tuple:
    data_points = sum(
        1
        for value in (
            p.current_offer_effective,
            p.current_offer_cash,
            p.current_offer_min_spend,
            p.peak_offer_points,
            p.annual_fee,
            p.referral_bonus_effective,
            p.referral_bonus_cash,
            p.earn_multipliers,
            p.best_category_uses,
            p.card_benefits,
            p.eligibility_tags,
            p.currency,
            p.source_url,
        )
        if value not in (None, "", [], {})
    )
    issuer = (p.issuer or "").strip().lower()
    financial_issuer = int(
        issuer in {"american express", "amex", "chase", "capital one", "citi", "bank of america", "wells fargo", "u.s. bank"}
    )
    return (
        data_points,
        int(bool(p.last_verified)),
        int(bool(p.discovery_reviewed)),
        financial_issuer,
        -(p.id or 0),
    )


def dedupe_products_by_variant(products: list[models.CardProduct]) -> list[models.CardProduct]:
    """Collapse same-card name/issuer variants for decision and display paths."""
    chosen: dict[tuple[str, str, str], models.CardProduct] = {}
    for product in products:
        key = _catalog_identity_key(product)
        current = chosen.get(key)
        if current is None or _product_completeness_score(product) > _product_completeness_score(current):
            chosen[key] = product
    return sorted(chosen.values(), key=lambda p: ((p.issuer or ""), (p.product_name or ""), p.id or 0))


def effective_catalog(db: Session) -> list[models.CardProduct]:
    """All catalog products minus blacklisted and same-variant duplicates."""
    bl = blacklisted_keys(db)
    products = db.scalars(select(models.CardProduct)).all()
    filtered = [
        p
        for p in products
        if ((p.issuer or "").strip().lower(), (p.product_name or "").strip().lower())
        not in bl
    ]
    return dedupe_products_by_variant(filtered)


def _offer_evidence_by_product(
    db: Session,
    product_ids: list[int],
) -> dict[int, list[models.IngestionEvidence]]:
    """Load all public evidence needed by a catalog projection in one query."""
    if not product_ids:
        return {}
    rows = list(
        db.scalars(
            select(models.IngestionEvidence).where(
                models.IngestionEvidence.product_id.in_(product_ids)
            )
        ).all()
    )
    out: dict[int, list[models.IngestionEvidence]] = {}
    for row in rows:
        out.setdefault(row.product_id, []).append(row)
    return out


def _verified_status(p: models.CardProduct) -> str:
    if not p.source_url or not p.last_verified:
        return "needs_source"
    age_days = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - p.last_verified).days
    if age_days > 30:
        return "stale"
    return "verified"


def _pending_fields(db: Session) -> dict[int, set[str]]:
    rows = db.execute(
        select(models.ProposedChange.target_id, models.ProposedChange.field).where(
            models.ProposedChange.target_table == "card_product",
            models.ProposedChange.status == "pending",
        )
    ).all()
    out: dict[int, set[str]] = {}
    for target_id, field in rows:
        out.setdefault(target_id, set()).add(field)
    return out


def _parse_evidence_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = dt.date.fromisoformat(raw)
        except ValueError:
            return None
        return dt.datetime.combine(parsed_date, dt.time.min)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_offer_expiration(value: str | None) -> tuple[dt.date | None, str]:
    """Return (date, state) without inventing a date for ambiguous text."""
    raw = str(value or "").strip()
    if not raw or raw.lower() in _UNKNOWN_EXPIRATION_VALUES:
        return None, "unknown"
    candidates = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m")
    for fmt in candidates:
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        if fmt == "%Y-%m":
            next_month = parsed.replace(day=28) + dt.timedelta(days=4)
            parsed = next_month - dt.timedelta(days=next_month.day)
        return parsed, "known"
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None, "invalid"
    return parsed, "known"


def _evidence_text(row: models.IngestionEvidence) -> str:
    snippets = row.evidence_snippets
    if isinstance(snippets, dict):
        values = snippets.values()
    elif isinstance(snippets, list):
        values = [snippets]
    else:
        values = []
    return " ".join(
        str(item or "")
        for group in values
        for item in (group if isinstance(group, list) else [group])
    )


def _evidence_json_value(row: models.IngestionEvidence):
    try:
        return json.loads(row.value_json) if row.value_json is not None else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def current_offer_quality(
    product: models.CardProduct,
    evidence_rows: list[models.IngestionEvidence] | None,
    as_of: dt.date,
) -> dict:
    """Evaluate persisted public terms against fresh, field-level evidence.

    ``CardProduct.last_verified`` is deliberately not consulted here.  It is a
    product-level scrape marker and cannot prove that each current amount,
    spend term, or expiration is still the value being displayed.  Evidence
    freshness is based on ``fetched_at`` and the supplied planning date;
    ``created_at`` is never used to rejuvenate an old fetch.
    """
    rows = list(evidence_rows or [])
    # Planning/expiry dates are local calendar dates; fetched_at is normalized
    # UTC. Compare timestamps in UTC after translating the local day boundary.
    as_of_dt = dt.datetime.combine(as_of, dt.time.max).astimezone(dt.timezone.utc).replace(tzinfo=None)
    cutoff = as_of_dt - dt.timedelta(days=config.DECISION_FRESH_DAYS)

    by_field: dict[str, list[models.IngestionEvidence]] = {}
    for row in rows:
        by_field.setdefault(row.field or "", []).append(row)

    def field_rows(field: str) -> list[models.IngestionEvidence]:
        aliases = set(FIELD_EVIDENCE_ALIASES.get(field, (field,))) | {field}
        return [row for alias in aliases for row in by_field.get(alias, [])]

    def has_field_quality(field: str, row: models.IngestionEvidence) -> bool:
        """Keep the decision reader aligned with ingestion's first-sight gate."""
        threshold = FIRST_SIGHT_FIELD_CONFIDENCE.get(field, 0.65)
        return (row.confidence or 0.0) >= threshold and bool(_evidence_text(row).strip())

    def evaluate_field(field: str, expected) -> tuple[bool, str]:
        candidates = field_rows(field)
        if not candidates:
            return False, "missing"
        dated = [_parse_evidence_time(row.fetched_at) for row in candidates]
        fresh_rows = [
            row
            for row, fetched in zip(candidates, dated)
            if fetched is not None and cutoff <= fetched <= as_of_dt
        ]
        if not fresh_rows:
            return False, "stale"
        public_rows = [row for row in fresh_rows if (row.offer_status or "").strip().lower() == "public"]
        if not public_rows:
            return False, "unknown"
        quality_rows = [row for row in public_rows if has_field_quality(field, row)]
        if not quality_rows:
            return False, "unknown"
        safe_rows = [
            row
            for row in quality_rows
            if source_quality.is_safe_product_source(
                product.issuer,
                product.product_name,
                row.source_url,
                _evidence_text(row),
            )
        ]
        if not safe_rows:
            return False, "unknown"
        if any(_evidence_json_value(row) == expected for row in safe_rows):
            return True, "fresh"
        return False, "mismatch"

    required: list[tuple[str, object]] = []
    effective_points = product.current_offer_effective
    if effective_points is not None:
        required.append(("current_offer_points", effective_points))
    if product.current_offer_cash is not None:
        required.append(("current_offer_cash", product.current_offer_cash))
    if product.current_offer_min_spend is not None:
        required.append(("current_offer_min_spend", product.current_offer_min_spend))
    if product.current_offer_window_months is not None:
        required.append(("current_offer_window_months", product.current_offer_window_months))

    failures: list[tuple[str, str]] = []
    fetched_values: list[dt.datetime] = []
    for field, expected in required:
        matched, state = evaluate_field(field, expected)
        if matched:
            for row in field_rows(field):
                fetched = _parse_evidence_time(row.fetched_at)
                if (
                    fetched is not None
                    and cutoff <= fetched <= as_of_dt
                    and (row.offer_status or "").strip().lower() == "public"
                    and _evidence_json_value(row) == expected
                ):
                    fetched_values.append(fetched)
        else:
            failures.append((field, state))

    expiration_date, expiration_state = _parse_offer_expiration(product.offer_expiration)
    if expiration_state == "invalid":
        failures.append(("offer_expiration", "invalid"))
    elif expiration_state == "known":
        if expiration_date is not None and expiration_date < as_of:
            failures.append(("offer_expiration", "expired"))
        else:
            matched, state = evaluate_field("offer_expiration", product.offer_expiration)
            if matched:
                for row in field_rows("offer_expiration"):
                    fetched = _parse_evidence_time(row.fetched_at)
                    if (
                        fetched is not None
                        and cutoff <= fetched <= as_of_dt
                        and (row.offer_status or "").strip().lower() == "public"
                        and _evidence_json_value(row) == product.offer_expiration
                    ):
                        fetched_values.append(fetched)
            else:
                failures.append(("offer_expiration", state))

    if not required:
        failures.append(("current_offer", "missing"))

    state_priority = {
        "expired": 0,
        "invalid": 1,
        "mismatch": 2,
        "stale": 3,
        "missing": 4,
        "unknown": 5,
    }
    if failures:
        primary_field, primary_state = sorted(
            failures,
            key=lambda item: (state_priority.get(item[1], 99), item[0]),
        )[0]
        status = {
            "expired": "expired",
            "invalid": "invalid_expiration",
            "mismatch": "current_offer_evidence_mismatch",
            "stale": "current_offer_evidence_stale",
            "missing": "current_offer_evidence_missing",
            "unknown": "current_offer_evidence_unknown",
        }.get(primary_state, "current_offer_unknown")
        detail = ", ".join(f"{field}: {state}" for field, state in failures)
        return {
            "ready": False,
            "status": status,
            "reason": f"Current public offer evidence is not ready ({detail}).",
            "expiration": product.offer_expiration,
            "expiration_status": "expired" if primary_state == "expired" else expiration_state,
            "evidence_fetched_at": max(fetched_values).isoformat() if fetched_values else None,
        }

    return {
        "ready": True,
        "status": "fresh_unknown_expiration" if expiration_state == "unknown" else "fresh",
        "reason": (
            "Current public offer terms match fresh product evidence; expiration is unknown."
            if expiration_state == "unknown"
            else "Current public offer terms match fresh product evidence."
        ),
        "expiration": product.offer_expiration,
        "expiration_status": expiration_state,
        "evidence_fetched_at": max(fetched_values).isoformat() if fetched_values else None,
    }


def _decision_quality_issues(
    p: models.CardProduct,
    pending_fields: set[str] | None = None,
    offer_quality: dict | None = None,
) -> list[str]:
    issues: list[str] = []
    pending_fields = pending_fields or set()
    pending_critical = sorted(CRITICAL_DECISION_FIELDS & pending_fields)
    if pending_critical:
        issues.append("pending_verified_update")
    source_issue = source_quality.source_quality_issue(p.issuer, p.product_name, p.source_url)
    if source_issue:
        issues.append(source_issue)
    # Fresh field-level evidence is authoritative for current terms.  Preserve
    # the legacy product-level checks when no ready evidence projection exists,
    # so old/reference rows remain visibly unverified rather than being silently
    # promoted by a stale product marker.
    if not offer_quality or not offer_quality.get("ready"):
        if not p.last_verified:
            issues.append("never_verified")
        else:
            age_days = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - p.last_verified).days
            if age_days > config.DECISION_FRESH_DAYS:
                issues.append("stale_verified_data")
    if offer_quality and not offer_quality.get("ready"):
        quality_status = offer_quality.get("status")
        if quality_status and quality_status not in issues:
            issues.append(quality_status)
    if not (p.current_offer_effective or p.current_offer_cash):
        issues.append("missing_current_offer")
    if not p.peak_offer_points:
        issues.append("missing_public_peak")
    if p.annual_fee is None:
        issues.append("missing_annual_fee")
    if not p.currency:
        issues.append("missing_currency")
    if p.current_offer_effective:
        if p.current_offer_min_spend is None:
            issues.append("missing_min_spend")
        if p.current_offer_window_months is None:
            issues.append("missing_spend_window")
    return list(dict.fromkeys(issues))


def _blocks_apply_decision(issues: list[str]) -> bool:
    blocking = {
        "pending_verified_update",
        "source_identity_conflict",
        "broad_source_not_product_truth",
        "source_not_product_specific",
        "never_verified",
        "stale_verified_data",
        "missing_current_offer",
        "missing_annual_fee",
        "missing_currency",
        "missing_min_spend",
        "missing_spend_window",
        "expired",
        "invalid_expiration",
        "current_offer_evidence_mismatch",
        "current_offer_evidence_stale",
        "current_offer_evidence_missing",
        "current_offer_evidence_unknown",
    }
    return any(issue in blocking for issue in issues)


def _display_benefits(p: models.CardProduct) -> tuple[list | None, list[str]]:
    """(spendable/trackable benefits, protection names).

    Coverage/insurance benefits are real but not actionable — they render as
    a compact Coverage line in card details, never in the credits tracker.
    """
    rows = normalize_public_benefits(
        p.issuer,
        p.product_name,
        p.source_url,
        p.card_benefits,
        allow_reference=_verified_status(p) == "verified",
    )
    if not rows:
        return None, []
    trackable: list = []
    protections: list[str] = []
    for row in rows:
        if is_protection_benefit(row):
            name = row.get("name") if isinstance(row, dict) else str(row)
            if name:
                protections.append(str(name))
        else:
            trackable.append(row)
    return (trackable or None), protections


def product_to_dict(p: models.CardProduct) -> dict:
    benefits, protections = _display_benefits(p)
    return {
        "id": p.id,
        "issuer": p.issuer,
        "product_name": p.product_name,
        "display_name": product_display_name(p.issuer, p.product_name),
        "canonical_key": canonical_product_key(p.issuer, p.product_name),
        "product_family": p.product_family,
        "ownership": p.ownership,
        "account_type": p.account_type,
        "currency": p.currency,
        "reports_to_personal_credit": p.reports_to_personal_credit,
        "annual_fee": p.annual_fee,
        "current_offer_points": p.current_offer_points,
        "current_offer_override": p.current_offer_override,
        "current_offer_effective": p.current_offer_effective,
        "current_offer_cash": p.current_offer_cash,
        "current_offer_min_spend": p.current_offer_min_spend,
        "current_offer_window_months": p.current_offer_window_months,
        "offer_expiration": p.offer_expiration,
        "peak_offer_points": p.peak_offer_points,
        "peak_offer_min_spend": p.peak_offer_min_spend,
        "peak_offer_source": p.peak_offer_source,
        "peak_offer_date": p.peak_offer_date,
        "peak_offer_effective": p.peak_offer_effective,
        "targeted_peak_offer_points": p.targeted_peak_offer_points,
        "targeted_peak_offer_cash": p.targeted_peak_offer_cash,
        "targeted_peak_offer_source": p.targeted_peak_offer_source,
        "targeted_peak_offer_date": p.targeted_peak_offer_date,
        "referral_bonus_points": p.referral_bonus_points,
        "referral_bonus_override": p.referral_bonus_override,
        "referral_bonus_effective": p.referral_bonus_effective,
        "referral_bonus_cash": p.referral_bonus_cash,
        "first_year_credit_value": p.first_year_credit_value,
        "earn_multipliers": p.earn_multipliers,
        "best_category_uses": p.best_category_uses,
        "card_benefits": benefits,
        "card_protections": protections,
        "downgrade_paths": p.downgrade_paths,
        "eligibility_tags": p.eligibility_tags,
        "tag": p.tag,
        "added_by": p.added_by,
        "discovery_reviewed": p.discovery_reviewed,
        "source_url": p.source_url,
        "last_verified": p.last_verified.isoformat() if p.last_verified else None,
        "last_web_search_at": p.last_web_search_at.isoformat() if p.last_web_search_at else None,
        "last_supplemental_search_at": p.last_supplemental_search_at.isoformat() if p.last_supplemental_search_at else None,
        "notes": p.notes,
    }


def _key(issuer: str | None, name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (name or "").strip().lower())


def find_product_by_identity(db: Session, issuer: str | None, product_name: str | None) -> models.CardProduct | None:
    """Find an existing product by exact identity or canonical variant."""
    exact = _key(issuer, product_name)
    if exact != ("", ""):
        product = db.scalar(
            select(models.CardProduct).where(
                models.CardProduct.issuer == issuer,
                models.CardProduct.product_name == product_name,
            )
        )
        if product:
            return product
    variant = product_variant_key(issuer, product_name)
    if not variant:
        return None
    for product in db.scalars(select(models.CardProduct)).all():
        if product_variant_key(product.issuer, product.product_name) == variant:
            return product
    return None


def _set_missing_public_identity_fields(product: models.CardProduct, data: dict) -> bool:
    changed = False
    safe_fields = (
        "product_family",
        "ownership",
        "account_type",
        "currency",
        "reports_to_personal_credit",
        "tag",
        "added_by",
        "source_url",
        "notes",
    )
    if not data.get("product_family"):
        data["product_family"] = derive_product_family(data.get("issuer"), data.get("product_name"))
    for field in safe_fields:
        value = data.get(field)
        if getattr(product, field, None) in (None, "", [], {}) and value not in (None, "", [], {}):
            setattr(product, field, value)
            changed = True
    return changed


def upsert_product_by_identity(db: Session, data: dict) -> tuple[models.CardProduct, bool]:
    """Create or reuse a catalog product using canonical variant identity."""
    product = find_product_by_identity(db, data.get("issuer"), data.get("product_name"))
    if product:
        _set_missing_public_identity_fields(product, data)
        return product, False
    payload = dict(data)
    payload["product_family"] = payload.get("product_family") or derive_product_family(
        payload.get("issuer"), payload.get("product_name")
    )
    product = models.CardProduct(**payload)
    db.add(product)
    return product, True


def _targeted_map(
    held: list[models.HeldCard], manual: list[models.ManualTargetedOffer]
) -> dict[tuple[str, str], int]:
    """{(issuer,product): best targeted points} from held cards + manual offers.

    Lets a user record a higher targeted offer even for a card they don't hold.
    """
    out: dict[tuple[str, str], int] = {}

    def _bump(key: tuple[str, str], pts: int | None) -> None:
        if pts:
            out[key] = max(out.get(key, 0), pts)

    for h in held:
        try:
            _bump(_key(h.issuer, h.product_name), h.my_targeted_offer_points)
        except MissingKeyError:
            pass
    for m in manual:
        if m.expires_at and m.expires_at < dt.date.today():
            continue
        try:
            _bump(_key(m.issuer, m.product_name), m.offer_points)
        except MissingKeyError:
            pass
    return out


def _manual_offer_to_dict(offer: models.ManualTargetedOffer | None) -> dict | None:
    if offer is None:
        return None
    try:
        points = offer.offer_points
        cash = offer.offer_cash
    except MissingKeyError:
        points = None
        cash = None
    return {
        "id": offer.id,
        "user": offer.user,
        "issuer": offer.issuer,
        "product_name": offer.product_name,
        "product_id": offer.product_id,
        "offer_points": points,
        "offer_cash": cash,
        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
        "notes": offer.notes,
        "created_at": offer.created_at.isoformat() if offer.created_at else None,
        "updated_at": offer.updated_at.isoformat() if offer.updated_at else None,
    }


def has_known_bonus(p: models.CardProduct) -> bool:
    """True if the card carries (or has ever carried) a welcome bonus or first-year credit."""
    return bool(
        (p.current_offer_effective or 0)
        or (p.current_offer_cash or 0)
        or (p.first_year_credit_value or 0)
        or (p.peak_offer_points or 0)
        or (p.targeted_peak_offer_points or 0)
        or (p.targeted_peak_offer_cash or 0)
    )


def _closed_to_new_applicants(p: models.CardProduct) -> bool:
    tags = {str(tag).strip().lower() for tag in (p.eligibility_tags or [])}
    return "closed_to_new_applicants" in tags or "closed to new applicants" in tags


def _show_in_plan(p: models.CardProduct) -> bool:
    """Hide refreshed cards that have no welcome bonus (now or ever) to cut bloat.

    Unverified and reference-seeded cards are kept so refresh can fill missing
    offer facts. Closed-to-new cards are hidden from new-application planning.
    """
    if _closed_to_new_applicants(p):
        return False
    if has_known_bonus(p):
        return True
    if p.added_by == "reference_seed":
        return True
    return p.last_verified is None


def scored_catalog(
    db: Session,
    user: str,
    held: list[models.HeldCard] | None = None,
    context: "DecisionContext | None" = None,
    as_of: dt.date | None = None,
) -> list[dict]:
    """Catalog entries enriched with per-user eligibility, score, and rank.

    Pass ``held`` to reuse an already-loaded held-card list (e.g. from the
    pipeline) and avoid re-querying it.
    """
    as_of = as_of or dt.date.today()
    vmap = context.valuations if context else valuation_map(db)
    # Load held cards once and reuse across five24 / eligibility / targeted
    # lookups — avoids the N+1 query storm of re-fetching them per product.
    held = held if held is not None else (
        list(context.held_by_user.get(user, [])) if context else elig.held_cards(db, user)
    )
    f24 = elig.five24(db, user, as_of=as_of, held=held)
    manual = (
        [
            offer
            for offer in context.manual_targeted_by_user.get(user, [])
            if offer.expires_at is None or offer.expires_at >= as_of
        ]
        if context
        else list(
            db.scalars(
                select(models.ManualTargetedOffer).where(
                    models.ManualTargetedOffer.user == user,
                    or_(
                        models.ManualTargetedOffer.expires_at.is_(None),
                        models.ManualTargetedOffer.expires_at >= as_of,
                    ),
                )
            ).all()
        )
    )
    targeted_map = _targeted_map(held, manual)
    manual_map = {_key(m.issuer, m.product_name): m for m in manual}
    products = [p for p in (context.products if context else effective_catalog(db)) if _show_in_plan(p)]
    pending_by_id = _pending_fields(db)
    evidence_by_product = (
        context.offer_evidence_by_product
        if context is not None
        else _offer_evidence_by_product(db, [p.id for p in products if p.id])
    )

    entries: list[dict] = []
    for p in products:
        e = elig.eligibility(
            db, user, p.issuer, p.product_name,
            ownership=p.ownership, as_of=as_of, f24=f24, held=held,
            eligibility_tags=p.eligibility_tags,
        ).to_dict()
        targeted = targeted_map.get(_key(p.issuer, p.product_name))
        manual_offer = manual_map.get(_key(p.issuer, p.product_name))
        s = scoring.compute_score(p, vmap, e, my_targeted_offer_points=targeted)
        offer_quality = current_offer_quality(
            p,
            evidence_by_product.get(p.id or 0, []),
            as_of,
        )
        quality_issues = _decision_quality_issues(
            p,
            pending_by_id.get(p.id or 0, set()),
            offer_quality,
        )
        quality_blocks = _blocks_apply_decision(quality_issues)
        entry = product_to_dict(p)
        status = scoring.NEEDS_DATA if quality_blocks and s.status != scoring.SKIP else s.status
        entry.update(
            {
                "eligibility": e,
                "peak_score": s.peak_score,
                "offer_value": s.offer_value,
                "effective_points": s.effective_points,
                "targeted_beats_public": s.targeted_beats_public,
                "my_targeted_offer_points": targeted,
                "manual_targeted_offer": _manual_offer_to_dict(manual_offer),
                "status": status,
                "needs_data": s.needs_data or quality_blocks,
                "value_known": s.value_known,
                "peak_is_targeted": s.peak_is_targeted,
                "is_exceptional": s.is_exceptional,
                "cash_only": s.cash_only,
                "data_quality_issues": quality_issues,
                "decision_ready": not quality_blocks,
                "current_offer_quality": offer_quality,
                "current_offer_status": offer_quality["status"],
                "current_offer_reason": offer_quality["reason"],
            }
        )
        entries.append(entry)

    # Rank APPLY NOW / WATCH by offer_value (§6).
    rankable = sorted(
        [e for e in entries if e["status"] in (scoring.APPLY_NOW, scoring.WATCH)],
        key=lambda e: e["offer_value"],
        reverse=True,
    )
    rank_by_id = {e["id"]: i + 1 for i, e in enumerate(rankable)}
    for e in entries:
        e["rank"] = rank_by_id.get(e["id"])

    return entries
