"""Application pipeline (§8) — eligibility-gated, scored, explainable.

Produces, per user:
  * next_cards   — an ordered apply queue, each step stating its binding rule /
                   value driver.
  * held_actions — keep / downgrade / cancel / re-queue recommendations.
"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models
from ..product_identity import family_key, product_display_name, product_variant_key
from . import catalog as catalog_logic
from . import eligibility as elig
from . import scoring

if TYPE_CHECKING:
    from .decision_context import DecisionContext

ACTIONABLE_STATUSES = {scoring.APPLY_NOW, scoring.WATCH, scoring.WAIT}


def _is_chase(issuer: str) -> bool:
    return "chase" in (issuer or "").lower()


def _exact_key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


def build_pipeline(db: Session, user: str, context: "DecisionContext | None" = None) -> dict:
    held = list(context.held_by_user.get(user, [])) if context else elig.held_cards(db, user)
    f24 = elig.five24(db, user, held=held)
    under_524 = f24.count < 5
    entries = catalog_logic.scored_catalog(db, user, held=held, context=context)

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
        if context:
            linked_products = [p for pid, p in context.products_by_id.items() if pid in held_product_ids]
        else:
            linked_products = db.scalars(select(models.CardProduct).where(models.CardProduct.id.in_(held_product_ids))).all()
        held_variants.update(
            variant
            for p in linked_products
            for variant in [product_variant_key(p.issuer, p.product_name)]
            if variant is not None
        )
        held_families = {
            family
            for p in linked_products
            for family in [family_key(p.issuer, p.product_name, p.product_family)]
            if family is not None
        }
    else:
        held_families = set()
    held_families.update(
        family
        for h in held
        if h.status != "Closed"
        for family in [family_key(h.issuer, h.product_name)]
        if family is not None
    )

    # --- Eligible, not-already-held candidates -----------------------------
    eligible_unheld = [
        e
        for e in entries
        if e["eligibility"]["eligible"]
        and e["id"] not in held_product_ids
        and _exact_key(e["issuer"], e["product_name"]) not in held_keys
        and product_variant_key(e["issuer"], e["product_name"]) not in held_variants
    ]

    def sort_key(e: dict):
        issuer_chase = _is_chase(e["issuer"])
        is_ink = issuer_chase and e["ownership"] == "Business" and "ink" in (e["product_name"] or "").lower()
        preferred_currency = 1 if (e.get("currency") or "").strip().lower() in config.PREFERRED_TRANSFERABLE_CURRENCIES else 0
        large_bonus = 1 if (e.get("effective_points") or 0) >= config.MIN_APPLY_POINTS else 0
        chase_priority = 1 if (issuer_chase and under_524) else 0
        return (
            chase_priority,
            1 if is_ink else 0,
            1 if e.get("is_exceptional") else 0,
            e["offer_value"],
            preferred_currency,
            large_bonus,
            {scoring.APPLY_NOW: 3, scoring.WATCH: 2, scoring.WAIT: 1}.get(e["status"], 0),
            e["peak_score"],
            -(e.get("annual_fee") or 0),
        )

    eligible: list[dict] = []
    family_alternates: list[dict] = []
    for entry in eligible_unheld:
        candidate_family = family_key(entry["issuer"], entry["product_name"], entry.get("product_family"))
        if candidate_family is not None and candidate_family in held_families:
            family_alternates.append(entry)
        else:
            eligible.append(entry)

    eligible.sort(key=sort_key, reverse=True)
    family_alternates.sort(key=sort_key, reverse=True)
    actionable = [e for e in eligible if e["status"] in ACTIONABLE_STATUSES]
    needs_data = [
        e
        for e in eligible
        if e["status"] in {scoring.NEEDS_DATA, scoring.LOW_PRIORITY}
    ]

    next_cards = []
    for i, e in enumerate(actionable):
        reason = _apply_reason(e, under_524)
        next_cards.append(
            {
                "rank": i + 1,
                "id": e["id"],
                "issuer": e["issuer"],
                "product_name": e["product_name"],
                "display_name": e.get("display_name") or product_display_name(e["issuer"], e["product_name"]),
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
                "is_exceptional": e.get("is_exceptional", False),
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
                "display_name": e.get("display_name") or product_display_name(e["issuer"], e["product_name"]),
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
                "is_exceptional": e.get("is_exceptional", False),
                "reason": _needs_review_reason(e),
            }
        )

    alternate_strategies = []
    for e in family_alternates:
        if e["status"] not in ACTIONABLE_STATUSES and e["status"] not in {scoring.NEEDS_DATA, scoring.LOW_PRIORITY}:
            continue
        alternate_strategies.append(
            {
                "id": e["id"],
                "issuer": e["issuer"],
                "product_name": e["product_name"],
                "display_name": e.get("display_name") or product_display_name(e["issuer"], e["product_name"]),
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
                "is_exceptional": e.get("is_exceptional", False),
                "relationship": "same_family_ladder",
                "reason": _alternate_strategy_reason(e),
            }
        )

    held_actions = _held_actions(db, held, context=context)

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
        "alternate_strategies": alternate_strategies,
        "held_actions": held_actions,
    }


def _apply_reason(e: dict, under_524: bool) -> str:
    issuer_chase = _is_chase(e["issuer"])
    if e.get("is_exceptional"):
        return (
            "Rare offer - grab it if eligibility and spend capacity are clean. "
            f"{e['effective_points']:,} points, value ${e['offer_value']:.0f}, "
            f"peak_score {e['peak_score']}."
        )
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


def _needs_review_reason(e: dict) -> str:
    if e["status"] == scoring.LOW_PRIORITY:
        return (
            "Excluded from apply queue: offer value is below the current value floor. "
            f"Value ${e['offer_value']:.0f}, peak_score {e['peak_score']}."
        )
    return "Missing current offer, peak, or valuation data; refresh before using this in the apply queue."


def _alternate_strategy_reason(e: dict) -> str:
    if e["status"] == scoring.NEEDS_DATA:
        return "Same-family ladder card; verify offer and peak before considering a product change or close/reapply path."
    if e["status"] == scoring.LOW_PRIORITY:
        return (
            "Same-family ladder card excluded from apply queue: value is below the current floor. "
            f"Value ${e['offer_value']:.0f}, peak_score {e['peak_score']}."
        )
    return (
        "Same-family ladder card. Do not open as a duplicate; review only as an upgrade, downgrade, "
        "or close-then-apply strategy if issuer rules and net value justify it. "
        f"Signal: {e['status']}, value ${e['offer_value']:.0f}, peak_score {e['peak_score']}."
    )


def _product_lookup(
    db: Session, held: list[models.HeldCard], context: "DecisionContext | None" = None
) -> dict[int, models.CardProduct]:
    ids = {h.product_id for h in held if h.product_id}
    if not ids:
        return {}
    if context:
        return {pid: product for pid, product in context.products_by_id.items() if pid in ids}
    return {p.id: p for p in db.scalars(select(models.CardProduct).where(models.CardProduct.id.in_(ids))).all()}


def _catalog_lookup(
    db: Session, context: "DecisionContext | None" = None
) -> dict[tuple[str, str], models.CardProduct]:
    if context:
        return context.products_by_key
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


def _ladder_alternatives(
    card: models.HeldCard,
    product: models.CardProduct | None,
    products: list[models.CardProduct],
    vmap: dict[str, float],
) -> list[dict]:
    family = _family_for_card(card, product)
    if not family:
        return []

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
                "display_name": product_display_name(candidate.issuer, candidate.product_name),
                "offer_value": score.offer_value,
                "peak_score": score.peak_score,
                "status": score.status,
            }
        )

    candidates.sort(key=lambda c: (c["offer_value"], c["peak_score"]), reverse=True)
    return candidates[:3]


def _list_len(value) -> int:
    if isinstance(value, list):
        return len([item for item in value if item])
    if isinstance(value, dict):
        return len([key for key, item in value.items() if key and item is not None])
    return 0


def _household_overlap_index(
    db: Session,
    context: "DecisionContext | None" = None,
) -> dict[tuple[str, int | tuple[str, str]], set[str]]:
    active = (
        [card for cards in context.active_held_by_user.values() for card in cards]
        if context
        else db.scalars(
            select(models.HeldCard).where(
                models.HeldCard.user.in_(config.USERS),
                models.HeldCard.status != "Closed",
            )
        ).all()
    )
    products_by_id = context.products_by_id if context else _product_lookup(db, active, context=context)
    index: dict[tuple[str, int | tuple[str, str]], set[str]] = {}
    for card in active:
        keys: set[tuple[str, int | tuple[str, str]]] = {("key", _exact_key(card.issuer, card.product_name))}
        variant = product_variant_key(card.issuer, card.product_name)
        if variant:
            keys.add(("variant", variant))
        if card.product_id:
            keys.add(("id", card.product_id))
            product = products_by_id.get(card.product_id)
            if product:
                keys.add(("key", _exact_key(product.issuer, product.product_name)))
                product_variant = product_variant_key(product.issuer, product.product_name)
                if product_variant:
                    keys.add(("variant", product_variant))
        for key_item in keys:
            index.setdefault(key_item, set()).add(card.user)
    return index


def _overlap_users(
    card: models.HeldCard,
    product: models.CardProduct | None,
    overlap_index: dict[tuple[str, int | tuple[str, str]], set[str]],
) -> list[str]:
    keys: set[tuple[str, int | tuple[str, str]]] = {("key", _exact_key(card.issuer, card.product_name))}
    variant = product_variant_key(card.issuer, card.product_name)
    if variant:
        keys.add(("variant", variant))
    if card.product_id:
        keys.add(("id", card.product_id))
    if product:
        keys.add(("key", _exact_key(product.issuer, product.product_name)))
        product_variant = product_variant_key(product.issuer, product.product_name)
        if product_variant:
            keys.add(("variant", product_variant))
    users = set()
    for key_item in keys:
        users.update(overlap_index.get(key_item, set()))
    users.discard(card.user)
    return sorted(users)


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


def _held_actions(
    db: Session, held: list[models.HeldCard], context: "DecisionContext | None" = None
) -> list[dict]:
    today = dt.date.today()
    vmap = context.valuations if context else catalog_logic.valuation_map(db)
    product_by_id = _product_lookup(db, held, context=context)
    product_by_key = _catalog_lookup(db, context=context)
    products = list(product_by_key.values())
    overlap_index = _household_overlap_index(db, context=context)
    actions = []
    for h in held:
        if h.status == "Closed":
            continue
        again_ok, again_date = elig.bonus_eligible_again(h, as_of=today)
        product = _card_product(h, product_by_id, product_by_key)
        household_overlap_users = _overlap_users(h, product, overlap_index)
        value_score, drivers = _held_value_signal(h, product)
        downgrade_paths = product.downgrade_paths if product and isinstance(product.downgrade_paths, list) else []
        ladder_alternatives = _ladder_alternatives(h, product, products, vmap)
        ladder_alternative = ladder_alternatives[0] if ladder_alternatives else None
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
                f"Review {ladder_alternative.get('display_name') or ladder_alternative['product_name']} as a same-ladder move only if "
                "issuer rules allow product-change or close/reapply. "
                f"Current offer signal: {ladder_alternative['status']}, "
                f"value ${ladder_alternative['offer_value']:.0f}, "
                f"peak_score {ladder_alternative['peak_score']}."
            )

        if household_overlap_users and action not in {"cancel", "downgrade"}:
            reason += (
                f" Household overlap with {', '.join(household_overlap_users)} is not a close/cancel reason; "
                "evaluate this account on its own credits, fee, bonus history, and spend use."
            )

        actions.append(
            {
                "id": h.id,
                "issuer": h.issuer,
                "product_name": h.product_name,
                "display_name": product_display_name(h.issuer, h.product_name),
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
                "household_overlap_users": household_overlap_users,
                "ladder_alternative": ladder_alternative,
                "ladder_alternatives": ladder_alternatives,
            }
        )
    return actions
