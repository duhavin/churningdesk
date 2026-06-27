"""Shared catalog helpers: effective catalog, valuations, and scored catalog.

Used by the Card Plan tab and the Application Pipeline so both see the same
(discovered ∪ watchlisted) − blacklisted view with identical scoring.
"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import models, source_quality
from ..benefit_normalization import normalize_public_benefits
from ..crypto import MissingKeyError
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
}
DECISION_FRESH_DAYS = 30


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


def _decision_quality_issues(p: models.CardProduct, pending_fields: set[str] | None = None) -> list[str]:
    issues: list[str] = []
    pending_fields = pending_fields or set()
    pending_critical = sorted(CRITICAL_DECISION_FIELDS & pending_fields)
    if pending_critical:
        issues.append("pending_verified_update")
    source_issue = source_quality.source_quality_issue(p.issuer, p.product_name, p.source_url)
    if source_issue:
        issues.append(source_issue)
    if not p.last_verified:
        issues.append("never_verified")
    else:
        age_days = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - p.last_verified).days
        if age_days > DECISION_FRESH_DAYS:
            issues.append("stale_verified_data")
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
    }
    return any(issue in blocking for issue in issues)


def _display_benefits(p: models.CardProduct) -> list | None:
    return normalize_public_benefits(
        p.issuer,
        p.product_name,
        p.source_url,
        p.card_benefits,
        allow_reference=_verified_status(p) == "verified",
    )


def product_to_dict(p: models.CardProduct) -> dict:
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
        "card_benefits": _display_benefits(p),
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
    """True if the card carries (or has ever carried) a welcome bonus."""
    return bool(
        (p.current_offer_effective or 0)
        or (p.current_offer_cash or 0)
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
) -> list[dict]:
    """Catalog entries enriched with per-user eligibility, score, and rank.

    Pass ``held`` to reuse an already-loaded held-card list (e.g. from the
    pipeline) and avoid re-querying it.
    """
    vmap = context.valuations if context else valuation_map(db)
    # Load held cards once and reuse across five24 / eligibility / targeted
    # lookups — avoids the N+1 query storm of re-fetching them per product.
    held = held if held is not None else (
        list(context.held_by_user.get(user, [])) if context else elig.held_cards(db, user)
    )
    f24 = elig.five24(db, user, held=held)
    today = dt.date.today()
    manual = (
        list(context.manual_targeted_by_user.get(user, []))
        if context
        else list(
            db.scalars(
                select(models.ManualTargetedOffer).where(
                    models.ManualTargetedOffer.user == user,
                    or_(
                        models.ManualTargetedOffer.expires_at.is_(None),
                        models.ManualTargetedOffer.expires_at >= today,
                    ),
                )
            ).all()
        )
    )
    targeted_map = _targeted_map(held, manual)
    manual_map = {_key(m.issuer, m.product_name): m for m in manual}
    products = [p for p in (context.products if context else effective_catalog(db)) if _show_in_plan(p)]
    pending_by_id = _pending_fields(db)

    entries: list[dict] = []
    for p in products:
        e = elig.eligibility(
            db, user, p.issuer, p.product_name,
            ownership=p.ownership, f24=f24, held=held,
            eligibility_tags=p.eligibility_tags,
        ).to_dict()
        targeted = targeted_map.get(_key(p.issuer, p.product_name))
        manual_offer = manual_map.get(_key(p.issuer, p.product_name))
        s = scoring.compute_score(p, vmap, e, my_targeted_offer_points=targeted)
        quality_issues = _decision_quality_issues(p, pending_by_id.get(p.id or 0, set()))
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
                "data_quality_issues": quality_issues,
                "decision_ready": not quality_blocks,
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
