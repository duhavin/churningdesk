"""Application pipeline (§8) — eligibility-gated, scored, explainable.

Produces, per user:
  * next_cards   — an ordered apply queue, each step stating its binding rule /
                   value driver.
  * held_actions — keep / downgrade / cancel / re-queue recommendations.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..product_identity import family_key, product_variant_key
from . import catalog as catalog_logic
from . import eligibility as elig
from . import scoring

ACTIONABLE_STATUSES = {scoring.APPLY_NOW, scoring.WATCH, scoring.WAIT}


def _is_chase(issuer: str) -> bool:
    return "chase" in (issuer or "").lower()


def _exact_key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


def build_pipeline(db: Session, user: str) -> dict:
    held = elig.held_cards(db, user)
    f24 = elig.five24(db, user, held=held)
    under_524 = f24.count < 5
    entries = catalog_logic.scored_catalog(db, user, held=held)

    # Products the user already holds (open) — never recommend opening these
    # again. Re-applying a re-eligible card is surfaced via held_actions
    # ("requeue") instead, so the apply queue stays free of redundant cards.
    held_keys = {
        _exact_key(h.issuer, h.product_name)
        for h in held
        if h.status != "Closed"
    }
    held_product_ids = {h.product_id for h in held if h.status != "Closed" and h.product_id}
    held_variants = {
        variant
        for h in held
        if h.status != "Closed"
        for variant in [product_variant_key(h.issuer, h.product_name)]
        if variant is not None
    }
    if held_product_ids:
        linked_products = db.scalars(select(models.CardProduct).where(models.CardProduct.id.in_(held_product_ids))).all()
        held_variants.update(
            variant
            for p in linked_products
            for variant in [product_variant_key(p.issuer, p.product_name)]
            if variant is not None
        )

    # --- Eligible, not-already-held candidates -----------------------------
    eligible = [
        e
        for e in entries
        if e["eligibility"]["eligible"]
        and e["id"] not in held_product_ids
        and _exact_key(e["issuer"], e["product_name"]) not in held_keys
        and product_variant_key(e["issuer"], e["product_name"]) not in held_variants
    ]

    def sort_key(e: dict):
        issuer_chase = _is_chase(e["issuer"])
        is_chase_business = issuer_chase and e["ownership"] == "Business"
        # Bias toward transferable currencies for long-term accumulation.
        transferable = 1 if e.get("tag") == "transferable" else 0
        # Ordering priority (higher tuple sorts first):
        #  1. Chase-first while under 5/24
        #  2. Chase business prioritized
        #  3. offer_value, then peak_score
        #  4. transferable bias and low annual fee as a final tiebreaker
        chase_priority = 1 if (issuer_chase and under_524) else 0
        return (
            chase_priority,
            1 if is_chase_business else 0,
            e["offer_value"],
            e["peak_score"],
            transferable,
            {scoring.APPLY_NOW: 3, scoring.WATCH: 2, scoring.WAIT: 1}.get(e["status"], 0),
            -(e.get("annual_fee") or 0),
        )

    eligible.sort(key=sort_key, reverse=True)
    actionable = [e for e in eligible if e["status"] in ACTIONABLE_STATUSES]
    needs_data = [e for e in eligible if e["status"] == scoring.NEEDS_DATA]

    next_cards = []
    for i, e in enumerate(actionable):
        reason = _apply_reason(e, under_524)
        next_cards.append(
            {
                "rank": i + 1,
                "id": e["id"],
                "issuer": e["issuer"],
                "product_name": e["product_name"],
                "ownership": e["ownership"],
                "currency": e["currency"],
                "tag": e.get("tag"),
                "status": e["status"],
                "peak_score": e["peak_score"],
                "offer_value": e["offer_value"],
                "effective_points": e["effective_points"],
                "current_offer_min_spend": e.get("current_offer_min_spend"),
                "current_offer_window_months": e.get("current_offer_window_months"),
                "targeted_beats_public": e["targeted_beats_public"],
                "reason": reason,
            }
        )
    needs_data_cards = []
    for e in needs_data:
        needs_data_cards.append(
            {
                "id": e["id"],
                "issuer": e["issuer"],
                "product_name": e["product_name"],
                "ownership": e["ownership"],
                "currency": e["currency"],
                "tag": e.get("tag"),
                "status": e["status"],
                "peak_score": e["peak_score"],
                "offer_value": e["offer_value"],
                "effective_points": e["effective_points"],
                "current_offer_min_spend": e.get("current_offer_min_spend"),
                "current_offer_window_months": e.get("current_offer_window_months"),
                "targeted_beats_public": e["targeted_beats_public"],
                "reason": "Missing current offer, peak, or valuation data; refresh before using this in the apply queue.",
            }
        )

    held_actions = _held_actions(db, held)

    return {
        "user": user,
        "five_24": {
            "count": f24.count,
            "under_524": under_524,
            "earliest_drop_date": f24.earliest_drop_date.isoformat()
            if f24.earliest_drop_date
            else None,
        },
        "next_cards": next_cards,
        "needs_data": needs_data_cards,
        "held_actions": held_actions,
    }


def _apply_reason(e: dict, under_524: bool) -> str:
    issuer_chase = _is_chase(e["issuer"])
    if issuer_chase and e["ownership"] == "Business" and under_524 and "ink" not in (e["product_name"] or "").lower():
        return (
            "Chase business: doesn't add to 5/24 but requires being under it - "
            f"get while under 5/24. peak_score {e['peak_score']}, "
            f"value ${e['offer_value']:.0f}."
        )
    if issuer_chase and e["ownership"] == "Business" and under_524:
        return (
            "Chase business (Ink): doesn't add to 5/24 but requires being under it — "
            f"get while under 5/24. peak_score {e['peak_score']}, "
            f"value ${e['offer_value']:.0f}."
        )
    if issuer_chase and under_524:
        return (
            "Chase-first while under 5/24 — Chase approvals get harder at 5/24. "
            f"peak_score {e['peak_score']}, value ${e['offer_value']:.0f}."
        )
    if e["status"] == scoring.APPLY_NOW:
        return (
            f"Offer is {e['peak_score']}% of its all-time peak — apply now. "
            f"Value ${e['offer_value']:.0f}."
        )
    driver = "transferable-currency accumulation" if e.get("tag") == "transferable" else "value"
    return (
        f"Eligible; peak_score {e['peak_score']}, value ${e['offer_value']:.0f}. "
        f"Ranked by {driver}."
    )


def _product_lookup(db: Session, held: list[models.HeldCard]) -> dict[int, models.CardProduct]:
    ids = {h.product_id for h in held if h.product_id}
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(models.CardProduct).where(models.CardProduct.id.in_(ids))).all()}


def _catalog_lookup(db: Session) -> dict[tuple[str, str], models.CardProduct]:
    return {
        ((p.issuer or "").strip().lower(), (p.product_name or "").strip().lower()): p
        for p in db.scalars(select(models.CardProduct)).all()
    }


def _card_product(
    card: models.HeldCard,
    by_id: dict[int, models.CardProduct],
    by_key: dict[tuple[str, str], models.CardProduct],
) -> models.CardProduct | None:
    if card.product_id and card.product_id in by_id:
        return by_id[card.product_id]
    exact = ((card.issuer or "").strip().lower(), (card.product_name or "").strip().lower())
    product = by_key.get(exact)
    if product:
        return product
    variant = product_variant_key(card.issuer, card.product_name)
    if not variant:
        return None
    for candidate in by_key.values():
        if product_variant_key(candidate.issuer, candidate.product_name) == variant:
            return candidate
    return None


def _family_for_card(card: models.HeldCard, product: models.CardProduct | None) -> tuple[str, str] | None:
    if product:
        return family_key(product.issuer, product.product_name, product.product_family)
    return family_key(card.issuer, card.product_name)


def _best_ladder_alternative(
    card: models.HeldCard,
    product: models.CardProduct | None,
    products: list[models.CardProduct],
    vmap: dict[str, float],
) -> dict | None:
    family = _family_for_card(card, product)
    if not family:
        return None

    held_variant = product_variant_key(
        product.issuer if product else card.issuer,
        product.product_name if product else card.product_name,
    )
    candidates: list[dict] = []
    for candidate in products:
        if product and candidate.id == product.id:
            continue
        if family_key(candidate.issuer, candidate.product_name, candidate.product_family) != family:
            continue
        if product_variant_key(candidate.issuer, candidate.product_name) == held_variant:
            continue
        score = scoring.compute_score(
            candidate,
            vmap,
            {"eligible": True, "block_type": "none", "reasons": [], "earliest_eligible_date": None},
        )
        if score.status not in {scoring.APPLY_NOW, scoring.WATCH}:
            continue
        candidates.append(
            {
                "id": candidate.id,
                "product_name": candidate.product_name,
                "offer_value": score.offer_value,
                "peak_score": score.peak_score,
                "status": score.status,
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["offer_value"], c["peak_score"]))


def _list_len(value) -> int:
    if isinstance(value, list):
        return len([item for item in value if item])
    if isinstance(value, dict):
        return len([key for key, item in value.items() if key and item is not None])
    return 0


def _held_value_signal(card: models.HeldCard, product: models.CardProduct | None) -> tuple[int, list[str]]:
    benefits = _list_len(product.card_benefits if product else None)
    categories = _list_len(product.earn_multipliers if product else None) or _list_len(
        product.best_category_uses if product else None
    )
    downgrade_paths = _list_len(product.downgrade_paths if product else None)
    annual_fee = card.annual_fee if card.annual_fee is not None else (product.annual_fee if product else None)
    annual_fee = annual_fee or 0

    score = 30
    score += min(benefits * 12, 36)
    score += min(categories * 10, 40)
    score += 8 if downgrade_paths else 0
    score -= min(annual_fee / 35, 18)
    if card.welcome_bonus_earned:
        score += 5

    drivers: list[str] = []
    if benefits:
        drivers.append(f"{benefits} benefit(s)")
    if categories:
        drivers.append(f"{categories} earn category signal(s)")
    if downgrade_paths:
        drivers.append("downgrade path available")
    if annual_fee:
        drivers.append(f"${annual_fee:.0f} annual fee considered")
    if not drivers:
        drivers.append("limited ongoing value data")
    return round(max(0, min(score, 100))), drivers


def _held_actions(db: Session, held: list[models.HeldCard]) -> list[dict]:
    today = dt.date.today()
    vmap = catalog_logic.valuation_map(db)
    product_by_id = _product_lookup(db, held)
    product_by_key = _catalog_lookup(db)
    products = list(product_by_key.values())
    actions = []
    for h in held:
        if h.status == "Closed":
            continue
        again_ok, again_date = elig.bonus_eligible_again(h, as_of=today)
        product = _card_product(h, product_by_id, product_by_key)
        value_score, drivers = _held_value_signal(h, product)
        downgrade_paths = product.downgrade_paths if product and isinstance(product.downgrade_paths, list) else []
        ladder_alternative = _best_ladder_alternative(h, product, products, vmap)
        action = "keep"
        reason = f"Active — keep. Ongoing value score {value_score}/100 from {', '.join(drivers)}."

        # Re-queue when re-eligibility has passed.
        if h.welcome_bonus_earned and again_ok:
            action = "requeue"
            reason = "Welcome bonus is eligible again — re-queue this product."

        if h.renewal_date:
            days_to_renewal = (h.renewal_date - today).days
            if 0 <= days_to_renewal <= 60:
                if value_score >= 55:
                    action = "renew_review"
                    reason = (
                        f"Renewal {h.renewal_date.isoformat()} — likely keep, but verify credits "
                        f"and retention. Value score {value_score}/100 from {', '.join(drivers)}."
                    )
                elif downgrade_paths:
                    action = "downgrade_review"
                    reason = (
                        f"Renewal {h.renewal_date.isoformat()} — ongoing value looks thin "
                        f"({value_score}/100). Compare downgrade path(s): {', '.join(map(str, downgrade_paths[:3]))}."
                    )
                else:
                    action = "retention_review"
                    reason = (
                        f"Renewal {h.renewal_date.isoformat()} — ask retention, then decide. "
                        f"Value score {value_score}/100 from {', '.join(drivers)}."
                    )

        if h.status in ("Downgrade Pending", "Cancel Pending"):
            action = "downgrade" if h.status == "Downgrade Pending" else "cancel"
            reason = f"Marked {h.status}."
        elif action == "keep" and ladder_alternative:
            action = "ladder_review"
            reason = (
                f"Review {ladder_alternative['product_name']} as a same-ladder move only if "
                "issuer rules allow product-change or close/reapply. "
                f"Current offer signal: {ladder_alternative['status']}, "
                f"value ${ladder_alternative['offer_value']:.0f}, "
                f"peak_score {ladder_alternative['peak_score']}."
            )

        actions.append(
            {
                "id": h.id,
                "issuer": h.issuer,
                "product_name": h.product_name,
                "status": h.status,
                "annual_fee": h.annual_fee,
                "renewal_date": h.renewal_date.isoformat() if h.renewal_date else None,
                "bonus_eligible_again": again_ok,
                "eligible_again_date": again_date.isoformat() if again_date else None,
                "action": action,
                "reason": reason,
                "value_score": value_score,
                "value_drivers": drivers,
                "downgrade_paths": downgrade_paths,
                "ladder_alternative": ladder_alternative,
            }
        )
    return actions
