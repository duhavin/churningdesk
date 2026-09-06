"""Application pipeline (§8) — eligibility-gated, scored, explainable.

Produces, per user:
  * next_cards   — an ordered apply queue, each step stating its binding rule /
                   value driver.
  * held_actions — keep / downgrade / cancel / re-queue recommendations.
"""
from __future__ import annotations

import datetime as dt
import re as _re
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models
from ..crypto import MissingKeyError
from ..product_identity import family_key, product_display_name, product_variant_key
from . import catalog as catalog_logic
from . import eligibility as elig
from . import scoring

if TYPE_CHECKING:
    from .decision_context import DecisionContext

ACTIONABLE_STATUSES = {scoring.APPLY_NOW, scoring.WATCH, scoring.WAIT}


def _is_chase(issuer: str) -> bool:
    return "chase" in (issuer or "").lower()


def is_ink_business(candidate: dict) -> bool:
    """Return whether a candidate belongs to the Chase Ink strategic band."""
    return (
        _is_chase(candidate.get("issuer"))
        and (candidate.get("ownership") or "") == "Business"
        and "ink" in (candidate.get("product_name") or "").lower()
    )


def strategy_sort_key(
    candidate: dict,
    *,
    under_524: bool,
    value: float | None = None,
    referral_value: float = 0.0,
    tie_points: int | float = 0,
    status: str | None = None,
    pipeline_rank: object = None,
    user_position: int = 0,
) -> tuple:
    """Canonical strategic ordering shared by Pipeline and Household.

    ``referral_value`` is an explicit household-only contribution to the value
    term; it is zero for the per-user pipeline.  The remaining fields preserve
    the documented Chase/Ink/exceptional, value, preference, timing and rank
    precedence without inventing a second household comparator.
    """
    base_value = value if value is not None else candidate.get("offer_value", 0)
    effective_value = round(float(base_value or 0) + float(referral_value or 0), 2)
    preferred_currency = 1 if (
        (candidate.get("currency") or "").strip().lower()
        in config.PREFERRED_TRANSFERABLE_CURRENCIES
    ) else 0
    points_for_bonus = candidate.get("effective_points")
    if points_for_bonus is None:
        points_for_bonus = tie_points
    large_bonus = 1 if (points_for_bonus or 0) >= config.MIN_APPLY_POINTS else 0
    status_priority = {
        scoring.APPLY_NOW: 3,
        scoring.WATCH: 2,
        scoring.WAIT: 1,
    }.get(status or candidate.get("status"), 0)
    rank = pipeline_rank if pipeline_rank is not None else candidate.get("rank")
    try:
        rank_score = -int(rank)
    except (TypeError, ValueError):
        rank_score = -999
    return (
        1 if _is_chase(candidate.get("issuer")) and under_524 else 0,
        1 if is_ink_business(candidate) else 0,
        1 if candidate.get("is_exceptional") else 0,
        effective_value,
        preferred_currency,
        large_bonus,
        status_priority,
        candidate.get("peak_score") or 0,
        tie_points or 0,
        rank_score,
        -(candidate.get("annual_fee") or 0),
        -user_position,
    )


def _exact_key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


def _active_min_spend_remaining(card: models.HeldCard) -> float:
    """Return only the recorded unfinished minimum-spend commitment."""
    if (card.status or "").strip().lower() in {"closed", "cancelled", "canceled"}:
        return 0.0
    if card.welcome_bonus_earned or card.min_spend_completed:
        return 0.0
    requirement = card.min_spend_requirement
    if requirement is None or requirement <= 0:
        return 0.0
    progress = max(float(card.min_spend_progress or 0.0), 0.0)
    return round(max(float(requirement) - progress, 0.0), 2)


def _complete_months_elapsed(as_of: dt.date, event: dt.date) -> int:
    """Count only full monthly allocation periods ending by ``event``."""
    if event <= as_of:
        return 0
    months = (event.year - as_of.year) * 12 + event.month - as_of.month
    if elig.add_months(as_of, months) > event:
        months -= 1
    return max(months, 0)


