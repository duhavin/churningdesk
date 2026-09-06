"""Household optimization — two-user (User A + User B) synergy.

Combines both users' eligibility-gated pipelines into one cohesive plan:

  * per-user 5/24 + estimated point value side by side,
  * the combined household point value,
  * **referral routing** — the core synergy: when one member already holds a
    card the other is eligible for and doesn't have, the application should go
    through the holder's referral link so the household earns the referral bonus
    *on top of* the welcome bonus,
  * a merged "best next moves" queue ranked by the per-user pipeline order plus
    household value (welcome offer + any referral bonus the household can capture).

Everything is derived from held-card timing + the scored catalog, so it updates
automatically as cards are added/edited and never recommends a card a user
already holds (the per-user pipeline already excludes held products).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from .. import config, models
from ..product_identity import family_key, product_display_name, product_variant_key
from . import pipeline as pipeline_logic
from .decision_context import DecisionContext

STATUS_PRIORITY = {"APPLY NOW": 3, "WATCH": 2, "WAIT": 1}
REFERRAL_FAMILY_KEYS = {
    ("capital_one", "capital_one_venture"),
    ("capital_one", "capital_one_venture_business"),
    ("american_express", "amex_gold"),
    ("american_express", "amex_business_gold"),
    ("chase", "chase_ink_preferred"),
    ("chase", "chase_ink_cash"),
    ("chase", "chase_ink_unlimited"),
    ("chase", "chase_ink_premier"),
}

# Maps individual family keys to a broader referral group, enabling cross-variant
# detection (e.g., Ink Cash holder → Ink Preferred applicant; Gold → Business Gold).
REFERRAL_SUPER_FAMILIES: dict[tuple, str] = {
    ("chase", "chase_ink_preferred"): "chase_ink",
    ("chase", "chase_ink_cash"): "chase_ink",
    ("chase", "chase_ink_unlimited"): "chase_ink",
    ("chase", "chase_ink_premier"): "chase_ink",
    ("american_express", "amex_gold"): "amex_mr_gold",
    ("american_express", "amex_business_gold"): "amex_mr_gold",
}


def _key(issuer: str | None, name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (name or "").strip().lower())


def _is_chase(issuer: str | None) -> bool:
    return "chase" in (issuer or "").lower()


def _held_referral_keys(
    held: models.HeldCard, products: dict[int, models.CardProduct]
) -> set[tuple[str, int | tuple[str, str]]]:
    keys: set[tuple[str, int | tuple[str, str]]] = {("key", _key(held.issuer, held.product_name))}
    held_variant = product_variant_key(held.issuer, held.product_name)
    if held_variant:
        keys.add(("variant", held_variant))
    if held.product_id:
        keys.add(("id", held.product_id))
        product = products.get(held.product_id)
        if product:
            keys.add(("key", _key(product.issuer, product.product_name)))
            product_variant = product_variant_key(product.issuer, product.product_name)
            if product_variant:
                keys.add(("variant", product_variant))
            product_family = family_key(product.issuer, product.product_name, product.product_family)
            if product_family in REFERRAL_FAMILY_KEYS:
                keys.add(("family", product_family))
            super_fam = REFERRAL_SUPER_FAMILIES.get(product_family)
            if super_fam:
                keys.add(("super_family", super_fam))
    held_family = family_key(held.issuer, held.product_name)
    if held_family in REFERRAL_FAMILY_KEYS:
        keys.add(("family", held_family))
    super_fam = REFERRAL_SUPER_FAMILIES.get(held_family)
    if super_fam:
        keys.add(("super_family", super_fam))
    return keys


def _candidate_referral_keys(nc: dict) -> set[tuple[str, int | tuple[str, str]]]:
    keys: set[tuple[str, int | tuple[str, str]]] = {
        ("id", nc["id"]),
        ("key", _key(nc["issuer"], nc["product_name"])),
    }
    variant = product_variant_key(nc["issuer"], nc["product_name"])
    if variant:
        keys.add(("variant", variant))
    candidate_family = family_key(nc["issuer"], nc["product_name"], nc.get("product_family"))
    if candidate_family in REFERRAL_FAMILY_KEYS:
        keys.add(("family", candidate_family))
    super_fam = REFERRAL_SUPER_FAMILIES.get(candidate_family)
    if super_fam:
        keys.add(("super_family", super_fam))
    return keys


def _value_of(balances: dict[str, float], vmap: dict[str, float]) -> float:
    """Total estimated dollar value of a set of point balances (cpp is cents/pt)."""
    return round(
        sum((bal or 0) * vmap.get((cur or "").strip().lower(), 0.0) / 100.0 for cur, bal in balances.items()),
        2,
    )


def _pipeline_rank_score(rank: object) -> int:
    try:
        value = int(rank)
    except (TypeError, ValueError):
        value = 999
    return -value


def _referral_reason(
    from_user: str,
    to_user: str,
    nc: dict,
    ref_pts: int | None,
    ref_cash: float | None,
    ref_val: float | None,
    family_route: bool = False,
    held_display_name: str | None = None,
) -> str:
    display_name = nc.get("display_name") or product_display_name(nc.get("issuer"), nc.get("product_name"))
    if family_route:
        # Name the card the referrer ACTUALLY holds — never claim they hold the
        # candidate product itself (they don't; the match is family-level).
        held_label = held_display_name or "a same-referral-family product"
        base = (
            f"{from_user} holds {held_label} - same referral family; have {from_user} send "
            f"{to_user} a referral link for {display_name} so the household can capture any "
            "issuer-supported referral bonus on top of the welcome offer"
        )
    else:
        base = (
            f"{from_user} already holds {display_name} - have {from_user} send "
            f"{to_user} a referral link so {to_user}'s application also earns {from_user} "
            "the referral bonus on top of the welcome offer"
        )
    if ref_pts or ref_cash:
        pieces = []
        if ref_pts:
            pieces.append(f"{ref_pts:,} pts")
        if ref_cash:
            pieces.append(f"${ref_cash:,.0f} cash")
        gain = f" (+{' + '.join(pieces)} to {from_user}"
        gain += f", ~${ref_val:,.0f})" if ref_val else ")"
        return base + gain + "."
    return base + ". Set this card's referral bonus in Card Plan to quantify the gain."


def _next_move_projection(
    db: Session,
    context: DecisionContext,
    ordered: list[dict],
    as_of: dt.date,
) -> dict:
    """Project one canonical move and two honest successors from a snapshot."""

    def conditional_successors(rows: list[dict], boundary: str) -> list[dict]:
        """Label every successor with the hypothetical boundary it depends on."""
        marked: list[dict] = []
        for row in rows[:2]:
            successor = dict(row)
            own_condition = successor.get("condition_reason")
            successor["conditional"] = True
            successor["condition_kind"] = successor.get("condition_kind") or "hypothetical_successor"
            successor["condition_reason"] = (
                f"{boundary} {own_condition}" if own_condition else boundary
            )
            marked.append(successor)
        return marked

    primary = ordered[0] if ordered else None
    if primary is None:
        return {"primary": None, "successors": []}

    if primary.get("status") == "WAIT":
        wait_until_raw = primary.get("earliest_eligible_date")
        if not wait_until_raw:
            return {
                "primary": primary,
                "successors": [],
                "successor_reason": (
                    "This WAIT has no recorded eligibility date; successors are withheld "
                    "until its timing condition is resolved."
                ),
            }
        try:
            wait_until = dt.date.fromisoformat(str(wait_until_raw))
        except ValueError:
            return {
                "primary": primary,
                "successors": [],
                "successor_reason": "The recorded WAIT date is invalid; successors are withheld.",
            }
        if wait_until <= as_of:
            return {
                "primary": primary,
                "successors": [],
                "successor_reason": (
                    "The WAIT date is no longer a future planning boundary; refresh eligibility "
                    "before projecting successors."
                ),
            }
        projected = build_household(
            db,
            context=context,
            include_next_move=False,
            as_of=wait_until,
            include_timing_waits=True,
        )
        return {
            "primary": primary,
            "successors": conditional_successors(
                projected["moves"],
                f"Conditional on waiting until {wait_until.isoformat()}.",
            ),
            "wait_until": wait_until.isoformat(),
        }

    product = context.products_by_id.get(primary.get("id"))
    candidate = dict(primary)
    if product is not None:
        candidate.update(
            {
                "ownership": product.ownership,
                "account_type": product.account_type,
                "reports_to_personal_credit": product.reports_to_personal_credit,
                "current_offer_min_spend": product.current_offer_min_spend,
                "current_offer_window_months": product.current_offer_window_months,
            }
        )
    # A clean or conditional application is still a declared hypothetical.  A
    # conditional primary carries its prerequisite while successors recompute
    # against the same synthetic commitment/5-24/reporting facts.
    projected_context = context.clone_for_hypothetical_application(
        primary["user"], candidate, as_of=as_of
    )

    projected = build_household(
        db,
        context=projected_context,
        include_next_move=False,
        as_of=as_of,
        include_timing_waits=True,
    )
    return {
        "primary": primary,
        "successors": conditional_successors(
            projected["moves"],
            f"Conditional on the hypothetical application of {primary.get('display_name') or primary.get('product_name')}."
            + (f" First: {primary['condition_reason']}" if primary.get("condition_reason") else ""),
        ),
        "successor_reason": (
            "No eligible successor was found after the hypothetical application."
            if not projected["moves"]
            else None
        ),
    }


def build_household(
    db: Session,
    context: DecisionContext | None = None,
    *,
    include_next_move: bool = True,
    excluded_move: tuple[str | None, int | None] | None = None,
    as_of: dt.date | None = None,
    include_timing_waits: bool = False,
) -> dict:
    users = config.USERS
    context = context or DecisionContext.load(db)
    as_of = as_of or dt.date.today()
    vmap = context.valuations
    products = context.products_by_id

    held_by_user = {u: list(context.held_by_user.get(u, [])) for u in users}
    active_by_user = {u: [h for h in held_by_user[u] if h.status != "Closed"] for u in users}
    active_keys = {
        u: {_key(h.issuer, h.product_name) for h in active_by_user[u]} for u in users
    }
    active_referral_keys = {
        u: set().union(*[_held_referral_keys(h, products) for h in active_by_user[u]])
        if active_by_user[u]
        else set()
        for u in users
    }
    balances_by_user = {u: dict(context.point_balances_by_user.get(u, {})) for u in users}
    pipelines = {
        u: pipeline_logic.build_pipeline(
            db,
            u,
            context=context,
            as_of=as_of,
            include_timing_waits=include_timing_waits,
        )
        for u in users
    }

    # --- Per-user summary ---------------------------------------------------
    user_summaries: list[dict] = []
    combined_value = 0.0
    for u in users:
        value = _value_of(balances_by_user[u], vmap)
        combined_value += value
        f24 = pipelines[u]["five_24"]
        annual_fees = round(sum(h.annual_fee or 0 for h in active_by_user[u]), 2)
        user_summaries.append(
            {
                "user": u,
                "under_524": f24["under_524"],
                "five24_count": f24["count"],
                "earliest_drop_date": f24["earliest_drop_date"],
                "total_est_value": value,
                "held_count": len(active_by_user[u]),
                "annual_fees": annual_fees,
            }
        )

    # --- Combined point balances table (per currency, per user, combined) ---
    currencies = sorted({c for bals in balances_by_user.values() for c in bals})
    balance_rows: list[dict] = []
    for currency in currencies:
        row = {"currency": currency, "by_user": {}, "combined": 0.0}
        for u in users:
            amt = balances_by_user[u].get(currency, 0) or 0
            row["by_user"][u] = amt
            row["combined"] += amt
        balance_rows.append(row)

    card_snapshot: dict[str, list[dict]] = {}
    for u in users:
        card_snapshot[u] = [
            {
                "id": h.id,
                "issuer": h.issuer,
                "product_name": h.product_name,
                "display_name": product_display_name(h.issuer, h.product_name),
                "date_opened": h.date_opened.isoformat() if h.date_opened else None,
                "renewal_date": h.renewal_date.isoformat() if h.renewal_date else None,
                "annual_fee": h.annual_fee,
                "status": h.status,
            }
            for h in active_by_user[u]
        ]

    # --- Referral opportunities --------------------------------------------
    referrals: list[dict] = []
    for to_user in users:
        for nc in pipelines[to_user]["next_cards"]:
            keys = _candidate_referral_keys(nc)
            for from_user in users:
                matched_keys = keys & active_referral_keys[from_user]
                if from_user == to_user or not matched_keys:
                    continue
                exact_route = any(key[0] in {"id", "key", "variant"} for key in matched_keys)
                candidate_family = family_key(nc["issuer"], nc["product_name"], nc.get("product_family"))
                # super_family matches (e.g. Ink Cash holder -> Ink Preferred applicant)
                # are family-level routes, never "exact" — the referrer does not hold
                # the candidate product itself.
                family_route = not exact_route and any(
                    key[0] in {"family", "super_family"} for key in matched_keys
                )
                super_family_only = family_route and all(
                    key[0] == "super_family" for key in matched_keys
                )
                super_family_name = next(
                    (key[1] for key in matched_keys if key[0] == "super_family"), None
                )
                held_display = None
                if family_route:
                    family_match_keys = {
                        key for key in matched_keys if key[0] in {"family", "super_family"}
                    }
                    held_match = next(
                        (
                            h
                            for h in active_by_user[from_user]
                            if _held_referral_keys(h, products) & family_match_keys
                        ),
                        None,
                    )
                    if held_match is not None:
                        held_display = product_display_name(held_match.issuer, held_match.product_name)
                if super_family_only and super_family_name:
                    route = f"Refer via {from_user} ({super_family_name} family)"
                elif family_route:
                    route = f"Refer via {from_user} (family)"
                else:
                    route = f"Refer via {from_user}"
                referral_family_key = (
                    f"{candidate_family[0]}:{candidate_family[1]}"
                    if candidate_family in REFERRAL_FAMILY_KEYS
                    else None
                )
                product = products.get(nc["id"])
                ref_pts = getattr(product, "referral_bonus_effective", None) if product else None
                ref_cash = getattr(product, "referral_bonus_cash", None) if product else None
                welcome_points = nc.get("effective_points") or (getattr(product, "current_offer_effective", None) if product else None)
                cpp = vmap.get((nc.get("currency") or "").strip().lower(), 0.0)
                ref_val = round(((ref_pts or 0) * cpp / 100.0) + (ref_cash or 0), 2)
                if not ref_pts and not ref_cash:
                    ref_val = None
                referrals.append(
                    {
                        "from_user": from_user,
                        "to_user": to_user,
                        "id": nc["id"],
                        "issuer": nc["issuer"],
                        "product_name": nc["product_name"],
                        "display_name": nc.get("display_name") or product_display_name(nc["issuer"], nc["product_name"]),
                        "currency": nc.get("currency"),
                        "welcome_points": welcome_points,
                        "current_offer_points": welcome_points,
                        "referral_bonus_points": ref_pts,
                        "referral_bonus_cash": ref_cash,
                        "household_points": (welcome_points or 0) + (ref_pts or 0),
                        "referral_value": ref_val,
                        "recipient_offer_value": nc["offer_value"],
                        "recipient_status": nc["status"],
                        "peak_score": nc["peak_score"],
                        "household_gain": round((nc["offer_value"] or 0) + (ref_val or 0), 2),
                        "pipeline_rank": nc.get("rank"),
                        "is_exceptional": nc.get("is_exceptional", False),
                        "decision_ready": nc.get("decision_ready", True),
                        "data_quality_issues": nc.get("data_quality_issues", []),
                        "offer_expiration": nc.get("offer_expiration"),
                        "current_offer_status": nc.get("current_offer_status"),
                        "current_offer_reason": nc.get("current_offer_reason"),
                        "current_offer_quality": nc.get("current_offer_quality"),
                        "conditional": nc.get("conditional", False),
                        "condition_kind": nc.get("condition_kind"),
                        "condition_reason": nc.get("condition_reason"),
                        "spend_capacity": nc.get("spend_capacity"),
                        "route": route,
                        "referral_match": "family" if family_route else "exact",
                        "referral_family_key": referral_family_key,
                        "reason": _referral_reason(
                            from_user, to_user, nc, ref_pts, ref_cash, ref_val, family_route, held_display
                        ),
                    }
                )

    def referral_sort_key(row: dict) -> tuple:
        # Household gain (welcome + referral value) is the primary value signal;
        # per-user pipeline rank is only a tiebreaker (PIPELINE_V2 change 1).
        return (
            STATUS_PRIORITY.get(row.get("recipient_status"), 0),
            1 if row.get("is_exceptional") else 0,
            row.get("household_gain") or 0,
            row.get("household_points") or 0,
            _pipeline_rank_score(row.get("pipeline_rank")),
            row.get("peak_score") or 0,
        )

    referrals.sort(key=referral_sort_key, reverse=True)
    all_referrals = list(referrals)

    def referral_group_key(row: dict) -> tuple:
        if row.get("referral_family_key"):
            return (row["from_user"], row["to_user"], "family", row["referral_family_key"])
        return (row["from_user"], row["to_user"], "exact", row["id"])

    collapsed_referrals: dict[tuple, dict] = {}
    for row in referrals:
        key = referral_group_key(row)
        if key not in collapsed_referrals:
            collapsed_referrals[key] = row
    referrals = sorted(collapsed_referrals.values(), key=referral_sort_key, reverse=True)

    # --- Quarterly application pace ------------------------------------------
    today = as_of
    quarter_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = dt.date(today.year, quarter_month, 1)
    total_quarter_apps = sum(
        1
        for u in users
        for h in active_by_user[u]
        if h.date_opened and h.date_opened >= quarter_start
    )
    at_pace_cap = total_quarter_apps >= config.MAX_APPS_PER_QUARTER

    # --- Merged "best next moves" (both applicants, paired by card) ---------
    # Keep first (best-ranked) referral for each (to_user, product_id) pair — all_referrals
    # is already sorted descending so the first write wins over later lower-value entries.
    referral_index: dict[tuple, dict] = {}
    for r in all_referrals:
        referral_index.setdefault((r["to_user"], r["id"]), r)
    moves: list[dict] = []
    actionable_statuses = {"APPLY NOW", "WATCH", "WAIT"}
    for u in users:
        under_524 = pipelines[u]["five_24"]["under_524"]
        for nc in pipelines[u]["next_cards"]:
            if nc["status"] not in actionable_statuses:
                continue
            if excluded_move and (u, nc["id"]) == excluded_move:
                continue
            ref = referral_index.get((u, nc["id"]))
            ref_val = ref["referral_value"] if ref and ref["referral_value"] else 0
            welcome_points = nc.get("effective_points") or 0
            referral_points = ref["referral_bonus_points"] if ref and ref.get("referral_bonus_points") else 0
            referral_cash = ref["referral_bonus_cash"] if ref and ref.get("referral_bonus_cash") else 0
            product = products.get(nc["id"])
            chase_urgent = _is_chase(nc["issuer"]) and under_524
            moves.append(
                {
                    "user": u,
                    "id": nc["id"],
                    "issuer": nc["issuer"],
                    "product_name": nc["product_name"],
                    "display_name": nc.get("display_name") or product_display_name(nc["issuer"], nc["product_name"]),
                    "ownership": nc["ownership"],
                    "account_type": nc.get("account_type") or getattr(product, "account_type", "Credit Card"),
                    "reports_to_personal_credit": nc.get(
                        "reports_to_personal_credit",
                        getattr(product, "reports_to_personal_credit", True),
                    ),
                    "currency": nc.get("currency"),
                    "status": nc["status"],
                    "peak_score": nc["peak_score"],
                    "offer_value": nc["offer_value"],
                    "first_year_value": nc["offer_value"],
                    "welcome_points": welcome_points,
                    "referral_bonus_points": referral_points,
                    "referral_bonus_cash": referral_cash,
                    "household_points": welcome_points + referral_points,
                    "current_offer_points": welcome_points or (getattr(product, "current_offer_effective", None) if product else None),
                     "current_offer_min_spend": getattr(product, "current_offer_min_spend", None) if product else None,
                     "current_offer_window_months": getattr(product, "current_offer_window_months", None) if product else None,
                     "offer_expiration": nc.get("offer_expiration"),
                     "current_offer_status": nc.get("current_offer_status"),
                     "current_offer_reason": nc.get("current_offer_reason"),
                     "current_offer_quality": nc.get("current_offer_quality"),
                    "earliest_eligible_date": nc.get("earliest_eligible_date"),
                    "is_exceptional": nc.get("is_exceptional", False),
                    "household_value": round((nc["offer_value"] or 0) + ref_val, 2),
                    "pipeline_rank": nc.get("rank"),
                    "decision_ready": nc.get("decision_ready", True),
                    "data_quality_issues": nc.get("data_quality_issues", []),
                    "conditional": nc.get("conditional", False),
                    "condition_kind": nc.get("condition_kind"),
                    "condition_reason": nc.get("condition_reason"),
                    "spend_capacity": nc.get("spend_capacity"),
                    "route": ref["route"] if ref else "Direct application",
                    "referral_from": ref["from_user"] if ref else None,
                    "referral_match": ref.get("referral_match") if ref else None,
                    "referral_value": ref["referral_value"] if ref else None,
                    "chase_urgent": chase_urgent,
                    "reason": (
                        nc["reason"] + (
                            f" Referral from {ref['from_user']} adds ~${ref_val:,.0f} to the household."
                            if ref_val
                            else f" Route via {ref['from_user']}'s referral link for bonus on top of welcome offer."
                        )
                        if ref else nc["reason"]
                    ) + (
                        f" Conditional: {nc['condition_reason']}"
                        if nc.get("condition_reason")
                        else ""
                    ),
                    "pace_warning": (
                        f"Household is at {total_quarter_apps}/{config.MAX_APPS_PER_QUARTER} "
                        "apps this quarter — confirm credit score / inquiry tolerance before applying."
                    ) if at_pace_cap else None,
                }
            )

    # Household uses the same strategic precedence as Pipeline.  Referral
    # capture is an explicit addition to the value term, so it can lift a move
    # without introducing a second strategic comparator.
    ordered = sorted(
        moves,
        key=lambda row: pipeline_logic.strategy_sort_key(
            row,
            under_524=bool(row.get("chase_urgent")),
            value=row.get("offer_value"),
            referral_value=row.get("referral_value") or 0,
            tie_points=row.get("household_points") or 0,
            status=row.get("status"),
            pipeline_rank=row.get("pipeline_rank"),
            user_position=users.index(row["user"]) if row.get("user") in users else 999,
        ),
        reverse=True,
    )

    result = {
        "users": user_summaries,
        "combined_est_value": round(combined_value, 2),
        "balance_rows": balance_rows,
        "card_snapshot": card_snapshot,
        "referrals": referrals,
        "moves": ordered[:config.HOUSEHOLD_MOVES_LIMIT],
        "quarter_apps": total_quarter_apps,
        "wallet": _wallet_efficiency(context, pipelines),
        "at_pace_cap": at_pace_cap,
        "max_apps_per_quarter": config.MAX_APPS_PER_QUARTER,
    }
    if include_next_move:
        result["next_move"] = _next_move_projection(db, context, ordered, as_of)
    return result


def _wallet_efficiency(context: DecisionContext, pipelines: dict) -> dict:
    """Household fee-vs-benefit balance so card bloat is visible at a glance.

    net = annualized benefit value (precise, structured-first) minus annual
    fees over ACTIVE held cards for both users. Cards with negative net are
    the downgrade/cancel shortlist — subject to the first-year guard.

    Reads held cards and products from the already-loaded DecisionContext
    (no fresh DB scans) and resolves each card's product via the context's
    id→key→variant resolver, so a held card whose exact product_id was deduped
    away still maps to its representative product rather than reading as $0.
    """
    from .pipeline import _annual_benefit_value

    held = [
        card
        for cards in context.held_by_user.values()
        for card in cards
        if card.user in config.USERS
        and (card.status or "").lower() not in ("closed", "cancelled", "canceled")
    ]
    total_fees = 0.0
    total_benefits = 0.0
    negative: list[dict] = []
    for card in held:
        product = context.product_for_card(card)
        fee = float(card.annual_fee or (product.annual_fee if product else 0) or 0)
        benefit_value = _annual_benefit_value(product)
        total_fees += fee
        total_benefits += benefit_value
        if fee > 0 and benefit_value - fee < 0:
            negative.append(
                {
                    "user": card.user,
                    "display_name": product_display_name(card.issuer, card.product_name),
                    "annual_fee": fee,
                    "annual_benefit_value": round(benefit_value, 2),
                    "net": round(benefit_value - fee, 2),
                }
            )
    negative.sort(key=lambda row: row["net"])
    return {
        "total_annual_fees": round(total_fees, 2),
        "total_annual_benefit_value": round(total_benefits, 2),
        "net_annual_value": round(total_benefits - total_fees, 2),
        "negative_net_cards": negative,
    }
