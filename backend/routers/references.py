"""Card reference registry endpoints (PUBLIC identity/source hints)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import card_references, models, schemas
from ..db import get_db

router = APIRouter(prefix="/api/card-references", tags=["card-references"])


@router.get("")
def list_card_references(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.CardReference).order_by(models.CardReference.issuer, models.CardReference.display_name)
    ).all()
    return [card_references.reference_to_dict(row) for row in rows]


@router.post("/seed")
def seed_references(db: Session = Depends(get_db)):
    return card_references.seed_card_references(db)


@router.put("/{reference_id}")
def update_reference(
    reference_id: int,
    payload: schemas.CardReferenceUpdate,
    db: Session = Depends(get_db),
):
    reference = db.get(models.CardReference, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Card reference not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(reference, field, value)
    db.commit()
    db.refresh(reference)
    return card_references.reference_to_dict(reference)