def spend_capacity_projection(
    context: "DecisionContext",
    user: str,
    candidate: dict,
    as_of: dt.date | None = None,
) -> dict:
    """Annotate a candidate with private capacity facts without changing rank.

    Capacity is the person's own monthly allocation before active bonus
    commitments.  A missing capacity is deliberately represented as unknown;
    no account balance or household total is inferred.
    """
    as_of = as_of or dt.date.today()
    capacity = context.organic_monthly_capacity_by_user.get(user)
    if capacity is not None:
        capacity = round(max(float(capacity), 0.0), 2)

    min_spend = candidate.get("current_offer_min_spend")
    window = candidate.get("current_offer_window_months")
    try:
        horizon_months = int(window) if window is not None else None
    except (TypeError, ValueError):
        horizon_months = None
    if horizon_months is not None and horizon_months <= 0:
        horizon_months = None
    candidate_horizon_end = elig.add_months(as_of, horizon_months) if horizon_months else None
    commitment_rows: list[tuple[float, dt.date | None]] = []
    unknown_commitment_timing = False
    overdue_commitment_timing = False
    for card in context.active_held_by_user.get(user, []):
        remaining = _active_min_spend_remaining(card)
        if not remaining:
            continue
        deadline = card.min_spend_deadline
        if deadline is None:
            unknown_commitment_timing = True
        elif deadline < as_of:
            overdue_commitment_timing = True
        commitment_rows.append((remaining, deadline))
    active_commitments = round(sum(amount for amount, _ in commitment_rows), 2)
    dated_deadlines = [deadline for _, deadline in commitment_rows if deadline is not None]
    analysis_horizon_end = max(
        [candidate_horizon_end, *dated_deadlines] if candidate_horizon_end else dated_deadlines,
        default=None,
    )
    analysis_horizon_months = None
    if analysis_horizon_end is not None:
        analysis_horizon_months = _complete_months_elapsed(as_of, analysis_horizon_end)
    horizon_budget = round(capacity * horizon_months, 2) if capacity is not None and horizon_months else None
    analysis_budget = (
        round(capacity * analysis_horizon_months, 2)
        if capacity is not None and analysis_horizon_months is not None
        else None
    )
    available = (
        round(max(analysis_budget - active_commitments, 0.0), 2)
        if analysis_budget is not None
        else None
    )
    required_monthly = None
    condition_kind = None
    condition_reason = None
    if min_spend is not None and float(min_spend) > 0:
        if horizon_months is None:
            condition_kind = "missing_spend_window"
            condition_reason = (
                "Minimum spend window is unknown; confirm the current terms before applying."
            )
        else:
            required_monthly = round(float(min_spend) / horizon_months, 2)
            if capacity is None:
                condition_kind = "unknown_capacity"
                condition_reason = (
                    "Organic monthly spend capacity is unknown; confirm available spend before applying."
                )
            elif unknown_commitment_timing:
                condition_kind = "unknown_commitment_timing"
                condition_reason = (
                    f"${active_commitments:,.0f} in active minimum-spend commitments has unknown timing; "
                    "confirm deadlines before applying."
                )
            elif overdue_commitment_timing:
                condition_kind = "overdue_commitment_timing"
                condition_reason = (
                    f"${active_commitments:,.0f} in active minimum-spend commitments includes an overdue deadline; "
                    "confirm the remaining obligations before applying."
                )
            else:
                # Check the candidate and every recorded commitment deadline as
                # one cumulative cash-flow model.  A later deadline remains a
                # real obligation; it cannot be ignored just because it falls
                # outside the candidate's spend window.
                due_events = sorted({deadline for _, deadline in commitment_rows if deadline is not None})
                if candidate_horizon_end is not None:
                    due_events.append(candidate_horizon_end)
                cumulative = 0.0
                candidate_added = False
                for event in sorted(set(due_events)):
                    cumulative += sum(amount for amount, deadline in commitment_rows if deadline == event)
                    if candidate_horizon_end is not None and not candidate_added and event >= candidate_horizon_end:
                        cumulative += float(min_spend)
                        candidate_added = True
                    months = _complete_months_elapsed(as_of, event)
                    event_budget = float(capacity or 0.0) * months
                    if cumulative > event_budget:
                        condition_kind = "insufficient_capacity"
                        condition_reason = (
                            f"By {event.isoformat()}, recorded minimum-spend commitments and this offer "
                            f"total ${cumulative:,.0f} against ${event_budget:,.0f} of declared capacity."
                        )
                        break
            if condition_kind is None and float(min_spend) > (available or 0.0):
                condition_kind = "insufficient_capacity"
                condition_reason = (
                    f"Across the declared {analysis_horizon_months or horizon_months}-month horizon, "
                    f"${active_commitments:,.0f} is already committed, leaving ${available or 0:,.0}; this offer needs "
                    f"${float(min_spend):,.0f}."
                )

    if candidate.get("targeted_beats_public"):
        targeted_reason = (
            "Confirm the selected private offer's minimum spend and time window. "
            "Public spend terms are a reference and do not verify this private offer."
        )
        condition_reason = f"{targeted_reason} {condition_reason}" if condition_reason else targeted_reason
        condition_kind = "unverified_targeted_terms"

    return {
        "conditional": condition_kind is not None,
        "condition_kind": condition_kind,
        "condition_reason": condition_reason,
        "spend_capacity": {
            "organic_monthly_capacity": capacity,
            "active_min_spend_remaining": active_commitments,
            "horizon_months": horizon_months,
            "horizon_end": candidate_horizon_end.isoformat() if candidate_horizon_end else None,
            "analysis_horizon_months": analysis_horizon_months,
            "analysis_horizon_end": analysis_horizon_end.isoformat() if analysis_horizon_end else None,
            "horizon_budget": horizon_budget,
            "available_after_commitments": available,
            "required_monthly_spend": required_monthly,
            "unknown_commitment_timing": unknown_commitment_timing,
        },
    }


