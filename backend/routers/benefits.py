"""Household benefits ledger."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..logic.benefits import build_benefits_ledger

router = APIRouter(prefix="/api", tags=["benefits"])


@router.get("/benefits")
def benefits(db: Session = Depends(get_db)):
    return build_benefits_ledger(db)
