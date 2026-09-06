"""Profiles (PRIVATE) — current cards, computed 5/24, manual balances, value."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..crypto import MissingKeyError
from ..db import get_db
from ..logic import benefits as benefits_logic
from ..logic import categories as categories_logic
from ..logic import eligibility as elig
from ..logic.decision_context import DecisionContext

router = APIRouter(prefix="/api", tags=["profiles"])


def _safe_balances(profile: models.UserProfile | None) -> dict | None:
    if not profile:
        return None
    try:
        return profile.point_balances
    except MissingKeyError:
        return None


def _profile_summary(db: Session, user: str, context: DecisionContext | None = None) -> dict:
    context = context or DecisionContext.load(db)
    try:
        profile = db.get(models.UserProfile, user)
    except MissingKeyError:
        # Keep the profile surface readable when an encrypted optional field
        # cannot be decrypted; the decision context already treats capacity and
        # balances as unknown under the same key boundary.
        profile = None
    balances = _safe_balances(profile) or {}
    vmap = context.valuations

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

    held = list(context.held_by_user.get(user, []))
    f24 = elig.five24(db, user, held=held)
    held_total = len(held)

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
        "organic_monthly_capacity": profile.organic_monthly_capacity if profile else None,
        "category_coverage": categories_logic.build_user_category_coverage(db, user, context=context),
        "benefit_tracker": benefits_logic.build_user_benefit_tracker(db, user, context=context),
        "notes": profile.notes if profile else None,
    }


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db)):
    context = DecisionContext.load(db)
    return [_profile_summary(db, u, context=context) for u in config.USERS]


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
        if "organic_monthly_capacity" in payload.model_fields_set:
            profile.organic_monthly_capacity = payload.organic_monthly_capacity
        if payload.notes is not None:
            profile.notes = payload.notes
        db.commit()
        return _profile_summary(db, user)
    except MissingKeyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/profiles/{user}/benefits")
def upsert_profile_benefit_usage(
    user: str,
    payload: schemas.BenefitUsageUpsert,
    db: Session = Depends(get_db),
):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    try:
        benefits_logic.upsert_benefit_usage(db, user, payload)
        return benefits_logic.build_user_benefit_tracker(db, user)
    except MissingKeyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