def build_pipeline(
    db: Session,
    user: str,
    context: "DecisionContext | None" = None,
    as_of: dt.date | None = None,
    include_timing_waits: bool = False,
) -> dict:
    held = list(context.held_by_user.get(user, [])) if context else elig.held_cards(db, user)
    as_of = as_of or dt.date.today()
    f24 = elig.five24(db, user, as_of=as_of, held=held)
    under_524 = f24.count < 5
    entries = catalog_logic.scored_catalog(db, user, held=held, context=context, as_of=as_of)

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
        if (
            e["eligibility"]["eligible"]
            or (
                e["status"] == scoring.WAIT
                and e["eligibility"].get("block_type") == "temporary"
                and (
                    include_timing_waits
                    or not any(
                        "5/24" in str(reason).lower()
                        for reason in e["eligibility"].get("reasons", [])
                    )
                )
            )
        )
        and e["id"] not in held_product_ids
        and _exact_key(e["issuer"], e["product_name"]) not in held_keys
        and product_variant_key(e["issuer"], e["product_name"]) not in held_variants
    ]

    eligible: list[dict] = []
    family_alternates: list[dict] = []
    for entry in eligible_unheld:
        candidate_family = family_key(entry["issuer"], entry["product_name"], entry.get("product_family"))
        if candidate_family is not None and candidate_family in held_families:
            family_alternates.append(entry)
        else:
            eligible.append(entry)

    eligible.sort(
        key=lambda e: strategy_sort_key(
            e,
            under_524=under_524,
            value=e["offer_value"],
            tie_points=e.get("effective_points") or 0,
            status=e["status"],
        ),
        reverse=True,
    )
    family_alternates.sort(
        key=lambda e: strategy_sort_key(
            e,
            under_524=under_524,
            value=e["offer_value"],
            tie_points=e.get("effective_points") or 0,
            status=e["status"],
        ),
        reverse=True,
    )
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
                "account_type": e.get("account_type", "Credit Card"),
                "reports_to_personal_credit": e.get("reports_to_personal_credit", True),
                "currency": e["currency"],
                "tag": e.get("tag"),
                "status": e["status"],
                "peak_score": e["peak_score"],
                "offer_value": e["offer_value"],
                "effective_points": e["effective_points"],
                "current_offer_min_spend": e.get("current_offer_min_spend"),
                "current_offer_window_months": e.get("current_offer_window_months"),
                "offer_expiration": e.get("offer_expiration"),
                "current_offer_status": e.get("current_offer_status"),
                "current_offer_reason": e.get("current_offer_reason"),
                "current_offer_quality": e.get("current_offer_quality"),
                "earliest_eligible_date": (e.get("eligibility") or {}).get("earliest_eligible_date"),
                "targeted_beats_public": e["targeted_beats_public"],
                "is_exceptional": e.get("is_exceptional", False),
                "decision_ready": e.get("decision_ready", True),
                "data_quality_issues": e.get("data_quality_issues", []),
                "reason": reason,
            }
        )
    if context is not None:
        for card in next_cards:
            card.update(spend_capacity_projection(context, user, card, as_of=as_of))
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
                "offer_expiration": e.get("offer_expiration"),
                "current_offer_status": e.get("current_offer_status"),
                "current_offer_reason": e.get("current_offer_reason"),
                "current_offer_quality": e.get("current_offer_quality"),
                "targeted_beats_public": e["targeted_beats_public"],
                "is_exceptional": e.get("is_exceptional", False),
                "decision_ready": e.get("decision_ready", False),
                "data_quality_issues": e.get("data_quality_issues", []),
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
                "offer_expiration": e.get("offer_expiration"),
                "current_offer_status": e.get("current_offer_status"),
                "current_offer_reason": e.get("current_offer_reason"),
                "current_offer_quality": e.get("current_offer_quality"),
                "targeted_beats_public": e["targeted_beats_public"],
                "is_exceptional": e.get("is_exceptional", False),
                "decision_ready": e.get("decision_ready", False),
                "data_quality_issues": e.get("data_quality_issues", []),
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
    min_spend = e.get("current_offer_min_spend")
    window = e.get("current_offer_window_months")
    if min_spend:
        spend_suffix = f" Min spend: ${min_spend:,.0f}"
        spend_suffix += f" in {window} months." if window else "."
    else:
        spend_suffix = ""

    issuer_chase = _is_chase(e["issuer"])
    if e.get("is_exceptional"):
        return (
            "Rare offer - grab it if eligibility and spend capacity are clean. "
            f"{e['effective_points']:,} points, value ${e['offer_value']:.0f}, "
            f"peak_score {e['peak_score']}."
        ) + spend_suffix
    if issuer_chase and e["ownership"] == "Business" and under_524 and "ink" not in (e["product_name"] or "").lower():
        return (
            "Chase business: doesn't add to 5/24 but requires being under it - "
            f"get while under 5/24. peak_score {e['peak_score']}, "
            f"value ${e['offer_value']:.0f}."
        ) + spend_suffix
    if issuer_chase and e["ownership"] == "Business" and under_524:
        return (
            "Chase business (Ink): doesn't add to 5/24 but requires being under it — "
            f"get while under 5/24. peak_score {e['peak_score']}, "
            f"value ${e['offer_value']:.0f}."
        ) + spend_suffix
    if issuer_chase and under_524:
        return (
            "Chase-first while under 5/24 — Chase approvals get harder at 5/24. "
            f"peak_score {e['peak_score']}, value ${e['offer_value']:.0f}."
        ) + spend_suffix
    if e["status"] == scoring.APPLY_NOW:
        return (
            f"Offer is {e['peak_score']}% of its all-time peak — apply now. "
            f"Value ${e['offer_value']:.0f}."
        ) + spend_suffix
    driver = "transferable-currency accumulation" if e.get("tag") == "transferable" else "value"
    return (
        f"Eligible; peak_score {e['peak_score']}, value ${e['offer_value']:.0f}. "
        f"Ranked by {driver}."
    ) + spend_suffix


