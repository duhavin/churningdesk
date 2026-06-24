"""Household category guidance."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..logic import categories as categories_logic
from ..logic.decision_context import DecisionContext

router = APIRouter(prefix="/api", tags=["categories"])


@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    return categories_logic.build_category_guide(db, context=DecisionContext.load(db))
