"""Safe catalog cleanup utilities for duplicate public product identities."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import models
from ..product_identity import product_display_name, product_variant_key
from .catalog import _product_completeness_score


SCALAR_COPY_FIELDS = (
    "product_family",
    "ownership",
    "account_type",
    "currency",
    "annual_fee",
    "current_offer_points",
    "current_offer_cash",
    "current_offer_min_spend",
    "current_offer_window_months",
    "peak_offer_min_spend",
    "peak_offer_source",
    "peak_offer_date",
    "targeted_peak_offer_points",
    "targeted_peak_offer_cash",
    "targeted_peak_offer_source",
    "targeted_peak_offer_date",
    "referral_bonus_points",
    "referral_bonus_cash",
    "first_year_credit_value",
    "reports_to_personal_credit",
    "tag",
    "added_by",
    "discovery_reviewed",
    "source_url",
    "last_verified",
    "last_web_search_at",
    "notes",
)
LIST_MERGE_FIELDS = ("card_benefits", "downgrade_paths", "eligibility_tags")
DICT_MERGE_FIELDS = ("earn_multipliers", "best_category_uses")


def _empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _norm_key(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _merge_list(left: Any, right: Any) -> list | None:
    if not isinstance(left, list) and not isinstance(right, list):
        return left if isinstance(left, list) else right if isinstance(right, list) else None
    merged: list = []
    seen: set[str] = set()
    for item in [*(left or []), *(right or [])]:
        key = _norm_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged or None


def _merge_dict(left: Any, right: Any) -> dict | None:
    if not isinstance(left, dict) and not isinstance(right, dict):
        return left if isinstance(left, dict) else right if isinstance(right, dict) else None
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if key not in merged or _empty(merged.get(key)):
            merged[key] = value
    return merged or None


def _copy_scalar(canonical: models.CardProduct, duplicate: models.CardProduct, field: str) -> bool:
    current = getattr(canonical, field, None)
    incoming = getattr(duplicate, field, None)
    if _empty(incoming):
        return False
    if field == "peak_offer_points":
        return False
    if field == "last_verified":
        if current is None or (incoming is not None and incoming > current):
            setattr(canonical, field, incoming)
            return True
        return False
    if _empty(current):
        setattr(canonical, field, incoming)
        return True
    return False


def _merge_product_fields(canonical: models.CardProduct, duplicate: models.CardProduct) -> list[str]:
    changed: list[str] = []
    duplicate_peak = duplicate.peak_offer_points or 0
    canonical_peak = canonical.peak_offer_points or 0
    if duplicate_peak > canonical_peak:
        canonical.peak_offer_points = duplicate.peak_offer_points
        if duplicate.peak_offer_min_spend:
            canonical.peak_offer_min_spend = duplicate.peak_offer_min_spend
        if duplicate.peak_offer_source:
            canonical.peak_offer_source = duplicate.peak_offer_source
        if duplicate.peak_offer_date:
            canonical.peak_offer_date = duplicate.peak_offer_date
        changed.append("peak_offer_points")
    for field in SCALAR_COPY_FIELDS:
        if _copy_scalar(canonical, duplicate, field):
            changed.append(field)
    for field in LIST_MERGE_FIELDS:
        merged = _merge_list(getattr(canonical, field, None), getattr(duplicate, field, None))
        if merged != getattr(canonical, field, None):
            setattr(canonical, field, merged)
            changed.append(field)
    for field in DICT_MERGE_FIELDS:
        merged = _merge_dict(getattr(canonical, field, None), getattr(duplicate, field, None))
        if merged != getattr(canonical, field, None):
            setattr(canonical, field, merged)
            changed.append(field)
    return changed


def _variant_groups(db: Session) -> dict[tuple[str, str], list[models.CardProduct]]:
    groups: dict[tuple[str, str], list[models.CardProduct]] = defaultdict(list)
    for product in db.scalars(select(models.CardProduct)).all():
        variant = product_variant_key(product.issuer, product.product_name)
        if variant:
            groups[variant].append(product)
    return {variant: rows for variant, rows in groups.items() if len(rows) > 1}


def duplicate_product_groups(db: Session) -> list[dict]:
    groups = []
    for variant, products in _variant_groups(db).items():
        chosen = max(products, key=_product_completeness_score)
        groups.append(
            {
                "variant": f"{variant[0]}:{variant[1]}",
                "canonical_product_id": chosen.id,
                "canonical_display_name": product_display_name(chosen.issuer, chosen.product_name),
                "duplicates": [
                    {
                        "product_id": product.id,
                        "issuer": product.issuer,
                        "product_name": product.product_name,
                        "display_name": product_display_name(product.issuer, product.product_name),
                    }
                    for product in sorted(products, key=lambda item: item.id or 0)
                ],
            }
        )
    return sorted(groups, key=lambda item: item["variant"])


def merge_duplicate_products(db: Session, *, commit: bool = True) -> dict:
    """Merge duplicate same-variant CardProduct rows into the best canonical row."""
    merged_groups: list[dict] = []
    for variant, products in _variant_groups(db).items():
        canonical = max(products, key=_product_completeness_score)
        duplicates = [product for product in products if product.id != canonical.id]
        changed_fields: set[str] = set()
        duplicate_ids: list[int] = []
        for duplicate in duplicates:
            duplicate_ids.append(duplicate.id)
            changed_fields.update(_merge_product_fields(canonical, duplicate))
            if duplicate.id and canonical.id:
                db.execute(
                    update(models.HeldCard)
                    .where(models.HeldCard.product_id == duplicate.id)
                    .values(product_id=canonical.id)
                )
                db.execute(
                    update(models.ManualTargetedOffer)
                    .where(models.ManualTargetedOffer.product_id == duplicate.id)
                    .values(product_id=canonical.id)
                )
                db.execute(
                    update(models.SourceConfig)
                    .where(models.SourceConfig.product_id == duplicate.id)
                    .values(product_id=canonical.id)
                )
                db.execute(
                    update(models.IngestionEvidence)
                    .where(models.IngestionEvidence.product_id == duplicate.id)
                    .values(product_id=canonical.id)
                )
                db.execute(
                    update(models.ProposedChange)
                    .where(
                        models.ProposedChange.target_table == "card_product",
                        models.ProposedChange.target_id == duplicate.id,
                    )
                    .values(target_id=canonical.id)
                )
            db.delete(duplicate)
        merged_groups.append(
            {
                "variant": f"{variant[0]}:{variant[1]}",
                "canonical_product_id": canonical.id,
                "canonical_display_name": product_display_name(canonical.issuer, canonical.product_name),
                "deleted_duplicate_ids": duplicate_ids,
                "merged_fields": sorted(changed_fields),
            }
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "groups_merged": len(merged_groups),
        "duplicates_deleted": sum(len(group["deleted_duplicate_ids"]) for group in merged_groups),
        "groups": merged_groups,
    }