def _needs_review_reason(e: dict) -> str:
    issues = e.get("data_quality_issues") or []
    if issues:
        labels = {
            "pending_verified_update": "verified update pending adoption",
            "source_identity_conflict": "source conflicts with this product",
            "broad_source_not_product_truth": "source is a broad roundup, not a product page",
            "source_not_product_specific": "source is not product-specific",
            "never_verified": "no verified product source yet",
            "stale_verified_data": "verified data is stale",
            "missing_current_offer": "missing current public offer",
            "missing_public_peak": "missing public peak",
            "missing_annual_fee": "missing annual fee",
            "missing_currency": "missing rewards currency",
            "missing_min_spend": "missing minimum spend",
            "missing_spend_window": "missing spend window",
            "expired": "public offer has expired",
            "invalid_expiration": "offer expiration is not a recognized date",
            "current_offer_evidence_mismatch": "fresh evidence disagrees with persisted offer terms",
            "current_offer_evidence_stale": "current offer evidence is stale",
            "current_offer_evidence_missing": "current offer evidence is missing",
            "current_offer_evidence_unknown": "current offer evidence has unknown public status",
        }
        detail = ", ".join(labels.get(issue, issue.replace("_", " ")) for issue in issues[:3])
        return f"Excluded from apply queue: {detail}. Review or fill these public fields before ranking."
    if e["status"] == scoring.LOW_PRIORITY:
        # cash_only + honest valuation land with the scoring change; read
        # defensively so this works before and after that change ships.
        if e.get("cash_only"):
            return (
                f"Cash-only offer (~${(e.get('offer_value') or 0):.0f} tracked) — "
                "filtered by points-first preference."
            )
        if (e.get("offer_value") or 0) >= config.MIN_WATCH_VALUE:
            return (
                "Offer is well below its known peak — waiting for a better window. "
                f"Value ${e['offer_value']:.0f}, peak_score {e['peak_score']}."
            )
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


