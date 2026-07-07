"""Redemption goals, transfer partners, and household progress."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..logic.redemption.engine import build_redemption_plan

router = APIRouter(prefix="/api", tags=["redemption"])


@router.get("/redemption")
def get_redemption_plan(db: Session = Depends(get_db)):
    return build_redemption_plan(db)


@router.post("/redemption/targets")
def create_target(payload: schemas.TargetRedemptionCreate, db: Session = Depends(get_db)):
    target = models.TargetRedemption(**payload.model_dump())
    db.add(target)
    db.commit()
    db.refresh(target)
    return build_redemption_plan(db)


@router.put("/redemption/targets/{target_id}")
def update_target(target_id: int, payload: schemas.TargetRedemptionUpdate, db: Session = Depends(get_db)):
    target = db.get(models.TargetRedemption, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target redemption not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    db.commit()
    return build_redemption_plan(db)


@router.delete("/redemption/targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db)):
    target = db.get(models.TargetRedemption, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target redemption not found")
    db.delete(target)
    db.commit()
    return build_redemption_plan(db)


@router.post("/redemption/transfer-bonuses/research")
def research_transfer_bonuses(force: bool = False, db: Session = Depends(get_db)):
    """Sourced suggestions for CURRENT transfer bonuses (cached web search).

    Read-only: the UI applies chosen suggestions via the normal
    transfer-partner update endpoint, so a human approves every change.
    """
    from ..logic.redemption.bonus_research import research_transfer_bonuses as run

    return run(db, force=force)


@router.post("/redemption/transfer-partners")
def create_transfer_partner(payload: schemas.TransferPartnerCreate, db: Session = Depends(get_db)):
    partner = models.TransferPartner(**payload.model_dump())
    db.add(partner)
    db.commit()
    return build_redemption_plan(db)


@router.put("/redemption/transfer-partners/{partner_id}")
def update_transfer_partner(partner_id: int, payload: schemas.TransferPartnerUpdate, db: Session = Depends(get_db)):
    partner = db.get(models.TransferPartner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Transfer partner not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(partner, field, value)
    db.commit()
    return build_redemption_plan(db)


@router.delete("/redemption/transfer-partners/{partner_id}")
def delete_transfer_partner(partner_id: int, db: Session = Depends(get_db)):
    partner = db.get(models.TransferPartner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Transfer partner not found")
    db.delete(partner)
    db.commit()
    return build_redemption_plan(db)
