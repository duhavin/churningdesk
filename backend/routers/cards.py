"""Held cards (PRIVATE) + the Dashboard 'needs attention' feed."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..crypto import MissingKeyError
from ..db import get_db
from ..logic import benefits as benefits_logic
from ..logic.catalog import effective_catalog
from ..logic import eligibility as elig
from ..product_identity import canonical_product_key, product_display_name, product_variant_key

router = APIRouter(prefix="/api", tags=["held-cards"])

ATTENTION_WINDOW_DAYS = 45


def _safe(fn):
    try:
        return fn()
    except MissingKeyError:
        return None


def serialize_held(h: models.HeldCard) -> dict:
    again_ok, again_date = elig.bonus_eligible_again(h)
    next_review = elig.add_months(h.renewal_date, -1) if h.renewal_date else None
    return {
        "id": h.id,
        "user": h.user,
        "product_id": h.product_id,
        "issuer": h.issuer,
        "product_name": h.product_name,
        "display_name": product_display_name(h.issuer, h.product_name),
        "canonical_key": canonical_product_key(h.issuer, h.product_name),
        "last4": _safe(lambda: h.last4),
        "date_opened": h.date_opened.isoformat() if h.date_opened else None,
        "ownership": h.ownership,
        "account_type": h.account_type,
        "reports_to_personal_credit": h.reports_to_personal_credit,
        "credit_limit": _safe(lambda: h.credit_limit),
        "annual_fee": h.annual_fee,
        "renewal_date": h.renewal_date.isoformat() if h.renewal_date else None,
        "next_review_date": next_review.isoformat() if next_review else None,
        "welcome_bonus_earned": h.welcome_bonus_earned,
        "bonus_points_earned": h.bonus_points_earned,
        "bonus_currency": h.bonus_currency,
        "bonus_earned_date": h.bonus_earned_date.isoformat() if h.bonus_earned_date else None,
        "min_spend_requirement": h.min_spend_requirement,
        "min_spend_deadline": h.min_spend_deadline.isoformat() if h.min_spend_deadline else None,
        "min_spend_progress": h.min_spend_progress,
        "min_spend_completed": h.min_spend_completed,
        "bonus_eligible_again": again_ok,
        "eligible_again_date": again_date.isoformat() if again_date else None,
        "my_targeted_offer_points": _safe(lambda: h.my_targeted_offer_points),
        "my_targeted_offer_notes": h.my_targeted_offer_notes,
        "status": h.status,
        "notes": h.notes,
    }


def _key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


def _match_product(db: Session, issuer: str | None, product_name: str | None) -> models.CardProduct | None:
    exact = _key(issuer, product_name)
    variant = product_variant_key(issuer, product_name)
    for product in effective_catalog(db):
        if _key(product.issuer, product.product_name) == exact:
            return product
        if variant and product_variant_key(product.issuer, product.product_name) == variant:
            return product
    return None


@router.get("/cards")
def list_cards(user: str | None = None, db: Session = Depends(get_db)):
    stmt = select(models.HeldCard)
    if user:
        stmt = stmt.where(models.HeldCard.user == user)
    cards = db.scalars(stmt.order_by(models.HeldCard.date_opened.desc())).all()
    return [serialize_held(c) for c in cards]


@router.post("/cards")
def create_card(payload: schemas.HeldCardCreate, db: Session = Depends(get_db)):
    try:
        data = payload.model_dump()
        product = db.get(models.CardProduct, data["product_id"]) if data.get("product_id") else None
        if not product:
            product = _match_product(db, data.get("issuer"), data.get("product_name"))
            if product:
                data["product_id"] = product.id
        if product:
            if data.get("min_spend_requirement") is None:
                data["min_spend_requirement"] = product.current_offer_min_spend
            if (
                data.get("date_opened")
                and data.get("min_spend_deadline") is None
                and product.current_offer_window_months
                and data.get("min_spend_requirement")
            ):
                data["min_spend_deadline"] = elig.add_months(
                    data["date_opened"], product.current_offer_window_months
                )
        card = models.HeldCard(**data)
        db.add(card)
        db.commit()
        db.refresh(card)
        return serialize_held(card)
    except MissingKeyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/cards/{card_id}")
def update_card(
    card_id: int, payload: schemas.HeldCardUpdate, db: Session = Depends(get_db)
):
    card = db.get(models.HeldCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Held card not found")
    try:
        data = payload.model_dump(exclude_unset=True)
        product_id = data.get("product_id", card.product_id)
        product = db.get(models.CardProduct, product_id) if product_id else None
        if not product:
            product = _match_product(
                db,
                data.get("issuer", card.issuer),
                data.get("product_name", card.product_name),
            )
            if product:
                data["product_id"] = product.id
        if product:
            if data.get("min_spend_requirement") is None and card.min_spend_requirement is None:
                data["min_spend_requirement"] = product.current_offer_min_spend
            opened = data.get("date_opened", card.date_opened)
            if (
                opened
                and data.get("min_spend_deadline") is None
                and card.min_spend_deadline is None
                and product.current_offer_window_months
                and (data.get("min_spend_requirement") or card.min_spend_requirement)
            ):
                data["min_spend_deadline"] = elig.add_months(opened, product.current_offer_window_months)
        for field, value in data.items():
            setattr(card, field, value)
        db.commit()
        db.refresh(card)
        return serialize_held(card)
    except MissingKeyError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(models.HeldCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Held card not found")
    db.delete(card)
    db.commit()
    return {"ok": True}


@router.get("/dashboard")
def dashboard(user: str, db: Session = Depends(get_db)):
    today = dt.date.today()
    soon = today + dt.timedelta(days=ATTENTION_WINDOW_DAYS)
    held = db.scalars(
        select(models.HeldCard).where(models.HeldCard.user == user)
    ).all()

    attention: list[dict] = []

    for h in held:
        if h.status == "Closed":
            continue
        # Retention calls due (renewal within window)
        if h.renewal_date and today <= h.renewal_date <= soon:
            action = "Call retention"
            detail = f"Renewal {h.renewal_date.isoformat()}; review fee before it posts."
            attention.append(
                {
                    "type": "retention_call",
                    "severity": "high",
                    "card": product_display_name(h.issuer, h.product_name),
                    "due_date": h.renewal_date.isoformat(),
                    "action": action,
                    "detail": detail,
                    "message": f"Renewal {h.renewal_date.isoformat()} — call retention before the annual fee posts.",
                }
            )
        # Approaching min-spend deadline (bonus not yet earned)
        if not h.welcome_bonus_earned:
            deadline = h.min_spend_deadline
            product = None
            requirement = h.min_spend_requirement
            if h.product_id and (requirement is None or deadline is None):
                product = db.get(models.CardProduct, h.product_id)
            if requirement is None and product:
                requirement = product.current_offer_min_spend
            progress = h.min_spend_progress or 0
            if deadline is None and product and product.current_offer_window_months:
                deadline = elig.add_months(h.date_opened, product.current_offer_window_months)
            if deadline and requirement and not h.min_spend_completed and today <= deadline <= soon:
                remaining = max(requirement - progress, 0)
                action = "Finish min spend"
                detail = f"${remaining:,.0f} left by {deadline.isoformat()}."
                attention.append(
                    {
                        "type": "min_spend_deadline",
                        "severity": "high",
                        "card": product_display_name(h.issuer, h.product_name),
                        "due_date": deadline.isoformat(),
                        "action": action,
                        "detail": detail,
                        "message": f"Min-spend window closes ~{deadline.isoformat()} - ${remaining:.0f} remaining.",
                    }
                )
        # Newly bonus-eligible again
        again_ok, again_date = elig.bonus_eligible_again(h)
        if h.welcome_bonus_earned and again_ok:
            action = "Recheck welcome bonus"
            detail = "Bonus clock has reset; use pipeline before reapplying."
            attention.append(
                {
                    "type": "bonus_eligible_again",
                    "severity": "info",
                    "card": product_display_name(h.issuer, h.product_name),
                    "due_date": again_date.isoformat() if again_date else None,
                    "action": action,
                    "detail": detail,
                    "message": "Welcome bonus is eligible again — consider re-applying.",
                }
            )

    attention.extend(benefits_logic.build_benefit_attention(db, user, ATTENTION_WINDOW_DAYS))

    pending_changes = db.scalars(
        select(models.ProposedChange).where(models.ProposedChange.status == "pending")
    ).all()
    if pending_changes:
        action = "Review data changes"
        detail = f"{len(pending_changes)} proposed change(s) waiting in Card Universe."
        attention.append(
            {
                "type": "pending_changes",
                "severity": "info",
                "card": "Card Universe",
                "action": action,
                "detail": detail,
                "message": f"{len(pending_changes)} proposed change(s) awaiting review.",
            }
        )

    unreviewed = db.scalars(
        select(models.CardProduct).where(
            models.CardProduct.added_by == "llm_discovery",
            models.CardProduct.discovery_reviewed.is_(False),
        )
    ).all()
    if unreviewed:
        action = "Review discovered cards"
        detail = f"{len(unreviewed)} new card(s) need catalog review."
        attention.append(
            {
                "type": "unreviewed_discovered",
                "severity": "info",
                "card": "Card Universe",
                "action": action,
                "detail": detail,
                "message": f"{len(unreviewed)} newly discovered card(s) to review.",
            }
        )

    f24 = elig.five24(db, user)
    return {
        "user": user,
        "held_cards": [serialize_held(h) for h in held],
        "five_24": {
            "count": f24.count,
            "under_524": f24.count < 5,
            "earliest_drop_date": f24.earliest_drop_date.isoformat()
            if f24.earliest_drop_date
            else None,
        },
        "needs_attention": attention,
    }