def _annualized_from_structured(item: dict) -> float | None:
    """Annualize a structured benefit (value + frequency fields).

    Returns None when the entry has no parseable value/frequency, so the
    text heuristic can take over.
    """
    raw_value = item.get("value")
    frequency = str(item.get("frequency") or "").strip().lower()
    if raw_value in (None, "") or not frequency:
        return None
    m = _re.search(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", str(raw_value))
    if not m:
        return None
    try:
        amount = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    multipliers = {
        "monthly": 12.0,
        "quarterly": 4.0,
        "semiannual": 2.0,
        "semi-annual": 2.0,
        "biannual": 2.0,
        "annual": 1.0,
        "cardmember_year": 1.0,
        "anniversary": 1.0,
        "yearly": 1.0,
    }
    multiplier = multipliers.get(frequency)
    if multiplier is None:
        return None
    return amount * multiplier


def _annual_benefit_value(product: models.CardProduct | None) -> float:
    """Sum annualized dollar amounts from card_benefits entries.

    Structured entries (value + known frequency) annualize exactly:
    monthly x12, quarterly x4, semiannual x2, annual/cardmember-year x1.
    Coverage/protection benefits are excluded — they offset risk, not the
    annual fee, and inflating renewal value with them skews retention calls.
    Unstructured text falls back to the cadence-keyword heuristic.
    """
    from ..benefit_normalization import is_protection_benefit

    if not product or not product.card_benefits:
        return 0.0
    total = 0.0
    items = product.card_benefits if isinstance(product.card_benefits, list) else []
    for item in items:
        if is_protection_benefit(item):
            continue
        if isinstance(item, dict):
            structured = _annualized_from_structured(item)
            if structured is not None:
                total += structured
                continue
            text = " ".join(str(v or "") for v in item.values())
        elif isinstance(item, str):
            text = item
        else:
            continue
        low = text.lower()
        is_semiannual = any(t in low for t in ("semiannual", "semi-annual", "semi-annually", "biannual"))
        is_annual = (not is_semiannual) and any(
            t in low for t in ("annual", "per year", "each year", "cardmember year", "calendar year")
        )
        is_monthly = any(t in low for t in ("per month", "monthly", "each month"))
        is_quarterly = "quarterly" in low

        if not is_annual and not is_monthly and not is_quarterly and not is_semiannual:
            continue

        amounts: list[float] = []
        for m in _re.finditer(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text):
            try:
                amounts.append(float(m.group(1).replace(",", "")))
            except ValueError:
                continue
        if not amounts:
            continue

        if is_annual and not is_monthly and not is_quarterly:
            total += sum(amounts)
        elif is_annual and (is_monthly or is_quarterly):
            # e.g. "$120 annual credit ($10/month)" — annual figure is authoritative
            total += max(amounts)
        elif is_semiannual:
            total += max(amounts) * 2
        elif is_monthly:
            total += max(amounts) * 12
        elif is_quarterly:
            total += max(amounts) * 4
    return total


def _held_value_signal(
    card: models.HeldCard,
    product: models.CardProduct | None,
    benefit_usages: list | None = None,
) -> tuple[int, list[str]]:
    benefits = _list_len(product.card_benefits if product else None)
    categories = _list_len(product.earn_multipliers if product else None) or _list_len(
        product.best_category_uses if product else None
    )
    downgrade_paths = _list_len(product.downgrade_paths if product else None)
    annual_fee = card.annual_fee if card.annual_fee is not None else (product.annual_fee if product else None)
    annual_fee = annual_fee or 0

    score = 30
    # Use extracted dollar value when available; fall back to benefit count
    benefit_dollar_value = _annual_benefit_value(product)
    if benefit_dollar_value > 0:
        score += min(benefit_dollar_value / 8, 36)  # $8 annual benefit = 1 pt, capped at 36
    elif benefits:
        score += min(benefits * 12, 36)
    score += min(categories * 10, 40)
    score += 8 if downgrade_paths else 0
    score -= min(annual_fee / 35, 18)
    if card.welcome_bonus_earned:
        score += 5

    drivers: list[str] = []
    if benefit_dollar_value > 0:
        drivers.append(f"~${benefit_dollar_value:.0f} annual benefit value")
    elif benefits:
        drivers.append(f"{benefits} benefit(s)")
    if categories:
        drivers.append(f"{categories} earn category signal(s)")
    if downgrade_paths:
        drivers.append("downgrade path available")
    if annual_fee:
        drivers.append(f"${annual_fee:.0f} annual fee considered")

    # Adjust for actual benefit credit utilization when usage data is available.
    # Only each benefit's MOST RECENT period counts, so an old unused year does
    # not permanently drag a card the user now redeems consistently.
    if benefit_usages:
        latest_by_benefit: dict[str, tuple[tuple, object]] = {}
        for u in benefit_usages:
            if not getattr(u, "amount_available", None):
                continue
            key = (
                getattr(u, "benefit_key", None)
                or getattr(u, "benefit_name", None)
                or f"row-{id(u)}"
            )
            order = (
                getattr(u, "period_start", None) or dt.date.min,
                str(getattr(u, "period_key", "") or ""),
            )
            prev = latest_by_benefit.get(key)
            if prev is None or order > prev[0]:
                latest_by_benefit[key] = (order, u)
        trackable = [u for _, u in latest_by_benefit.values()]
        if trackable:
            try:
                avg_util = sum(
                    min((u.amount_used or 0) / u.amount_available, 1.0)
                    for u in trackable
                ) / len(trackable)
            except (TypeError, ZeroDivisionError, MissingKeyError):
                avg_util = None
            if avg_util is not None:
                if avg_util >= 0.6:
                    score += 10
                    drivers.append(f"benefit credits well-used ({avg_util:.0%})")
                elif avg_util == 0:
                    score -= 8
                    drivers.append(f"benefit credits unused ({len(trackable)} trackable)")

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

    # Use pre-loaded BenefitUsage from context when available to avoid re-querying
    if context is not None:
        usage_by_held: dict[int, list] = context.benefit_usage_by_held
    else:
        held_ids = [h.id for h in held if h.id]
        usage_by_held = {}
        if held_ids:
            usage_rows = db.scalars(
                select(models.BenefitUsage)
                .where(models.BenefitUsage.held_card_id.in_(held_ids))
                .where(models.BenefitUsage.period_key != "__all__")
            ).all()
            for u in usage_rows:
                usage_by_held.setdefault(u.held_card_id, []).append(u)

    actions = []
    benefit_dollar_value_by_id: dict[int, float] = {}
    for h in held:
        if h.status == "Closed":
            continue
        _p = _card_product(h, product_by_id, product_by_key)
        benefit_dollar_value_by_id[h.id] = _annual_benefit_value(_p)
        again_ok, again_date = elig.bonus_eligible_again(h, as_of=today)
        product = _card_product(h, product_by_id, product_by_key)
        household_overlap_users = _overlap_users(h, product, overlap_index)
        value_score, drivers = _held_value_signal(h, product, benefit_usages=usage_by_held.get(h.id))
        downgrade_paths = product.downgrade_paths if product and isinstance(product.downgrade_paths, list) else []
        ladder_alternatives = _ladder_alternatives(h, product, products, vmap)
        ladder_alternative = ladder_alternatives[0] if ladder_alternatives else None
        action = "keep"
        reason = f"Active — keep. Ongoing value score {value_score}/100 from {', '.join(drivers)}."

        # Re-queue when re-eligibility has passed.
        requeue_note = ""
        if h.welcome_bonus_earned and again_ok:
            action = "requeue"
            reason = "Welcome bonus is eligible again — re-queue this product."
            since = f" since {again_date.isoformat()}" if again_date else ""
            requeue_note = (
                f" Also bonus-re-eligible{since} — consider downgrade/cancel then reapply."
            )

        if h.renewal_date:
            days_to_renewal = (h.renewal_date - today).days
            if 0 <= days_to_renewal <= 60:
                # Renewal decision takes over the action, but a pending
                # re-eligibility must stay visible in the binding reason.
                was_requeue = action == "requeue"
                annual_fee_val = h.annual_fee or (product.annual_fee if product else None) or 0
                fee_note = f" ${annual_fee_val:,.0f} annual fee." if annual_fee_val > 0 else ""
                if value_score >= 55:
                    action = "renew_review"
                    reason = (
                        f"Renewal {h.renewal_date.isoformat()}.{fee_note} Likely keep — value score "
                        f"{value_score}/100 from {', '.join(drivers)}. Verify credits and retention before paying."
                    )
                elif downgrade_paths:
                    action = "downgrade_review"
                    reason = (
                        f"Renewal {h.renewal_date.isoformat()}.{fee_note} Ongoing value looks thin "
                        f"({value_score}/100). Compare downgrade path(s): {', '.join(map(str, downgrade_paths[:3]))}."
                    )
                else:
                    action = "retention_review"
                    reason = (
                        f"Renewal {h.renewal_date.isoformat()}.{fee_note} Ask retention, then decide. "
                        f"Value score {value_score}/100 from {', '.join(drivers)}."
                    )
                if was_requeue:
                    reason += requeue_note

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

        # First-year clawback guard: never encourage a downgrade/cancel while
        # the account is under 12 months — issuers can claw back the bonus,
        # and that bonus was the whole point of the application.
        account_age_days = (today - h.date_opened).days if h.date_opened else None
        first_year_guard = bool(
            account_age_days is not None
            and account_age_days < 365
            and action in ("downgrade", "cancel", "downgrade_review", "retention_review")
        )
        if first_year_guard:
            reason += (
                " FIRST-YEAR GUARD: this account is under 12 months old — downgrading or "
                "cancelling now risks welcome-bonus clawback. Wait until after the first "
                "annual fee posts."
            )

        # Coming-soon re-eligibility: surface upcoming windows so they can be planned
        if not again_ok and again_date:
            days_until_eligible = (again_date - today).days
            if 0 < days_until_eligible <= 180:
                reason += (
                    f" Bonus eligible again {again_date.isoformat()} "
                    "— flag for re-application planning."
                )

        # Min-spend deadline alert: escalate when bonus window is closing
        if not h.min_spend_completed and h.min_spend_deadline:
            days_to_min_spend = (h.min_spend_deadline - today).days
            if 0 <= days_to_min_spend <= 45:
                if days_to_min_spend <= 7:
                    urgency = "URGENT —"
                elif days_to_min_spend <= 14:
                    urgency = "Warning —"
                else:
                    urgency = "Alert —"
                progress_note = (
                    f" Progress: ${h.min_spend_progress:,.0f} of ${h.min_spend_requirement:,.0f}."
                    if (h.min_spend_progress is not None and h.min_spend_requirement)
                    else ""
                )
                reason += (
                    f" {urgency} min-spend deadline in {days_to_min_spend} day(s) "
                    f"({h.min_spend_deadline.isoformat()}).{progress_note}"
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
                "min_spend_deadline": h.min_spend_deadline.isoformat() if h.min_spend_deadline else None,
                "min_spend_completed": h.min_spend_completed,
                "bonus_eligible_again": again_ok,
                "eligible_again_date": again_date.isoformat() if again_date else None,
                "action": action,
                "reason": reason,
                "value_score": value_score,
                "value_drivers": drivers,
                "annual_benefit_value": round(benefit_dollar_value_by_id.get(h.id, 0.0), 2)
                if isinstance(benefit_dollar_value_by_id, dict)
                else None,
                "net_annual_value": round(
                    benefit_dollar_value_by_id.get(h.id, 0.0)
                    - float(h.annual_fee or (product.annual_fee if product else 0) or 0),
                    2,
                )
                if isinstance(benefit_dollar_value_by_id, dict)
                else None,
                "first_year_guard": first_year_guard,
                "downgrade_paths": downgrade_paths,
                "household_overlap_users": household_overlap_users,
                "ladder_alternative": ladder_alternative,
                "ladder_alternatives": ladder_alternatives,
            }
        )
    return actions
