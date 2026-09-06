"""Application pipeline (§8) endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..logic import household as household_logic
from ..logic import pipeline as pipeline_logic
from ..logic.decision_context import DecisionContext

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.get("/pipeline")
def get_pipeline(user: str, db: Session = Depends(get_db)):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    context = DecisionContext.load(db)
    result = pipeline_logic.build_pipeline(db, user, context=context)
    # Pipeline and Household expose one derived next move so each surface
    # names the same primary application and recomputed successors.
    result["household_next_move"] = household_logic.build_household(
        db, context=context
    )["next_move"]
    return result
