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

from sqlalchemy.orm import Session

from .. import config, models
from ..product_identity import product_display_name, product_variant_key
from . import pipeline as pipeline_logic
from .decision_context import DecisionContext

STATUS_PRIORITY = {"APPLY NOW": 3, "WATCH": 2, "WAIT": 1}


def _key(issuer: str | None, name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (name or "").strip().lower())


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
    return keys


def _candidate_referral_keys(nc: dict) -> set[tuple[str, int | tuple[str, str]]]:
    keys: set[tuple[str, int | tuple[str, str]]] = {
        ("id", nc["id"]),
        ("key", _key(nc["issuer"], nc["product_name"])),
    }
    variant = product_variant_key(nc["issuer"], nc["product_name"])
    if variant:
        keys.add(("variant", variant))
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
) -> str:
    display_name = nc.get("display_name") or product_display_name(nc.get("issuer"), nc.get("product_name"))
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


def build_household(db: Session) -> dict:
    users = config.USERS
    context = DecisionContext.load(db)
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
    pipelines = {u: pipeline_logic.build_pipeline(db, u, context=context) for u in users}

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
                if from_user == to_user or not (keys & active_referral_keys[from_user]):
                    continue
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
                        "reason": _referral_reason(from_user, to_user, nc, ref_pts, ref_cash, ref_val),
                    }
                )

    def referral_sort_key(row: dict) -> tuple:
        return (
            STATUS_PRIORITY.get(row.get("recipient_status"), 0),
            1 if row.get("is_exceptional") else 0,
            _pipeline_rank_score(row.get("pipeline_rank")),
            row.get("household_gain") or 0,
            row.get("household_points") or 0,
            row.get("peak_score") or 0,
        )

    referrals.sort(key=referral_sort_key, reverse=True)

    # --- Merged "best next moves" (both applicants, paired by card) ---------
    referral_index = {(r["to_user"], r["id"]): r for r in referrals}
    moves: list[dict] = []
    actionable_statuses = {"APPLY NOW", "WATCH", "WAIT"}
    for u in users:
        for nc in pipelines[u]["next_cards"]:
            if nc["status"] not in actionable_statuses:
                continue
            ref = referral_index.get((u, nc["id"]))
            ref_val = ref["referral_value"] if ref and ref["referral_value"] else 0
            welcome_points = nc.get("effective_points") or 0
            referral_points = ref["referral_bonus_points"] if ref and ref.get("referral_bonus_points") else 0
            referral_cash = ref["referral_bonus_cash"] if ref and ref.get("referral_bonus_cash") else 0
            product = products.get(nc["id"])
            moves.append(
                {
                    "user": u,
                    "id": nc["id"],
                    "issuer": nc["issuer"],
                    "product_name": nc["product_name"],
                    "display_name": nc.get("display_name") or product_display_name(nc["issuer"], nc["product_name"]),
                    "ownership": nc["ownership"],
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
                    "is_exceptional": nc.get("is_exceptional", False),
                    "household_value": round((nc["offer_value"] or 0) + ref_val, 2),
                    "pipeline_rank": nc.get("rank"),
                    "route": f"Refer via {ref['from_user']}" if ref else "Direct application",
                    "referral_from": ref["from_user"] if ref else None,
                    "referral_value": ref["referral_value"] if ref else None,
                    "reason": nc["reason"],
                }
            )

    # Preserve Pipeline's decision ranking instead of alphabetizing or regrouping
    # by product. Household value and points break close calls.
    def move_sort_key(row: dict) -> tuple:
        return (
            STATUS_PRIORITY.get(row.get("status"), 0),
            1 if row.get("is_exceptional") else 0,
            _pipeline_rank_score(row.get("pipeline_rank")),
            row.get("household_value") or 0,
            row.get("household_points") or 0,
            row.get("peak_score") or 0,
            -users.index(row["user"]) if row.get("user") in users else -999,
        )

    ordered = sorted(moves, key=move_sort_key, reverse=True)

    return {
        "users": user_summaries,
        "combined_est_value": round(combined_value, 2),
        "balance_rows": balance_rows,
        "card_snapshot": card_snapshot,
        "referrals": referrals,
        "moves": ordered[:16],
    }
