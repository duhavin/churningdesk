"""Card Universe / Review admin: proposed-change queue, discovered-card review,
source config. PUBLIC tables only (§10 tab 5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from urllib.parse import urlsplit

from .. import config, models, schemas
from ..db import get_db
from ..ingestion import validate

router = APIRouter(prefix="/api", tags=["ingestion-admin"])


# --- Proposed changes (review queue, §4.4) ---------------------------------
@router.get("/proposed-changes")
def list_proposed_changes(status: str = "pending", db: Session = Depends(get_db)):
    stmt = select(models.ProposedChange)
    if status != "all":
        stmt = stmt.where(models.ProposedChange.status == status)
    rows = db.scalars(stmt.order_by(models.ProposedChange.created_at.desc())).all()
    out = []
    for c in rows:
        product = db.get(models.CardProduct, c.target_id)
        out.append(
            {
                "id": c.id,
                "target_table": c.target_table,
                "target_id": c.target_id,
                "product": f"{product.issuer} {product.product_name}" if product else None,
                "field": c.field,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "source_url": c.source_url,
                "confidence": c.confidence,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
        )
    return out


@router.post("/proposed-changes/{change_id}/decision")
def decide_change(
    change_id: int,
    payload: schemas.ProposedDecision,
    db: Session = Depends(get_db),
):
    change = db.get(models.ProposedChange, change_id)
    if not change:
        raise HTTPException(status_code=404, detail="Proposed change not found")
    if payload.status == "approved":
        validate.approve_change(db, change)
    else:
        validate.reject_change(db, change)
    return {"id": change.id, "status": change.status}


# --- Discovered-card review (§4.2) -----------------------------------------
@router.get("/discovered")
def list_discovered(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.CardProduct).where(
            models.CardProduct.added_by.in_(("llm_discovery", "static_discovery")),
            models.CardProduct.discovery_reviewed.is_(False),
        )
    ).all()
    return [
        {
            "id": p.id,
            "issuer": p.issuer,
            "product_name": p.product_name,
            "ownership": p.ownership,
            "currency": p.currency,
            "tag": p.tag,
        }
        for p in rows
    ]


@router.post("/discovered/{product_id}/review")
def mark_reviewed(product_id: int, db: Session = Depends(get_db)):
    product = db.get(models.CardProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.discovery_reviewed = True
    db.commit()
    return {"id": product.id, "discovery_reviewed": True}


# --- Source config (§9) -----------------------------------------------------
def _source_name(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.netloc or url


def _seed_sources(db: Session) -> None:
    existing = db.scalars(select(models.SourceConfig)).first()
    if existing:
        return
    for index, url in enumerate(config.DEFAULT_SOURCES, start=1):
        db.add(
            models.SourceConfig(
                name=_source_name(url),
                url=url,
                kind="offer",
                active=True,
                priority=min(index, 5),
            )
        )
    db.commit()


def _source_to_dict(source: models.SourceConfig) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "product_id": source.product_id,
        "kind": source.kind,
        "active": source.active,
        "priority": source.priority,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)):
    _seed_sources(db)
    rows = db.scalars(
        select(models.SourceConfig).order_by(models.SourceConfig.active.desc(), models.SourceConfig.priority)
    ).all()
    return {
        "sources": [s.url for s in rows if s.active],
        "records": [_source_to_dict(s) for s in rows],
        "discovery_issuers": config.DISCOVERY_ISSUERS,
    }


@router.post("/sources")
def create_source(payload: schemas.SourceCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(models.SourceConfig).where(models.SourceConfig.url == payload.url))
    if existing:
        raise HTTPException(status_code=409, detail="Source URL already exists")
    source = models.SourceConfig(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_to_dict(source)


@router.put("/sources/{source_id}")
def update_source(source_id: int, payload: schemas.SourceUpdate, db: Session = Depends(get_db)):
    source = db.get(models.SourceConfig, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return _source_to_dict(source)


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    source = db.get(models.SourceConfig, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    source.active = False
    db.commit()
    return {"ok": True}
