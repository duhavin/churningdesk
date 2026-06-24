"""Household spend-category guidance.

Computes "what to use where" from cards the household actually holds. This is
decision logic only: no scraping, no LLM, and no private data leaves the app.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models
from ..product_identity import product_display_name, product_variant_key
from . import catalog as catalog_logic

if TYPE_CHECKING:
    from .decision_context import DecisionContext

CATEGORIES: dict[str, tuple[str, ...]] = {
    "dining": ("dining", "restaurant", "restaurants"),
    "groceries": ("grocery", "groceries", "supermarket", "supermarkets"),
    "travel": ("travel", "airfare", "flight", "flights", "hotel", "hotels", "transit"),
    "everyday": ("everyday", "day to day", "daily", "all purchases", "everything else", "non bonus", "non-bonus"),
    "gas": ("gas", "fuel", "gas station", "gas stations"),
}


def _key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


def _category_match(raw_category: str, category: str) -> bool:
    low = (raw_category or "").strip().lower()
    return any(alias in low for alias in CATEGORIES[category])


def _multiplier_value(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"(\d+(?:\.\d+)?)\s*x", value.lower())
        if match:
            return float(match.group(1))
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _format_multiplier(multiplier: float) -> str:
    if multiplier.is_integer():
        return f"{int(multiplier)}x"
    return f"{multiplier:g}x"


def _cpp(vmap: dict[str, float], currency: str | None) -> float | None:
    if not currency:
        return None
    low = currency.strip().lower()
    if low in {"cash", "cash back", "cashback", "usd"}:
        return 1.0
    return vmap.get(low)


def _product_for_card(
    card: models.HeldCard,
    by_id: dict[int, models.CardProduct],
    by_key: dict[tuple[str, str], models.CardProduct],
    products: list[models.CardProduct],
) -> models.CardProduct | None:
    if card.product_id and card.product_id in by_id:
        return by_id[card.product_id]
    exact = by_key.get(_key(card.issuer, card.product_name))
    if exact:
        return exact
    variant = product_variant_key(card.issuer, card.product_name)
    if not variant:
        return None
    for product in products:
        if product_variant_key(product.issuer, product.product_name) == variant:
            return product
    return None


def _category_multiplier(product: models.CardProduct, category: str) -> tuple[float | None, str | None, str | None]:
    multipliers = product.earn_multipliers if isinstance(product.earn_multipliers, dict) else {}
    uses = product.best_category_uses if isinstance(product.best_category_uses, dict) else {}
    use_note = next(
        (str(raw_note) for raw_category, raw_note in uses.items() if _category_match(str(raw_category), category)),
        None,
    )

    for raw_category, raw_multiplier in multipliers.items():
        if _category_match(str(raw_category), category):
            multiplier = _multiplier_value(raw_multiplier)
            if multiplier is not None:
                return multiplier, use_note or _format_multiplier(multiplier), "earn_multipliers"

    for raw_category, raw_note in uses.items():
        if _category_match(str(raw_category), category):
            multiplier = _multiplier_value(raw_note)
            if multiplier is not None:
                return multiplier, str(raw_note), "best_category_uses"
            return None, str(raw_note), "best_category_uses"

    return None, None, None


def _candidate(
    card: models.HeldCard,
    product: models.CardProduct,
    category: str,
    vmap: dict[str, float],
) -> dict | None:
    multiplier, note, source = _category_multiplier(product, category)
    if multiplier is None:
        return None
    cpp = _cpp(vmap, product.currency)
    if cpp is None:
        return {
            "needs_data": True,
            "reason": f"Missing valuation for {product.currency or 'unknown currency'}.",
            "user": card.user,
            "card_id": card.id,
            "product_id": product.id,
            "issuer": card.issuer,
            "product_name": card.product_name,
            "display_name": product_display_name(card.issuer, card.product_name),
            "currency": product.currency,
            "multiplier": multiplier,
            "cpp": None,
            "effective_rate_cents": None,
            "value_per_dollar": None,
            "note": note,
            "source": source,
            "source_category": category,
            "is_fallback": False,
            "fallback_reason": None,
        }
    effective_rate_cents = multiplier * cpp
    return {
        "needs_data": False,
        "user": card.user,
        "card_id": card.id,
        "product_id": product.id,
        "issuer": card.issuer,
        "product_name": card.product_name,
        "display_name": product_display_name(card.issuer, card.product_name),
        "currency": product.currency,
        "multiplier": multiplier,
        "cpp": cpp,
        "effective_rate_cents": round(effective_rate_cents, 3),
        "value_per_dollar": round(effective_rate_cents / 100.0, 4),
        "note": note,
        "source": source,
        "source_category": category,
        "is_fallback": False,
        "fallback_reason": None,
    }


def _fallback_candidate(candidate: dict, category: str) -> dict:
    out = dict(candidate)
    out["source_category"] = "everyday"
    out["is_fallback"] = True
    out["fallback_reason"] = f"No verified {category} multiplier; using best everyday card."
    return out


def _category_candidates_with_everyday_baseline(
    category: str,
    candidates_by_category: dict[str, list[dict]],
) -> list[dict]:
    candidates = list(candidates_by_category[category])
    if category == "everyday":
        return candidates

    candidates.extend(_fallback_candidate(candidate, category) for candidate in candidates_by_category["everyday"])
    candidates.sort(key=lambda c: (c["effective_rate_cents"] or 0, c["multiplier"] or 0), reverse=True)
    deduped: list[dict] = []
    seen_card_ids: set[int] = set()
    for candidate in candidates:
        card_id = candidate.get("card_id")
        if card_id in seen_card_ids:
            continue
        seen_card_ids.add(card_id)
        deduped.append(candidate)
    return deduped


def _active_held_cards(
    db: Session,
    context: "DecisionContext | None" = None,
    user: str | None = None,
) -> list[models.HeldCard]:
    if context:
        if user:
            return list(context.active_held_by_user.get(user, []))
        return [card for cards in context.active_held_by_user.values() for card in cards]
    stmt = select(models.HeldCard).where(models.HeldCard.status != "Closed")
    if user:
        stmt = stmt.where(models.HeldCard.user == user)
    else:
        stmt = stmt.where(models.HeldCard.user.in_(config.USERS))
    return list(db.scalars(stmt).all())


def build_category_guide(
    db: Session,
    context: "DecisionContext | None" = None,
    user: str | None = None,
) -> dict:
    vmap = context.valuations if context else catalog_logic.valuation_map(db)
    products = list(context.products if context else db.scalars(select(models.CardProduct)).all())
    by_id = {p.id: p for p in products}
    by_key = {_key(p.issuer, p.product_name): p for p in products}
    held = _active_held_cards(db, context=context, user=user)

    rows: list[dict] = []
    candidates_by_category: dict[str, list[dict]] = {category: [] for category in CATEGORIES}
    missing_by_category: dict[str, list[dict]] = {category: [] for category in CATEGORIES}
    for card in held:
        product = _product_for_card(card, by_id, by_key, products)
        if not product:
            continue
        for category in CATEGORIES:
            candidate = _candidate(card, product, category, vmap)
            if not candidate:
                continue
            if candidate["needs_data"]:
                missing_by_category[category].append(candidate)
            else:
                candidates_by_category[category].append(candidate)

    for candidates in candidates_by_category.values():
        candidates.sort(key=lambda c: (c["effective_rate_cents"] or 0, c["multiplier"] or 0), reverse=True)

    for category in CATEGORIES:
        candidates = _category_candidates_with_everyday_baseline(category, candidates_by_category)
        missing = missing_by_category[category]
        rows.append(
            {
                "category": category,
                "status": "covered" if candidates else "needs_data",
                "winner": candidates[0] if candidates else None,
                "runner_up": candidates[1] if len(candidates) > 1 else None,
                "needs_data_reason": None
                if candidates
                else (
                    missing[0]["reason"]
                    if missing
                    else "No active held card has verified multiplier data for this category."
                ),
                "needs_data_candidates": missing,
            }
        )

    return {"user": user, "scope": user or "household", "categories": rows}


def build_user_category_coverage(
    db: Session,
    user: str,
    context: "DecisionContext | None" = None,
) -> list[dict]:
    """Profile-facing category coverage from the same engine as Household."""
    guide = build_category_guide(db, context=context, user=user)
    rows: list[dict] = []
    for item in guide["categories"]:
        winner = item.get("winner")
        if winner:
            rows.append(
                {
                    "category": item["category"],
                    "covered": True,
                    "issuer": winner.get("issuer"),
                    "product_name": winner.get("product_name"),
                    "display_name": winner.get("display_name"),
                    "product_id": winner.get("product_id"),
                    "multiplier": winner.get("multiplier"),
                    "cpp": winner.get("cpp"),
                    "effective_rate_cents": winner.get("effective_rate_cents"),
                    "value_per_dollar": winner.get("value_per_dollar"),
                    "note": winner.get("note"),
                    "source_category": winner.get("source_category"),
                    "is_fallback": winner.get("is_fallback", False),
                    "fallback_reason": winner.get("fallback_reason"),
                    "needs_data_reason": item.get("needs_data_reason"),
                }
            )
        else:
            rows.append(
                {
                    "category": item["category"],
                    "covered": False,
                    "issuer": None,
                    "product_name": None,
                    "display_name": None,
                    "product_id": None,
                    "multiplier": None,
                    "cpp": None,
                    "effective_rate_cents": None,
                    "value_per_dollar": None,
                    "note": None,
                    "source_category": None,
                    "is_fallback": False,
                    "fallback_reason": None,
                    "needs_data_reason": item.get("needs_data_reason"),
                }
            )
    return rows
