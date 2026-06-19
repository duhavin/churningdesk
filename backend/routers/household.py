"""Household view — combined two-user plan + referral synergy."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..logic import household as household_logic

router = APIRouter(prefix="/api", tags=["household"])


@router.get("/household")
def get_household(db: Session = Depends(get_db)):
    return household_logic.build_household(db)
