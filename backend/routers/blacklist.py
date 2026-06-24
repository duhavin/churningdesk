"""Blacklist (PUBLIC) — cards excluded everywhere (§4.3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..product_identity import canonical_product_key, product_display_name

router = APIRouter(prefix="/api", tags=["blacklist"])


@router.get("/blacklist")
def list_blacklist(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.CardBlacklist).order_by(models.CardBlacklist.created_at.desc())
    ).all()
    return [
        {
            "id": b.id,
            "issuer": b.issuer,
            "product_name": b.product_name,
            "display_name": product_display_name(b.issuer, b.product_name),
            "canonical_key": canonical_product_key(b.issuer, b.product_name),
            "reason": b.reason,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in rows
    ]


@router.post("/blacklist")
def add_blacklist(payload: schemas.BlacklistCreate, db: Session = Depends(get_db)):
    entry = models.CardBlacklist(
        issuer=payload.issuer,
        product_name=payload.product_name,
        reason=payload.reason,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {
        "id": entry.id,
        "issuer": entry.issuer,
        "product_name": entry.product_name,
        "display_name": product_display_name(entry.issuer, entry.product_name),
        "canonical_key": canonical_product_key(entry.issuer, entry.product_name),
    }


@router.delete("/blacklist/{entry_id}")
def remove_blacklist(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(models.CardBlacklist, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Blacklist entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
