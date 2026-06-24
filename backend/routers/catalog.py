"""Catalog: the scored Card Plan view, product CRUD, and valuations (PUBLIC)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..crypto import MissingKeyError
from ..db import get_db
from ..logic import catalog as catalog_logic
from ..logic import catalog_cleanup
from ..logic.catalog_health import build_catalog_health
from ..logic.decision_context import DecisionContext
from ..product_identity import derive_product_family

router = APIRouter(prefix="/api", tags=["catalog"])


def _utcnow() -> dt.datetime:
    # Naive UTC (utcnow() is deprecated on 3.12+); matches naive DateTime columns.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@router.get("/catalog")
def scored_catalog(user: str, db: Session = Depends(get_db)):
    """Full effective catalog with per-user eligibility, peak_score, status, rank."""
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    return catalog_logic.scored_catalog(db, user)


@router.get("/catalog-health")
def catalog_health(db: Session = Depends(get_db)):
    return build_catalog_health(db, context=DecisionContext.load(db))


@router.get("/catalog/duplicates")
def catalog_duplicates(db: Session = Depends(get_db)):
    return {"groups": catalog_cleanup.duplicate_product_groups(db)}


@router.post("/catalog/duplicates/merge")
def merge_catalog_duplicates(db: Session = Depends(get_db)):
    return catalog_cleanup.merge_duplicate_products(db)


@router.post("/catalog")
def create_product(payload: schemas.CardProductCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    product, _created = catalog_logic.upsert_product_by_identity(db, data)
    db.commit()
    db.refresh(product)
    return catalog_logic.product_to_dict(product)


@router.put("/catalog/{product_id}")
def update_product(
    product_id: int, payload: schemas.CardProductUpdate, db: Session = Depends(get_db)
):
    product = db.get(models.CardProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(product, field, value)
    if "product_family" not in data and ("issuer" in data or "product_name" in data):
        product.product_family = derive_product_family(product.issuer, product.product_name)
    product.last_verified = _utcnow()
    db.commit()
    db.refresh(product)
    return catalog_logic.product_to_dict(product)


@router.delete("/catalog/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(models.CardProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(product)
    db.commit()
    return {"ok": True}


# --- Manual targeted offers (PRIVATE) --------------------------------------
def _targeted_offer_to_dict(offer: models.ManualTargetedOffer) -> dict:
    try:
        points = offer.offer_points
        cash = offer.offer_cash
    except MissingKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": offer.id,
        "user": offer.user,
        "issuer": offer.issuer,
        "product_name": offer.product_name,
        "product_id": offer.product_id,
        "offer_points": points,
        "offer_cash": cash,
        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
        "notes": offer.notes,
        "created_at": offer.created_at.isoformat() if offer.created_at else None,
        "updated_at": offer.updated_at.isoformat() if offer.updated_at else None,
    }


def _targeted_offer_query(db: Session, user: str, product: models.CardProduct):
    return select(models.ManualTargetedOffer).where(
        models.ManualTargetedOffer.user == user,
        models.ManualTargetedOffer.issuer == product.issuer,
        models.ManualTargetedOffer.product_name == product.product_name,
    )


@router.get("/catalog/{product_id}/targeted-offer")
def get_targeted_offer(product_id: int, user: str, db: Session = Depends(get_db)):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    product = db.get(models.CardProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    offer = db.scalar(_targeted_offer_query(db, user, product))
    if offer and offer.expires_at and offer.expires_at < dt.date.today():
        db.delete(offer)
        db.commit()
        offer = None
    return _targeted_offer_to_dict(offer) if offer else None


@router.put("/catalog/{product_id}/targeted-offer")
def upsert_targeted_offer(
    product_id: int,
    user: str,
    payload: schemas.ManualTargetedOfferUpsert,
    db: Session = Depends(get_db),
):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    product = db.get(models.CardProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    offer = db.scalar(_targeted_offer_query(db, user, product))
    data = payload.model_dump()
    try:
        if data.get("expires_at") and data["expires_at"] < dt.date.today():
            if offer:
                db.delete(offer)
                db.commit()
            return None
        if offer is None:
            offer = models.ManualTargetedOffer(
                user=user,
                issuer=product.issuer,
                product_name=product.product_name,
                product_id=product.id,
                offer_points=data.get("offer_points"),
                offer_cash=data.get("offer_cash"),
                expires_at=data.get("expires_at"),
                notes=data.get("notes"),
            )
            db.add(offer)
        else:
            offer.product_id = product.id
            offer.offer_points = data.get("offer_points")
            offer.offer_cash = data.get("offer_cash")
            offer.expires_at = data.get("expires_at")
            offer.notes = data.get("notes")
        db.commit()
        db.refresh(offer)
        return _targeted_offer_to_dict(offer)
    except MissingKeyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/catalog/{product_id}/targeted-offer")
def delete_targeted_offer(product_id: int, user: str, db: Session = Depends(get_db)):
    if user not in config.USERS:
        raise HTTPException(status_code=400, detail=f"Unknown user '{user}'")
    product = db.get(models.CardProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    offer = db.scalar(_targeted_offer_query(db, user, product))
    if offer:
        db.delete(offer)
        db.commit()
    return {"ok": True}


# --- Valuations -------------------------------------------------------------
@router.get("/valuations")
def list_valuations(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Valuation).order_by(models.Valuation.currency)).all()
    return [
        {
            "id": v.id,
            "currency": v.currency,
            "cpp_scraped": v.cpp_scraped,
            "cpp_override": v.cpp_override,
            "cpp_effective": v.cpp_effective,
            "source_url": v.source_url,
            "last_verified": v.last_verified.isoformat() if v.last_verified else None,
        }
        for v in rows
    ]


@router.put("/valuations")
def upsert_valuation(payload: schemas.ValuationUpsert, db: Session = Depends(get_db)):
    existing = db.scalar(
        select(models.Valuation).where(
            models.Valuation.currency == payload.currency
        )
    )
    if existing:
        if payload.cpp_scraped is not None:
            existing.cpp_scraped = payload.cpp_scraped
        if payload.cpp_override is not None:
            existing.cpp_override = payload.cpp_override
        if payload.source_url is not None:
            existing.source_url = payload.source_url
        existing.last_verified = _utcnow()
        v = existing
    else:
        v = models.Valuation(
            currency=payload.currency,
            cpp_scraped=payload.cpp_scraped,
            cpp_override=payload.cpp_override,
            source_url=payload.source_url,
            last_verified=_utcnow(),
        )
        db.add(v)
    db.commit()
    db.refresh(v)
    return {"currency": v.currency, "cpp_effective": v.cpp_effective}
