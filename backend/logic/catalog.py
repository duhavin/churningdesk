"""Shared catalog helpers: effective catalog, valuations, and scored catalog.

Used by the Card Plan tab and the Application Pipeline so both see the same
(discovered ∪ watchlisted) − blacklisted view with identical scoring.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import models
from ..crypto import MissingKeyError
from . import eligibility as elig
from . import scoring


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


def effective_catalog(db: Session) -> list[models.CardProduct]:
    """All catalog products minus anything matching the blacklist (§5.3)."""
    bl = blacklisted_keys(db)
    products = db.scalars(select(models.CardProduct)).all()
    return [
        p
        for p in products
        if ((p.issuer or "").strip().lower(), (p.product_name or "").strip().lower())
        not in bl
    ]


def product_to_dict(p: models.CardProduct) -> dict:
    return {
        "id": p.id,
        "issuer": p.issuer,
        "product_name": p.product_name,
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
        "card_benefits": p.card_benefits,
        "downgrade_paths": p.downgrade_paths,
        "eligibility_tags": p.eligibility_tags,
        "tag": p.tag,
        "added_by": p.added_by,
        "discovery_reviewed": p.discovery_reviewed,
        "source_url": p.source_url,
        "last_verified": p.last_verified.isoformat() if p.last_verified else None,
        "last_web_search_at": p.last_web_search_at.isoformat() if p.last_web_search_at else None,
        "notes": p.notes,
    }


def _key(issuer: str | None, name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (name or "").strip().lower())


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


def _show_in_plan(p: models.CardProduct) -> bool:
    """Hide refreshed cards that have no welcome bonus (now or ever) to cut bloat.

    Unverified cards (never refreshed) are kept so a future refresh can fill them
    in; a card that has been verified and still shows no bonus is dropped (it
    re-appears automatically if a later refresh finds an offer).
    """
    if has_known_bonus(p):
        return True
    return p.last_verified is None


def scored_catalog(
    db: Session, user: str, held: list[models.HeldCard] | None = None
) -> list[dict]:
    """Catalog entries enriched with per-user eligibility, score, and rank.

    Pass ``held`` to reuse an already-loaded held-card list (e.g. from the
    pipeline) and avoid re-querying it.
    """
    vmap = valuation_map(db)
    # Load held cards once and reuse across five24 / eligibility / targeted
    # lookups — avoids the N+1 query storm of re-fetching them per product.
    held = held if held is not None else elig.held_cards(db, user)
    f24 = elig.five24(db, user, held=held)
    today = dt.date.today()
    manual = list(
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
    targeted_map = _targeted_map(held, manual)
    manual_map = {_key(m.issuer, m.product_name): m for m in manual}
    products = [p for p in effective_catalog(db) if _show_in_plan(p)]

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
        entry = product_to_dict(p)
        entry.update(
            {
                "eligibility": e,
                "peak_score": s.peak_score,
                "offer_value": s.offer_value,
                "effective_points": s.effective_points,
                "targeted_beats_public": s.targeted_beats_public,
                "my_targeted_offer_points": targeted,
                "manual_targeted_offer": _manual_offer_to_dict(manual_offer),
                "status": s.status,
                "needs_data": s.needs_data,
                "value_known": s.value_known,
                "peak_is_targeted": s.peak_is_targeted,
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
