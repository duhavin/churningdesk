"""Card Universe / Review admin: proposed-change queue, discovered-card review,
source config. PUBLIC tables only (§10 tab 5)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from urllib.parse import urlsplit

from .. import config, models, schemas
from ..benefit_normalization import is_benefit_noise
from ..db import get_db
from ..ingestion import validate
from ..product_identity import canonical_product_key, product_display_name

router = APIRouter(prefix="/api", tags=["ingestion-admin"])


FIELD_LABELS = {
    "annual_fee": "Annual fee",
    "best_category_uses": "Best category uses",
    "card_benefits": "Benefits",
    "currency": "Currency",
    "current_offer_cash": "Welcome cash",
    "current_offer_min_spend": "Minimum spend",
    "current_offer_points": "Welcome bonus",
    "current_offer_window_months": "Spend window",
    "earn_multipliers": "Earn rates",
    "eligibility_tags": "Eligibility rules",
    "peak_offer_points": "Public peak",
    "referral_bonus_cash": "Referral cash",
    "referral_bonus_points": "Referral bonus",
}

MONEY_FIELDS = {
    "annual_fee",
    "current_offer_cash",
    "current_offer_min_spend",
    "first_year_credit_value",
    "peak_offer_min_spend",
    "referral_bonus_cash",
}

POINT_FIELDS = {
    "current_offer_points",
    "peak_offer_points",
    "targeted_peak_offer_points",
    "referral_bonus_points",
}


def _decode_review_value(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _clean_review_text(value) -> str:
    text = str(value or "")
    text = re.sub(r"\[(?:text|title|meta|json-ld|table)\]\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    return text


def _is_raw_source_fragment(value) -> bool:
    if is_benefit_noise(value):
        return True
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("benefit") or value.get("title") or "").strip()
        if name and len(name) <= 96:
            return False
    low = str(value or "").lower()
    text = str(value or "")
    if "[text]" in low:
        return True
    if len(text) > 220:
        return True
    return any(
        token in low
        for token in (
            "[json-ld]",
            "@context",
            "newsarticle",
            "articlesection",
            "schema.org",
            "[title]",
            "[meta]",
            "best current credit card",
            "best credit cards for students",
            "breadcrumblist",
            "aggregaterating",
            "credit card members may have the option",
            "pay over time",
            "payment plan",
            "doesn't include",
            "does not include",
        )
    )


def _short_text(value, limit: int = 72) -> str:
    text = _clean_review_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _benefit_preview(item) -> str:
    if not isinstance(item, dict):
        return _short_text(item)
    pieces = [
        item.get("name") or item.get("benefit") or item.get("title"),
        item.get("value") or item.get("amount") or item.get("annual_value"),
        item.get("frequency") if item.get("frequency") not in (None, "unknown") else None,
    ]
    text = " ".join(str(part) for part in pieces if part)
    return _short_text(text or item, 86)


def _format_review_value(field: str, value) -> str:
    if value in (None, "", [], {}):
        return "blank"
    if isinstance(value, list):
        clean_items = []
        for item in value:
            preview = _benefit_preview(item) if field == "card_benefits" else _short_text(item)
            if preview and not _is_raw_source_fragment(item):
                clean_items.append(preview)
        prefix = f"{len(value)} item{'s' if len(value) != 1 else ''}"
        if not clean_items:
            return f"{prefix}: raw source fragments"
        visible = "; ".join(clean_items[:3])
        suffix = f"; +{len(clean_items) - 3} more" if len(clean_items) > 3 else ""
        return f"{prefix}: {visible}{suffix}"
    if isinstance(value, dict):
        parts = []
        for key, item in list(value.items())[:5]:
            if field == "earn_multipliers":
                try:
                    parts.append(f"{key} {float(item):g}x")
                    continue
                except (TypeError, ValueError):
                    pass
            parts.append(f"{key}: {_short_text(item, 36)}")
        suffix = f"; +{len(value) - 5} more" if len(value) > 5 else ""
        return "; ".join(parts) + suffix
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if field in MONEY_FIELDS:
            return f"${value:,.0f}"
        if field in POINT_FIELDS:
            return f"{value:,.0f} points"
        if field == "current_offer_window_months":
            return f"{value:g} months"
        return f"{value:g}"
    return _short_text(value)


def _review_note(field: str, old_value, new_value) -> str | None:
    if field == "card_benefits":
        new_items = new_value if isinstance(new_value, list) else []
        raw_count = len([item for item in new_items if _is_raw_source_fragment(item)])
        if raw_count:
            return f"Contains {raw_count} raw source fragment{'s' if raw_count != 1 else ''}; reject unless the cleaned benefits are clearly correct."
    if field == "currency" and isinstance(new_value, str) and new_value.lower() in {"points", "miles"}:
        return "Generic currency detected; co-brand cards should keep the actual rewards program."
    if field in {"earn_multipliers", "best_category_uses"} and isinstance(old_value, dict) and isinstance(new_value, dict):
        if len(new_value) < len(old_value):
            return "New value removes categories; approve only if the source clearly replaced the old earn/use map."
    return None


def _source_domain(source_url: str | None) -> str | None:
    if not source_url:
        return None
    return urlsplit(source_url).netloc or source_url


def _proposed_change_to_dict(c: models.ProposedChange, product: models.CardProduct | None) -> dict:
    old_value = _decode_review_value(c.old_value)
    new_value = _decode_review_value(c.new_value)
    field_label = FIELD_LABELS.get(c.field, c.field.replace("_", " ").title())
    old_preview = _format_review_value(c.field, old_value)
    new_preview = _format_review_value(c.field, new_value)
    return {
        "id": c.id,
        "target_table": c.target_table,
        "target_id": c.target_id,
        "product": product_display_name(product.issuer, product.product_name) if product else None,
        "product_name": product.product_name if product else None,
        "display_name": product_display_name(product.issuer, product.product_name) if product else None,
        "canonical_key": canonical_product_key(product.issuer, product.product_name) if product else None,
        "field": c.field,
        "field_label": field_label,
        "old_value": c.old_value,
        "new_value": c.new_value,
        "old_preview": old_preview,
        "new_preview": new_preview,
        "change_summary": f"{field_label}: {old_preview} -> {new_preview}",
        "review_note": c.review_note or _review_note(c.field, old_value, new_value),
        "reason_code": c.reason_code,
        "risk_level": c.risk_level,
        "quality_score": c.quality_score,
        "source_url": c.source_url,
        "source_domain": _source_domain(c.source_url),
        "confidence": c.confidence,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


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
        out.append(_proposed_change_to_dict(c, product))
    return out


@router.post("/proposed-changes/cleanup")
def cleanup_proposed_changes(db: Session = Depends(get_db)):
    return validate.cleanup_bad_pending_changes(db)


@router.post("/proposed-changes/auto-resolve")
def auto_resolve_changes(db: Session = Depends(get_db)):
    """System self-review: evidence + plausibility gated, peaks need trusted sources."""
    return validate.auto_resolve_pending_changes(db)


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
            "display_name": product_display_name(p.issuer, p.product_name),
            "canonical_key": canonical_product_key(p.issuer, p.product_name),
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
