"""Safe catalog cleanup utilities for duplicate public product identities."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import card_references, models, source_quality
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
UNSAFE_SOURCE_ISSUES = {
    "source_identity_conflict",
    "broad_source_not_product_truth",
    "source_not_product_specific",
}


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


def _safe_reference_url(db: Session, product: models.CardProduct) -> str | None:
    for url in card_references.reference_source_urls(db, product):
        if source_quality.is_safe_product_source(product.issuer, product.product_name, url):
            return source_quality.normalize_url(url)
    return None


def _deactivate_unsafe_product_sources(db: Session, product: models.CardProduct) -> int:
    if not product.id:
        return 0
    count = 0
    rows = db.scalars(
        select(models.SourceConfig).where(
            models.SourceConfig.product_id == product.id,
            models.SourceConfig.active.is_(True),
        )
    ).all()
    for row in rows:
        if source_quality.source_quality_issue(product.issuer, product.product_name, row.url) in UNSAFE_SOURCE_ISSUES:
            row.active = False
            count += 1
    return count


def _prune_unsafe_learned_reference_urls(db: Session, product: models.CardProduct) -> int:
    ref = card_references.get_reference(db, product.issuer, product.product_name)
    if ref is None or not ref.learned_source_urls:
        return 0
    safe_urls = [
        url
        for url in ref.learned_source_urls
        if source_quality.is_safe_product_source(product.issuer, product.product_name, url)
    ]
    removed = len(ref.learned_source_urls) - len(safe_urls)
    if removed:
        ref.learned_source_urls = safe_urls or None
    return removed


def repair_unsafe_product_sources(db: Session, *, commit: bool = True) -> dict:
    """Quarantine product sources that cannot support decision ranking.

    Unsafe sources should not strand a card forever. When the reference registry
    has an exact safe issuer/product URL, promote it as the next source to
    refresh. The product is marked unverified so old facts cannot rank until a
    refresh re-verifies them from the safe URL.
    """
    rows: list[dict] = []
    product_sources_deactivated = 0
    learned_reference_urls_removed = 0

    products = db.scalars(select(models.CardProduct)).all()
    for product in products:
        issue = source_quality.source_quality_issue(product.issuer, product.product_name, product.source_url)
        learned_reference_urls_removed += _prune_unsafe_learned_reference_urls(db, product)
        product_sources_deactivated += _deactivate_unsafe_product_sources(db, product)
        if issue not in UNSAFE_SOURCE_ISSUES:
            continue

        replacement = _safe_reference_url(db, product)
        old_source = product.source_url
        if replacement:
            product.source_url = replacement
            action = "replaced_with_reference_source"
        else:
            product.source_url = None
            action = "cleared_unsafe_source"
        product.last_verified = None
        product.last_web_search_at = None
        product.last_supplemental_search_at = None
        rows.append(
            {
                "product_id": product.id,
                "display_name": product_display_name(product.issuer, product.product_name),
                "issue": issue,
                "action": action,
                "had_source": bool(old_source),
                "replacement_source": replacement,
            }
        )

    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "repaired_count": len(rows),
        "replaced_count": len([row for row in rows if row["action"] == "replaced_with_reference_source"]),
        "cleared_count": len([row for row in rows if row["action"] == "cleared_unsafe_source"]),
        "product_sources_deactivated": product_sources_deactivated,
        "learned_reference_urls_removed": learned_reference_urls_removed,
        "rows": rows,
    }


# --------------------------------------------------------------- text sweep

_TEXT_SWEEP_FIELDS = ("product_name", "issuer", "notes", "product_family", "currency")


def sanitize_catalog_text(db: Session, *, commit: bool = True) -> dict:
    """One-shot PUBLIC catalog text sweep (2026-07-06 audit).

    Repairs mojibake/trademark junk in identity+note fields, strips a
    duplicated leading issuer from product names, re-runs the hardened
    benefit gate over stored card_benefits, and sanitizes category-use and
    downgrade text. Identity-safe: a product_name change is applied ONLY when
    the variant key is unchanged, so held-card links and reference matching
    keep working. Touches PUBLIC card_product rows only.
    """
    from ..benefit_normalization import normalize_public_benefits
    from ..text_sanitize import clean_text, looks_like_scrape_junk

    products = db.scalars(select(models.CardProduct)).all()
    rows: list[dict[str, Any]] = []
    for product in products:
        changes: list[str] = []

        # Identity + free-text scalar fields.
        for field in _TEXT_SWEEP_FIELDS:
            old = getattr(product, field, None)
            if not isinstance(old, str) or not old:
                continue
            new = clean_text(old)
            if field == "product_name":
                issuer_clean = clean_text(product.issuer)
                while issuer_clean and new.lower().startswith(issuer_clean.lower() + " "):
                    stripped = new[len(issuer_clean) + 1 :].strip()
                    if not stripped:
                        break
                    new = stripped
                if new != old and product_variant_key(product.issuer, new) != product_variant_key(
                    product.issuer, old
                ):
                    new = clean_text(old)  # keep cleaned text, skip the prefix strip
                    if product_variant_key(product.issuer, new) != product_variant_key(
                        product.issuer, old
                    ):
                        continue  # identity would shift — leave untouched
            if new and new != old:
                setattr(product, field, new)
                changes.append(field)

        # Stored benefits → hardened gate (drops scrape junk, cleans values,
        # dedupes concepts, salvages good benefits from junk descriptions).
        if product.card_benefits:
            cleaned_benefits = normalize_public_benefits(
                product.issuer,
                product.product_name,
                product.source_url,
                product.card_benefits,
                allow_reference=False,
            )
            if cleaned_benefits != product.card_benefits:
                product.card_benefits = cleaned_benefits
                changes.append("card_benefits")

        if isinstance(product.best_category_uses, dict):
            cleaned_uses: dict = {}
            for key, value in product.best_category_uses.items():
                cat = clean_text(key).lower().replace(" ", "_")
                note = clean_text(value)
                if not cat or not note or len(note) > 120 or looks_like_scrape_junk(note):
                    continue
                cleaned_uses[cat] = note
            if cleaned_uses != product.best_category_uses:
                product.best_category_uses = cleaned_uses or None
                changes.append("best_category_uses")

        if isinstance(product.downgrade_paths, list):
            cleaned_paths = []
            for path in product.downgrade_paths:
                cleaned = clean_text(path)
                if cleaned and len(cleaned) <= 120 and not looks_like_scrape_junk(cleaned):
                    cleaned_paths.append(cleaned)
            if cleaned_paths != product.downgrade_paths:
                product.downgrade_paths = cleaned_paths or None
                changes.append("downgrade_paths")

        if changes:
            rows.append(
                {
                    "product_id": product.id,
                    "display_name": product_display_name(product.issuer, product.product_name),
                    "changed_fields": changes,
                }
            )

    if commit:
        db.commit()
    else:
        db.flush()
    return {"changed_count": len(rows), "rows": rows}
