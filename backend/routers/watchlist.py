"""Watchlist (PUBLIC) — the manual backstop for discovery (§4.3).

Adding a watchlist entry also seeds a catalog product (added_by=user_watchlist)
if one doesn't already exist, so it shows up in the Card Plan immediately.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..logic import catalog as catalog_logic
from ..product_identity import canonical_product_key, product_display_name

router = APIRouter(prefix="/api", tags=["watchlist"])


@router.get("/watchlist")
def list_watchlist(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.CardWatchlist)).all()
    return [
        {
            "id": w.id,
            "issuer": w.issuer,
            "product_name": w.product_name,
            "display_name": product_display_name(w.issuer, w.product_name),
            "canonical_key": canonical_product_key(w.issuer, w.product_name),
            "priority": w.priority,
            "added_by": w.added_by,
            "active": w.active,
            "notes": w.notes,
        }
        for w in rows
    ]


@router.post("/watchlist")
def add_watchlist(payload: schemas.WatchlistCreate, db: Session = Depends(get_db)):
    entry = models.CardWatchlist(
        issuer=payload.issuer,
        product_name=payload.product_name,
        priority=payload.priority,
        added_by="user",
        active=True,
        notes=payload.notes,
    )
    db.add(entry)

    # Seed/reuse a catalog product by canonical identity so short/manual names
    # do not create duplicate same-card rows.
    catalog_logic.upsert_product_by_identity(
        db,
        {
            "issuer": payload.issuer,
            "product_name": payload.product_name,
            "added_by": "user_watchlist",
            "discovery_reviewed": True,
        },
    )

    db.commit()
    db.refresh(entry)
    return {
        "id": entry.id,
        "issuer": entry.issuer,
        "product_name": entry.product_name,
        "display_name": product_display_name(entry.issuer, entry.product_name),
        "canonical_key": canonical_product_key(entry.issuer, entry.product_name),
    }


@router.delete("/watchlist/{entry_id}")
def remove_watchlist(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(models.CardWatchlist, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
