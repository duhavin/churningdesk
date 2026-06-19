"""Profiles (PRIVATE) — current cards, computed 5/24, manual balances, value."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..crypto import MissingKeyError
from ..db import get_db
from ..logic import catalog as catalog_logic
from ..logic import eligibility as elig

router = APIRouter(prefix="/api", tags=["profiles"])

CATEGORY_ALIASES = {
    "dining": ("dining", "restaurant", "restaurants"),
    "groceries": ("grocery", "groceries", "supermarket", "supermarkets"),
    "travel": ("travel", "airfare", "flights", "hotel", "hotels", "transit"),
    "gas": ("gas", "fuel", "gas station", "gas stations"),
    "everyday": ("everyday", "day to day", "daily", "other", "all purchases", "everything else"),
}


def _safe_balances(profile: models.UserProfile | None) -> dict | None:
    if not profile:
        return None
    try:
        return profile.point_balances
    except MissingKeyError:
        return None


def _key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


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


def _category_match(raw_category: str, category: str) -> bool:
    low = (raw_category or "").strip().lower()
    return any(alias in low for alias in CATEGORY_ALIASES[category])


def _category_coverage(db: Session, user: str) -> list[dict]:
    held = [
        h
        for h in db.scalars(
            select(models.HeldCard).where(
                models.HeldCard.user == user,
                models.HeldCard.status != "Closed",
            )
        ).all()
    ]
    products = db.scalars(select(models.CardProduct)).all()
    by_id = {p.id: p for p in products}
    by_key = {_key(p.issuer, p.product_name): p for p in products}

    best: dict[str, dict] = {}
    for card in held:
        product = by_id.get(card.product_id) if card.product_id else None
        product = product or by_key.get(_key(card.issuer, card.product_name))
        if not product:
            continue

        multipliers = product.earn_multipliers if isinstance(product.earn_multipliers, dict) else {}
        uses = product.best_category_uses if isinstance(product.best_category_uses, dict) else {}
        for category in CATEGORY_ALIASES:
            multiplier = None
            note = None
            for raw_category, raw_multiplier in multipliers.items():
                if _category_match(str(raw_category), category):
                    multiplier = _multiplier_value(raw_multiplier)
                    note = f"{raw_multiplier}x" if isinstance(raw_multiplier, (int, float)) else str(raw_multiplier)
                    break
            for raw_category, raw_note in uses.items():
                if _category_match(str(raw_category), category):
                    note = str(raw_note)
                    multiplier = multiplier or _multiplier_value(raw_note)
                    break
            if multiplier is None and not note:
                continue
            candidate = {
                "category": category,
                "covered": True,
                "issuer": card.issuer,
                "product_name": card.product_name,
                "product_id": product.id,
                "multiplier": multiplier,
                "note": note,
            }
            current = best.get(category)
            if current is None or (candidate["multiplier"] or 0) > (current.get("multiplier") or 0):
                best[category] = candidate

    return [
        best.get(category)
        or {
            "category": category,
            "covered": False,
            "issuer": None,
            "product_name": None,
            "product_id": None,
            "multiplier": None,
            "note": None,
        }
        for category in CATEGORY_ALIASES
    ]


def _profile_summary(db: Session, user: str) -> dict:
    profile = db.get(models.UserProfile, user)
    balances = _safe_balances(profile) or {}
    vmap = catalog_logic.valuation_map(db)

    total_value = 0.0
    breakdown = []
    for currency, balance in balances.items():
        cpp = vmap.get(currency.strip().lower(), 0.0)
        # cpp is cents-per-point; divide by 100 for dollars (matches scoring.py).
        value = (balance or 0) * cpp / 100.0
        total_value += value
        breakdown.append(
            {"currency": currency, "balance": balance, "cpp": cpp, "value": round(value, 2)}
        )

    f24 = elig.five24(db, user)
    held_total = len(
        db.scalars(
            select(models.HeldCard.id).where(models.HeldCard.user == user)
        ).all()
    )

    return {
        "user": user,
        "point_balances": balances,
        "balance_breakdown": breakdown,
        "total_est_value": round(total_value, 2),
        "five_24": {
            "count": f24.count,
            "under_524": f24.count < 5,
            "earliest_drop_date": f24.earliest_drop_date.isoformat()
            if f24.earliest_drop_date
            else None,
            "contributing": f24.contributing,
        },
        "held_count": held_total,
        "category_coverage": _category_coverage(db, user),
        "notes": profile.notes if profile else None,
    }


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db)):
    return [_profile_summary(db, u) for u in config.USERS]


@router.get("/profiles/{user}")
def get_profile(user: str, db: Session = Depends(get_db)):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    return _profile_summary(db, user)


@router.put("/profiles/{user}")
def upsert_profile(
    user: str, payload: schemas.ProfileUpsert, db: Session = Depends(get_db)
):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    try:
        profile = db.get(models.UserProfile, user)
        if not profile:
            profile = models.UserProfile(user=user)
            db.add(profile)
        if payload.point_balances is not None:
            profile.point_balances = payload.point_balances
        if payload.notes is not None:
            profile.notes = payload.notes
        db.commit()
        return _profile_summary(db, user)
    except MissingKeyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
