"""Catalog data-quality health checks.

This is a read-only decision/admin layer. It explains why a product is healthy,
stale, needs review, or still needs data without mutating sourced public facts.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, source_quality
from ..product_identity import product_display_name, product_variant_key
from .catalog import effective_catalog

if TYPE_CHECKING:
    from .decision_context import DecisionContext

STALE_SOURCE_DAYS = 30
UNSAFE_SOURCE_ISSUES = {
    "source_identity_conflict",
    "broad_source_not_product_truth",
    "source_not_product_specific",
}


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _has_current_offer(product: models.CardProduct) -> bool:
    return bool(
        product.current_offer_effective
        or product.current_offer_cash
    )


def _has_public_peak(product: models.CardProduct) -> bool:
    return bool(product.peak_offer_points)


def _has_supplemental(product: models.CardProduct) -> bool:
    return bool(product.card_benefits and (product.earn_multipliers or product.best_category_uses))


def _held_users_for_product(
    product: models.CardProduct,
    context: "DecisionContext | None",
    db: Session,
) -> list[str]:
    if context:
        active_cards = [card for cards in context.active_held_by_user.values() for card in cards]
    else:
        active_cards = list(db.scalars(select(models.HeldCard).where(models.HeldCard.status != "Closed")).all())
    product_variant = product_variant_key(product.issuer, product.product_name)
    users: set[str] = set()
    for card in active_cards:
        if card.product_id and product.id and card.product_id == product.id:
            users.add(card.user)
            continue
        if (
            (card.issuer or "").strip().lower() == (product.issuer or "").strip().lower()
            and (card.product_name or "").strip().lower() == (product.product_name or "").strip().lower()
        ):
            users.add(card.user)
            continue
        card_variant = product_variant_key(card.issuer, card.product_name)
        if product_variant and card_variant and product_variant == card_variant:
            users.add(card.user)
    return sorted(users)


def _pending_fields(db: Session) -> dict[int, set[str]]:
    rows = db.execute(
        select(models.ProposedChange.target_id, models.ProposedChange.field).where(
            models.ProposedChange.target_table == "card_product",
            models.ProposedChange.status == "pending",
        )
    ).all()
    out: dict[int, set[str]] = {}
    for target_id, field in rows:
        out.setdefault(target_id, set()).add(field)
    return out


def _duplicate_variant_counts(db: Session) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for product in db.scalars(select(models.CardProduct)).all():
        variant = product_variant_key(product.issuer, product.product_name)
        if variant:
            counts[variant] += 1
    return counts


def _status(issue_codes: set[str]) -> str:
    if "pending_review" in issue_codes:
        return "needs_review"
    if issue_codes & UNSAFE_SOURCE_ISSUES:
        return "needs_data"
    if any(code.startswith("missing_") or code == "duplicate_identity" for code in issue_codes):
        return "needs_data"
    if "source_stale" in issue_codes or "never_verified" in issue_codes:
        return "stale"
    return "healthy"


def _next_action(status: str, issue_codes: set[str], held_users: list[str]) -> str:
    if "pending_review" in issue_codes:
        return "Review proposed change."
    if "duplicate_identity" in issue_codes:
        return "Merge or blacklist duplicate catalog identity."
    if "missing_benefits" in issue_codes or "missing_multipliers" in issue_codes:
        return "Run deep refresh for held-card benefits." if held_users else "Fill opportunistically after offer data."
    if "missing_current_offer" in issue_codes or "missing_public_peak" in issue_codes:
        return "Run targeted deep refresh with cited sources."
    if "missing_cpp" in issue_codes:
        return "Add or refresh point valuation."
    if "missing_source" in issue_codes:
        return "Attach a verified product/source URL."
    if "source_identity_conflict" in issue_codes:
        return "Replace the conflicting source with the exact issuer product page."
    if "broad_source_not_product_truth" in issue_codes or "source_not_product_specific" in issue_codes:
        return "Attach a product-specific issuer/source URL before ranking."
    if status == "stale":
        return "Refresh cached source."
    return "No action."


def build_catalog_health(db: Session, context: "DecisionContext | None" = None) -> dict:
    products = list(context.products if context else effective_catalog(db))
    valuations = context.valuations if context else {
        row.currency.strip().lower(): row.cpp_effective
        for row in db.scalars(select(models.Valuation)).all()
        if row.currency and row.cpp_effective is not None
    }
    pending_by_id = _pending_fields(db)
    duplicate_counts = _duplicate_variant_counts(db)
    now = _utcnow()
    rows: list[dict] = []
    summary = {
        "total": len(products),
        "healthy": 0,
        "stale": 0,
        "needs_data": 0,
        "needs_review": 0,
        "held_needs_data": 0,
    }

    for product in products:
        held_users = _held_users_for_product(product, context, db)
        issues: list[dict] = []

        def add(code: str, label: str, severity: str = "medium") -> None:
            issues.append({"code": code, "label": label, "severity": severity})

        if product.id in pending_by_id:
            fields = ", ".join(sorted(pending_by_id[product.id]))
            add("pending_review", f"Pending review: {fields}", "high")
        if not _has_current_offer(product):
            add("missing_current_offer", "Missing current public offer", "high")
        if not _has_public_peak(product):
            add("missing_public_peak", "Missing public peak offer", "medium")
        if not product.currency:
            add("missing_currency", "Missing rewards currency", "high")
        elif product.currency.strip().lower() not in valuations and product.currency.strip().lower() not in {"cash", "cash back", "cashback", "usd"}:
            add("missing_cpp", f"Missing valuation for {product.currency}", "medium")
        if not product.card_benefits:
            add("missing_benefits", "Missing verified benefits", "medium" if held_users else "low")
        if not (product.earn_multipliers or product.best_category_uses):
            add("missing_multipliers", "Missing earn/use categories", "medium" if held_users else "low")
        if not product.source_url:
            add("missing_source", "Missing source URL", "medium")
        else:
            source_issue = source_quality.source_quality_issue(product.issuer, product.product_name, product.source_url)
            if source_issue:
                add(source_issue, source_issue.replace("_", " ").title(), "high")
        if not product.last_verified:
            add("never_verified", "Never verified", "medium")
        elif (now - product.last_verified).days > STALE_SOURCE_DAYS:
            add("source_stale", f"Source older than {STALE_SOURCE_DAYS} days", "low")
        variant = product_variant_key(product.issuer, product.product_name)
        if variant and duplicate_counts[variant] > 1:
            add("duplicate_identity", "Duplicate product identity exists", "medium")

        issue_codes = {issue["code"] for issue in issues}
        status = _status(issue_codes)
        summary[status] += 1
        if held_users and status in {"needs_data", "needs_review"}:
            summary["held_needs_data"] += 1
        severity_cost = {"high": 18, "medium": 10, "low": 5}
        score = max(0, 100 - sum(severity_cost.get(issue["severity"], 8) for issue in issues))
        rows.append(
            {
                "product_id": product.id,
                "issuer": product.issuer,
                "product_name": product.product_name,
                "display_name": product_display_name(product.issuer, product.product_name),
                "currency": product.currency,
                "held_by": held_users,
                "status": status,
                "health_score": score,
                "issues": issues,
                "next_action": _next_action(status, issue_codes, held_users),
                "source_url": product.source_url,
                "last_verified": product.last_verified.isoformat() if product.last_verified else None,
                "has_offer": _has_current_offer(product),
                "has_public_peak": _has_public_peak(product),
                "has_supplemental": _has_supplemental(product),
            }
        )

    status_order = {"needs_review": 0, "needs_data": 1, "stale": 2, "healthy": 3}
    rows.sort(
        key=lambda row: (
            0 if row["held_by"] else 1,
            status_order.get(row["status"], 9),
            row["health_score"],
            row["issuer"] or "",
            row["product_name"] or "",
        )
    )
    return {"summary": summary, "products": rows}
