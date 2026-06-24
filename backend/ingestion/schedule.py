"""Optimized refresh orchestration.

"Refresh offers" now runs as a structured ingestion pipeline:

1. Select stale catalog products.
2. Dedupe all candidate URLs and fetch them concurrently with a per-domain cap.
3. Cache by URL + validators/content hash.
4. Parse static HTML into compact snippets.
5. Extract deterministic rows when confidence is high.
6. Batch only the unresolved compact snippets through the LLM.
7. Delta-gate public offer changes and record field-level evidence.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from itertools import islice
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import card_references, config, models
from ..logic.catalog import effective_catalog
from . import extract, fetch, rendered_fetch, research_resolver, static_parse, validate

STALE_AFTER_DAYS = 3
MIN_CONFIDENCE = 0.5
DETERMINISTIC_CONFIDENCE = 0.78
LLM_BATCH_SIZE = 8
MAX_SNIPPETS_PER_CARD = 8
# Per-snippet char cap for the compact LLM input (offers live in 1-3 short lines).
SNIPPET_LLM_CHARS = 500
IDENTITY_STOPWORDS = {
    "the",
    "card",
    "credit",
    "charge",
    "visa",
    "mastercard",
    "world",
    "elite",
    "signature",
}


def _is_llm_schema_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "compiled grammar is too large" in text or "simplify your tool schemas" in text


def _utcnow() -> dt.datetime:
    # Naive UTC (utcnow() is deprecated on 3.12+); matches naive DateTime columns.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _is_stale(product: models.CardProduct, stale_after_days: int = STALE_AFTER_DAYS) -> bool:
    if stale_after_days <= 0:
        return True
    if product.last_verified is None:
        return True
    age = _utcnow() - product.last_verified
    return age.days >= stale_after_days


def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _same_url(left: str | None, right: str | None) -> bool:
    return bool(left and right and _normalize_url(left) == _normalize_url(right))


def _is_real_offer_url(url: str | None) -> bool:
    normalized = _normalize_url(url)
    if not normalized:
        return False
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    if "anthropic web search" in normalized.lower():
        return False
    return not validate._is_non_offer_source(normalized)


def _is_real_public_url(url: str | None) -> bool:
    normalized = _normalize_url(url)
    if not normalized:
        return False
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    return "anthropic web search" not in normalized.lower()


def _best_real_source_url(row: extract.OfferScanRow, source_urls: list[str]) -> str | None:
    for candidate in [row.source_url, row.product_url, *source_urls]:
        if _is_real_offer_url(candidate):
            return _normalize_url(candidate)
    return None


def _web_search_cooldown_open(product: models.CardProduct, now: dt.datetime) -> bool:
    last = product.last_web_search_at
    if last is None:
        return True
    return (now - last).days >= config.WEB_SEARCH_COOLDOWN_DAYS


def _supplemental_search_cooldown_open(product: models.CardProduct, now: dt.datetime) -> bool:
    last = product.last_supplemental_search_at
    if last is None:
        return True
    return (now - last).days >= config.SUPPLEMENTAL_SEARCH_COOLDOWN_DAYS


def _promote_source_urls(db: Session, urls: list[str], product_id: int | None = None) -> int:
    candidates = list(dict.fromkeys(_normalize_url(url) for url in urls if _is_real_offer_url(url)))
    if not candidates:
        return 0
    existing = {
        _normalize_url(row.url)
        for row in db.scalars(select(models.SourceConfig)).all()
        if row.url
    }
    added = 0
    for url in candidates:
        if not url or url in existing:
            continue
        host = urlsplit(url).netloc or "Offer source"
        db.add(
            models.SourceConfig(
                name=host,
                url=url,
                product_id=product_id,
                kind="offer",
                active=True,
                priority=2,
            )
        )
        existing.add(url)
        added += 1
    if added:
        db.flush()
    return added


def active_source_urls(db: Session, extra_urls: list[str] | None = None) -> list[str]:
    """Active global refresh/discovery sources, falling back to config defaults."""
    rows = db.scalars(
        select(models.SourceConfig)
        .where(models.SourceConfig.active.is_(True))
        .where(models.SourceConfig.product_id.is_(None))
        .order_by(models.SourceConfig.priority, models.SourceConfig.name)
    ).all()
    urls = [r.url for r in rows if r.url] or list(config.DEFAULT_SOURCES)
    if extra_urls:
        urls.extend(extra_urls)
    return list(dict.fromkeys(u for u in urls if u))


def product_source_urls(
    db: Session, products: list[models.CardProduct]
) -> dict[int, list[str]]:
    ids = [p.id for p in products if p.id]
    if not ids:
        return {}
    rows = db.scalars(
        select(models.SourceConfig)
        .where(models.SourceConfig.active.is_(True))
        .where(models.SourceConfig.product_id.in_(ids))
        .order_by(models.SourceConfig.priority, models.SourceConfig.name)
    ).all()
    out: dict[int, list[str]] = {}
    for row in rows:
        if row.url and row.product_id:
            out.setdefault(row.product_id, []).append(row.url)
    for product in products:
        ref_urls = card_references.reference_source_urls(db, product)
        if ref_urls and product.id:
            out.setdefault(product.id, []).extend(ref_urls)
    return {product_id: list(dict.fromkeys(urls)) for product_id, urls in out.items()}


def _candidate_urls(
    products: list[models.CardProduct],
    source_urls: list[str],
    product_sources: dict[int, list[str]] | None = None,
) -> list[str]:
    if not products:
        return []
    urls: list[str] = []
    for product in products:
        if product.source_url:
            urls.append(product.source_url)
        urls.extend((product_sources or {}).get(product.id, []))
    urls.extend(source_urls)
    return list(dict.fromkeys(urls))


def _product_render_urls(
    product: models.CardProduct,
    product_sources: list[str] | None = None,
) -> list[str]:
    urls: list[str] = []
    urls.extend(product_sources or [])
    if product.source_url:
        urls.append(product.source_url)
    return [
        url
        for url in dict.fromkeys(urls)
        if _is_real_public_url(url) and not validate._is_non_offer_source(url)
    ]


def _chunks(items: list, size: int):
    iterator = iter(items)
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            return
        yield chunk


def _product_label(product: models.CardProduct) -> str:
    return " ".join(part for part in (product.issuer, product.product_name) if part)


def _identity_tokens(value: str | None) -> set[str]:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return {token for token in text.split() if token and token not in IDENTITY_STOPWORDS}


def _identity_text(value: str | None) -> str:
    return " ".join(sorted(_identity_tokens(value)))


def _row_matches_product(row: extract.OfferScanRow | None, product: models.CardProduct) -> bool:
    if row is None:
        return False
    product_name = _identity_tokens(product.product_name)
    row_name = _identity_tokens(row.card_name)
    if not product_name or not row_name:
        return False

    issuer = _identity_text(product.issuer)
    row_issuer = _identity_text(row.issuer)
    issuer_ok = not row_issuer or not issuer or issuer == row_issuer
    issuer_ok = issuer_ok or row_issuer in _identity_text(product.product_name)
    issuer_ok = issuer_ok or issuer in _identity_text(row.card_name)

    overlap = len(product_name & row_name)
    required = max(2, min(len(product_name), len(row_name)) - 1)
    name_ok = product_name == row_name or product_name.issubset(row_name) or row_name.issubset(product_name)
    name_ok = name_ok or overlap >= required
    return issuer_ok and name_ok


def _match_extracted_rows(
    products: list[models.CardProduct],
    rows: list[extract.OfferScanRow],
) -> dict[int, extract.OfferScanRow]:
    """Match LLM/web rows by identity, not by output order."""
    matches: dict[int, extract.OfferScanRow] = {}
    used: set[int] = set()
    for product in products:
        for idx, row in enumerate(rows):
            if idx in used:
                continue
            if _row_matches_product(row, product):
                matches[product.id] = row
                used.add(idx)
                break
    return matches


def _batch_label(batch: list) -> str | None:
    if not batch:
        return None
    products = [item[0] if isinstance(item, tuple) else item for item in batch]
    first = _product_label(products[0])
    if len(products) == 1:
        return first
    return f"{first} + {len(products) - 1} more"


def _eligibility_tags(text: str | None) -> list[str] | None:
    if not text:
        return None
    low = text.lower()
    tags: list[str] = []
    if "5/24" in low:
        tags.append("requires_under_524")
    if "once per lifetime" in low or ("lifetime" in low and "american express" in low):
        tags.append("amex_once_per_lifetime")
    if "48 month" in low and "sapphire" in low:
        tags.append("sapphire_48mo")
    if "24 month" in low:
        tags.append("bonus_24mo")
    return tags or None


def _is_cash_unit(unit: str | None) -> bool:
    return any(k in (unit or "").lower() for k in ("cash", "statement credit", "dollar"))


def _earn_multipliers(value: dict[str, float] | None) -> list[extract.EarnMultiplier] | None:
    out: list[extract.EarnMultiplier] = []
    for category, multiplier in (value or {}).items():
        if not category or multiplier is None:
            continue
        try:
            out.append(extract.EarnMultiplier(category=str(category), multiplier=float(multiplier)))
        except (TypeError, ValueError):
            continue
    return out or None


def _row_to_extraction(row: extract.OfferScanRow) -> extract.OfferExtraction:
    is_cash = _is_cash_unit(row.bonus_unit)
    current_offer_cash = float(row.bonus_amount) if row.bonus_amount is not None and is_cash else None
    current_offer_points = (
        int(row.bonus_amount)
        if row.bonus_amount is not None and not is_cash
        else None
    )
    currency = "cash back" if is_cash else row.bonus_unit

    # Peak ("target/goal") is points-only — cash peaks don't drive peak_score.
    peak_offer_points = (
        int(row.peak_bonus_amount)
        if row.peak_bonus_amount is not None and not _is_cash_unit(row.peak_bonus_unit)
        else None
    )
    # Peak can never be below the current offer, but do not invent a historical
    # peak when the source only provides the current offer.
    if current_offer_points is not None and peak_offer_points is not None:
        peak_offer_points = max(peak_offer_points, current_offer_points)

    referral_bonus_points = (
        int(row.referral_bonus_amount)
        if row.referral_bonus_amount is not None and not _is_cash_unit(row.referral_bonus_unit)
        else None
    )
    referral_bonus_cash = (
        float(row.referral_bonus_amount)
        if row.referral_bonus_amount is not None and _is_cash_unit(row.referral_bonus_unit)
        else None
    )

    return extract.OfferExtraction(
        found=row.found,
        confidence=row.confidence,
        currency=currency,
        annual_fee=row.annual_fee,
        current_offer_points=current_offer_points,
        current_offer_cash=current_offer_cash,
        current_offer_min_spend=row.spend_requirement,
        current_offer_window_months=row.spend_window_months,
        peak_offer_points=peak_offer_points,
        peak_offer_min_spend=row.peak_spend_requirement,
        peak_offer_date=row.peak_offer_date,
        peak_offer_source=row.peak_offer_source,
        referral_bonus_points=referral_bonus_points,
        referral_bonus_cash=referral_bonus_cash,
        first_year_credit_value=row.first_year_credit_value,
        earn_multipliers=_earn_multipliers(row.earn_multipliers),
        best_category_uses=row.best_category_uses,
        card_benefits=row.card_benefits,
        downgrade_paths=row.downgrade_paths,
        eligibility_tags=row.eligibility_tags or _eligibility_tags(row.eligibility_language),
        reports_to_personal_credit=row.reports_to_personal_credit,
        offer_expiration=row.offer_expiration,
        eligibility_language=row.eligibility_language,
        is_targeted=row.is_targeted,
        offer_status=row.offer_status,
        source_priority=row.source_priority,
        evidence_snippets=row.evidence_snippets,
        changed_fields=row.changed_fields,
        needs_review_reason=row.needs_review_reason,
        source_url=row.source_url,
        product_url=row.product_url,
        fetched_at=row.fetched_at,
        content_hash=row.content_hash,
    )


def _can_apply(row: extract.OfferScanRow) -> bool:
    has_public_offer = row.offer_status == "public" and row.bonus_amount is not None
    supplemental_fields = {
        "earn_multipliers": row.earn_multipliers,
        "best_category_uses": row.best_category_uses,
        "card_benefits": row.card_benefits,
        "downgrade_paths": row.downgrade_paths,
        "referral_bonus_amount": row.referral_bonus_amount,
        "first_year_credit_value": row.first_year_credit_value,
        "annual_fee": row.annual_fee,
    }
    present_supplemental_fields = [
        field for field, value in supplemental_fields.items() if value not in (None, [], {})
    ]
    has_supplemental_facts = bool(present_supplemental_fields)
    if has_supplemental_facts and not has_public_offer:
        has_source = _is_real_public_url(row.source_url or row.product_url)
        has_evidence = any(
            row.evidence_snippets.get(field) for field in present_supplemental_fields
        )
        if not (has_source and has_evidence):
            return False
    return (
        row.found
        and (has_public_offer or has_supplemental_facts)
        and row.offer_status not in {"targeted", "expired", "affiliate", "needs_review"}
        and not row.is_targeted
        and row.confidence >= MIN_CONFIDENCE
    )


SUPPLEMENTAL_FIELDS = {
    "earn_multipliers",
    "best_category_uses",
    "card_benefits",
    "downgrade_paths",
}

BROAD_SOURCE_FACT_FIELDS = {
    "annual_fee",
    "first_year_credit_value",
    "referral_bonus_amount",
    "referral_bonus_unit",
}

BROAD_SOURCE_FIELD_GROUPS = {
    "bonus_amount": ("bonus_amount", "bonus_unit"),
    "spend_requirement": ("spend_requirement", "spend_window_months"),
    "annual_fee": ("annual_fee",),
    "peak_bonus_amount": (
        "peak_bonus_amount",
        "peak_bonus_unit",
        "peak_spend_requirement",
        "peak_offer_date",
        "peak_offer_source",
    ),
    "referral_bonus_amount": ("referral_bonus_amount", "referral_bonus_unit"),
    "first_year_credit_value": ("first_year_credit_value",),
}


def _has_supplemental_facts(row: extract.OfferScanRow) -> bool:
    return any(getattr(row, field, None) not in (None, [], {}) for field in SUPPLEMENTAL_FIELDS)


def _has_variant_conflict(product: models.CardProduct, text: str | None) -> bool:
    """Reject close-name crossovers like personal Venture X vs Venture X Business."""
    haystack = _identity_tokens(text)
    if not haystack:
        return False
    product_tokens = _identity_tokens(product.product_name)
    ownership = (product.ownership or "").strip().lower()
    product_is_business = ownership == "business" or "business" in product_tokens
    if "business" in haystack and not product_is_business:
        return True

    conflict_groups = (
        {"preferred", "reserve"},
        {"flex", "unlimited"},
        {"cash", "preferred", "unlimited", "premier"},
        {"gold", "platinum", "green", "blue"},
        {"bold", "boundless", "brilliant", "bevy", "bountiful"},
    )
    for group in conflict_groups:
        product_hits = product_tokens & group
        text_hits = haystack & group
        if product_hits and text_hits and product_hits.isdisjoint(text_hits):
            return True

    # Venture X and plain Venture are separate products. Treat an x-bearing
    # source as conflicting for plain Venture, and a non-x Venture source as
    # insufficiently specific for Venture X.
    if "venture" in product_tokens and "venture" in haystack:
        product_has_x = "x" in product_tokens
        text_has_x = "x" in haystack
        if product_has_x != text_has_x:
            return True
    return False


def _source_allows_supplemental(row: extract.OfferScanRow, product: models.CardProduct) -> bool:
    url = row.source_url or row.product_url
    if not _is_real_public_url(url):
        return False
    parsed = urlsplit(url)
    path_text = re.sub(r"[^a-z0-9]+", " ", (parsed.path or "").lower())
    host = (parsed.hostname or "").lower()
    title_and_path = " ".join(
        str(part or "")
        for part in (
            row.source_url,
            row.product_url,
            row.card_name,
        )
    )
    if _has_variant_conflict(product, title_and_path):
        return False
    product_tokens = {
        token
        for token in _identity_tokens(product.product_name)
        if token not in {"card", "credit", "rewards", "reward", "from"}
    }
    token_hits = len(product_tokens.intersection(path_text.split()))
    required_hits = 1 if len(product_tokens) == 1 else max(2, min(3, len(product_tokens)))
    looks_product_specific = bool(product_tokens) and token_hits >= required_hits
    if looks_product_specific:
        return True
    issuer_key = re.sub(r"[^a-z0-9]+", "", (product.issuer or "").lower())
    official_hosts = {
        "americanexpress": "americanexpress.com",
        "chase": "chase.com",
        "capitalone": "capitalone.com",
        "citi": "citi.com",
        "wellsfargo": "wellsfargo.com",
        "bankofamerica": "bankofamerica.com",
    }
    official = official_hosts.get(issuer_key)
    if official and official in host and row.source_priority == 1:
        return True
    return False


def _evidence_supports_product(product: models.CardProduct, snippets: list[str] | None) -> bool:
    if not snippets:
        return False
    haystack = _identity_tokens(" ".join(str(snippet or "") for snippet in snippets))
    if not haystack:
        return False
    product_tokens = {
        token
        for token in _identity_tokens(product.product_name)
        if token not in {"card", "credit", "rewards", "reward", "from"}
    }
    if not product_tokens:
        return False
    overlap = len(product_tokens & haystack)
    required = 1 if len(product_tokens) == 1 else max(2, min(3, len(product_tokens)))
    return overlap >= required


def _drop_unsupported_broad_fields(row: extract.OfferScanRow, product: models.CardProduct) -> extract.OfferScanRow:
    if _source_allows_supplemental(row, product):
        return row
    matched_row_identity = _row_matches_product(row, product)
    for evidence_key, fields in BROAD_SOURCE_FIELD_GROUPS.items():
        if not any(getattr(row, field, None) not in (None, [], {}) for field in fields):
            continue
        snippets = row.evidence_snippets.get(evidence_key) or []
        if _evidence_supports_product(product, snippets):
            continue
        if evidence_key in {"bonus_amount", "spend_requirement"} and matched_row_identity:
            continue
        for field in fields:
            setattr(row, field, None)
        row.evidence_snippets.pop(evidence_key, None)
        row.needs_review_reason = row.needs_review_reason or "field_evidence_missing_product_identity"
    return row


def _drop_unsafe_supplemental(row: extract.OfferScanRow, product: models.CardProduct) -> extract.OfferScanRow:
    if _source_allows_supplemental(row, product):
        return row
    unsafe_fields = {
        field
        for field in (SUPPLEMENTAL_FIELDS | BROAD_SOURCE_FACT_FIELDS)
        if getattr(row, field, None) not in (None, [], {})
    }
    if not unsafe_fields:
        return row
    for field in unsafe_fields:
        setattr(row, field, None)
        row.evidence_snippets.pop(field, None)
    row.needs_review_reason = row.needs_review_reason or "product_specific_fields_ignored_from_broad_source"
    return row


def _apply_scan_row(
    db: Session,
    product: models.CardProduct,
    row: extract.OfferScanRow,
) -> dict:
    if _has_variant_conflict(product, f"{row.source_url or ''} {row.product_url or ''}"):
        return {
            "committed": [],
            "proposed": [],
            "note": "source_variant_conflict",
            "source_url": row.source_url,
            "scan_confidence": row.confidence,
            "offer_status": row.offer_status,
        }
    row = _drop_unsupported_broad_fields(row, product)
    row = _drop_unsafe_supplemental(row, product)
    if not _can_apply(row):
        return {
            "committed": [],
            "proposed": [],
            "note": row.needs_review_reason
            or f"not committed: status={row.offer_status}, confidence={row.confidence}",
            "source_url": row.source_url,
            "scan_confidence": row.confidence,
            "offer_status": row.offer_status,
        }

    ext = _row_to_extraction(row)
    applied = validate.apply_extraction(
        db, product, ext, row.source_url or row.product_url or "", commit=False
    )
    row.changed_fields = list(dict.fromkeys(applied.get("committed", []) + applied.get("proposed", [])))
    applied.update(
        {
            "source_url": row.source_url,
            "scan_confidence": row.confidence,
            "offer_status": row.offer_status,
        }
    )
    return applied


def _best_row(rows: list[extract.OfferScanRow], product: models.CardProduct | None = None) -> extract.OfferScanRow | None:
    if not rows:
        return None

    def supplemental_key(row: extract.OfferScanRow) -> tuple:
        supplemental = sum(
            int(getattr(row, field, None) not in (None, [], {}))
            for field in SUPPLEMENTAL_FIELDS
        )
        return (supplemental, -row.source_priority, row.confidence or 0)

    if product is not None and not _needs_offer_backfill(product):
        safe_supplemental_rows = [
            row
            for row in rows
            if _has_supplemental_facts(row) and _source_allows_supplemental(row, product)
        ]
        if safe_supplemental_rows:
            return max(safe_supplemental_rows, key=supplemental_key)

    def key(row: extract.OfferScanRow) -> tuple:
        public_offer = int(row.offer_status == "public" and row.bonus_amount is not None)
        peak = int(row.peak_bonus_amount is not None)
        has_supplemental = _has_supplemental_facts(row)
        safe_supplemental = int(
            has_supplemental and (product is None or _source_allows_supplemental(row, product))
        )
        supplemental = sum(
            int(getattr(row, field, None) not in (None, [], {}))
            for field in SUPPLEMENTAL_FIELDS
        )
        return (public_offer, peak, safe_supplemental, supplemental, -row.source_priority, row.confidence or 0)

    return max(rows, key=key)


def _fill_row_metadata(
    row: extract.OfferScanRow,
    product: models.CardProduct,
    fallback: extract.OfferScanRow | None,
) -> extract.OfferScanRow:
    row.issuer = row.issuer or product.issuer
    row.card_name = row.card_name or product.product_name
    row.is_business_card = row.is_business_card or (product.ownership or "").lower() == "business"
    if fallback:
        row.product_url = row.product_url or fallback.product_url
        row.source_url = row.source_url or fallback.source_url
        row.fetched_at = row.fetched_at or fallback.fetched_at
        row.content_hash = row.content_hash or fallback.content_hash
        if not row.evidence_snippets:
            row.evidence_snippets = fallback.evidence_snippets
    return row


def _scan_product_static(
    product: models.CardProduct,
    pages_by_url: dict[str, static_parse.ParsedPage],
    source_urls: list[str],
    product_source_urls: list[str] | None = None,
) -> tuple[extract.OfferScanRow | None, extract.OfferSnippetInput | None]:
    rows: list[extract.OfferScanRow] = []
    compact_snippets: list[str] = []
    urls: list[str] = []
    ordered_urls = []
    ordered_urls.extend(product_source_urls or [])
    if product.source_url:
        ordered_urls.append(product.source_url)
    ordered_urls.extend(source_urls)
    product_specific_urls = list(product_source_urls or [])
    if product.source_url:
        product_specific_urls.append(product.source_url)

    for url in dict.fromkeys(ordered_urls):
        page = pages_by_url.get(url)
        if not page:
            continue
        assume_product_page = any(
            _same_url(url, product_url) or _same_url(page.final_url, product_url)
            for product_url in product_specific_urls
        )
        snippets = static_parse.snippets_for_card(
            page,
            product.issuer,
            product.product_name,
            assume_product_page=assume_product_page,
            max_snippets=20 if assume_product_page else 10,
        )
        if not snippets:
            continue
        row = static_parse.deterministic_offer_row(
            page=page,
            issuer=product.issuer,
            product_name=product.product_name,
            ownership=product.ownership,
            snippets=snippets,
            assume_product_page=assume_product_page,
        )
        rows.append(row)
        urls.append(page.final_url or page.url)
        # Compact prefix only: source URL + priority drive attribution/ranking.
        # content_hash/fetched_at are page-level provenance set in Python (via the
        # deterministic fallback row), so they don't belong in the LLM prompt.
        priority = static_parse.source_priority(page, product.issuer, assume_product_page)
        page_source = page.final_url or page.url
        for snippet in snippets:
            compact_snippets.append(
                "src={source} p={priority}\n{snippet}".format(
                    source=page_source,
                    priority=priority,
                    snippet=snippet[:SNIPPET_LLM_CHARS],
                )
            )

    best = _best_row(rows, product)
    if not compact_snippets:
        return best, None

    snippet_input = extract.OfferSnippetInput(
        issuer=product.issuer,
        card_name=product.product_name,
        ownership=product.ownership,
        candidate_urls=list(dict.fromkeys(urls)),
        snippets=compact_snippets[:MAX_SNIPPETS_PER_CARD],
        # Compact hint only — omit the verbose row (evidence_snippets etc.) that
        # would just duplicate the snippets and inflate input tokens.
        deterministic_guess=(
            {
                "bonus_amount": best.bonus_amount,
                "bonus_unit": best.bonus_unit,
                "spend_requirement": best.spend_requirement,
                "spend_window_months": best.spend_window_months,
                "annual_fee": best.annual_fee,
                "peak_bonus_amount": best.peak_bonus_amount,
                "offer_status": best.offer_status,
                "confidence": best.confidence,
            }
            if best
            else None
        ),
    )
    return best, snippet_input


def _has_current_offer(product: models.CardProduct) -> bool:
    return bool(
        product.current_offer_points
        or product.current_offer_cash
        or product.current_offer_min_spend
    )


def _has_peak_offer(product: models.CardProduct) -> bool:
    return bool(
        product.peak_offer_points
        or product.peak_offer_min_spend
        or product.targeted_peak_offer_points
        or product.targeted_peak_offer_cash
    )


def _needs_offer_backfill(product: models.CardProduct) -> bool:
    return not _has_current_offer(product) or not _has_peak_offer(product)


def _needs_supplemental_backfill(product: models.CardProduct) -> bool:
    return not (product.earn_multipliers and product.best_category_uses and product.card_benefits)


def _identity_key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


def _normalize_identity_refs(
    *,
    product_ids: list[int] | set[int] | None = None,
    product_keys: list[tuple[str, str]] | set[tuple[str, str]] | None = None,
    product_variants: list[tuple[str, str]] | set[tuple[str, str]] | None = None,
) -> tuple[set[int], set[tuple[str, str]], set[tuple[str, str]]]:
    ids = {int(product_id) for product_id in (product_ids or []) if product_id}
    keys = {
        ((issuer or "").strip().lower(), (product_name or "").strip().lower())
        for issuer, product_name in (product_keys or [])
        if issuer or product_name
    }
    variants = {
        ((issuer or "").strip().lower(), (variant or "").strip().lower())
        for issuer, variant in (product_variants or [])
        if issuer or variant
    }
    return ids, keys, variants


def _is_priority_product(
    product: models.CardProduct,
    priority_ids: set[int],
    priority_keys: set[tuple[str, str]],
    priority_variants: set[tuple[str, str]],
) -> bool:
    from ..product_identity import product_variant_key

    variant = product_variant_key(product.issuer, product.product_name)
    return (
        product.id in priority_ids
        or _identity_key(product.issuer, product.product_name) in priority_keys
        or (variant is not None and variant in priority_variants)
    )


def _needs_public_data_backfill(product: models.CardProduct, held_for_benefit_backfill: bool = True) -> bool:
    return _needs_offer_backfill(product) or (
        held_for_benefit_backfill and _needs_supplemental_backfill(product)
    )


def _pending_review_fields(db: Session) -> dict[int, set[str]]:
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


def run_refresh(
    db: Session,
    issuer: str | None = None,
    limit: int | None = 8,
    only_stale: bool | None = True,
    product_ids: list[int] | None = None,
    source_urls: list[str] | None = None,
    use_web_search: bool = False,
    refresh_stale_days: int | None = STALE_AFTER_DAYS,
    force: bool = False,
    batch_size: int | None = None,
    web_fallback_limit: int | None = None,
    llm_fallback: bool = True,
    include_incomplete: bool = True,
    incomplete_only: bool = False,
    peak_backfill: bool = False,
    refresh_valuations: bool = False,
    valuations_only: bool = False,
    use_rendered_fallback: bool | None = None,
    priority_product_ids: list[int] | None = None,
    priority_product_keys: list[tuple[str, str]] | None = None,
    priority_product_variants: list[tuple[str, str]] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    if valuations_only:
        valuations = backfill_valuations(db)
        if progress_callback:
            progress_callback(
                {
                    "phase": "complete",
                    "processed": 0,
                    "total": 0,
                    "skipped": 0,
                    "committed": 0,
                    "proposed": 0,
                    "errors": [],
                    "warnings": [],
                }
            )
        return {
            "refreshed_count": 0,
            "products_checked": 0,
            "products_skipped": 0,
            "products_pending_review": 0,
            "products_skipped_filtered": 0,
            "products_deferred_by_limit": 0,
            "committed": 0,
            "proposed": 0,
            "errors": [],
            "warnings": [],
            "results": [],
            "rows": [],
            "scan": {
                "mode": "valuations_only",
                "selected_products": 0,
                "candidate_urls": 0,
                "fetched_urls": 0,
                "cache_hits": 0,
                "cache_misses_or_updates": 0,
                "deterministic_applied": 0,
                "llm_batches": 0,
                "llm_rows": 0,
                "web_search_used": False,
                "web_search_requests": 0,
                "rendered_fallback_used": False,
                "rendered_urls": 0,
                "rendered_pages": 0,
                "refresh_valuations": True,
            },
            "peaks_filled": 0,
            "valuations_added": valuations,
        }

    all_products = effective_catalog(db)
    if issuer:
        all_products = [p for p in all_products if issuer.lower() in (p.issuer or "").lower()]
    if product_ids:
        wanted = set(product_ids)
        all_products = [p for p in all_products if p.id in wanted]

    priority_ids, priority_keys, priority_variants = _normalize_identity_refs(
        product_ids=priority_product_ids,
        product_keys=priority_product_keys,
        product_variants=priority_product_variants,
    )

    def needs_backfill(product: models.CardProduct) -> bool:
        return _needs_public_data_backfill(
            product,
            _is_priority_product(product, priority_ids, priority_keys, priority_variants),
        )

    def is_priority(product: models.CardProduct) -> bool:
        return _is_priority_product(product, priority_ids, priority_keys, priority_variants)

    def priority_benefit_gap(product: models.CardProduct) -> bool:
        return is_priority(product) and _needs_supplemental_backfill(product)

    pending_fields_by_id = _pending_review_fields(db)
    pending_ids = set(pending_fields_by_id)

    def only_supplemental_pending(product: models.CardProduct) -> bool:
        fields = pending_fields_by_id.get(product.id or 0, set())
        return bool(fields) and fields.issubset(SUPPLEMENTAL_FIELDS)

    products_without_pending = [
        p
        for p in all_products
        if p.id not in pending_ids or priority_benefit_gap(p) or (is_priority(p) and only_supplemental_pending(p))
    ]
    skipped_pending = len(all_products) - len(products_without_pending)

    stale_days = STALE_AFTER_DAYS if refresh_stale_days is None else refresh_stale_days
    if peak_backfill:
        products = [p for p in products_without_pending if not _has_peak_offer(p)]
    elif incomplete_only:
        products = [p for p in products_without_pending if needs_backfill(p)]
    elif force or only_stale is False:
        products = products_without_pending
    else:
        products = [
            p
            for p in products_without_pending
            if _is_stale(p, stale_days) or (include_incomplete and needs_backfill(p))
        ]

    products.sort(
        key=lambda p: (
            0 if priority_benefit_gap(p) else 1 if is_priority(p) else 2,
            p.last_verified or dt.datetime.min,
            p.id or 0,
        )
    )
    products_selected_before_limit = len(products)
    limit_deferred = 0
    if limit is not None:
        limit_deferred = max(0, len(products) - max(0, limit))
        products = products[: max(0, limit)]
    initial_stale_by_id = {
        p.id: bool(force or _is_stale(p, stale_days) or needs_backfill(p))
        for p in products
    }
    products_skipped_total = max(0, len(all_products) - len(products))
    products_skipped_filtered = max(
        0,
        len(products_without_pending) - products_selected_before_limit,
    )

    source_pool = active_source_urls(db, source_urls)
    product_sources = product_source_urls(db, products)
    urls = _candidate_urls(products, source_pool, product_sources)
    fetched_pages = fetch.fetch_many_pages(urls) if urls else {}

    results: list[dict] = []
    result_index: dict[int, int] = {}
    strict_rows: list[extract.OfferScanRow] = []
    unresolved: list[tuple[models.CardProduct, extract.OfferScanRow | None, extract.OfferSnippetInput]] = []
    web_candidates: dict[int, models.CardProduct] = {}
    deterministic_applied = 0
    rendered_urls = 0
    rendered_pages = 0
    rendered_candidates = 0
    rendered_results = 0
    errors: list[dict] = []
    warnings: list[dict] = []
    resolver_stats: dict[str, int] = {
        "search_queries": 0,
        "cached_search_queries": 0,
        "urls_from_search": 0,
        "urls_fetched": 0,
        "detail_urls_discovered": 0,
        "detail_urls_fetched": 0,
        "cached_urls_reused": 0,
        "adapter_rows": 0,
        "llm_batches": 0,
        "llm_snippets_sent": 0,
        "research_text_snippets": 0,
        "cards_resolved": 0,
        "cards_needs_data": 0,
        "web_search_requests": 0,
    }
    priority_detail_products = [product for product in products if priority_benefit_gap(product)]
    if priority_detail_products and fetched_pages:
        try:
            link_hub_sources = {
                product.id: list(dict.fromkeys([*product_sources.get(product.id, []), *source_pool]))
                for product in priority_detail_products
            }
            detail_urls_by_product = research_resolver._discover_detail_urls(
                priority_detail_products,
                fetched_pages,
                link_hub_sources,
            )
            detail_urls = list(dict.fromkeys(url for urls in detail_urls_by_product.values() for url in urls))
            if detail_urls:
                for product_id, urls_for_product in detail_urls_by_product.items():
                    product_sources.setdefault(product_id, []).extend(urls_for_product)
                    product_sources[product_id] = list(dict.fromkeys(product_sources[product_id]))
                missing_detail_urls = [url for url in detail_urls if url not in fetched_pages]
                if missing_detail_urls:
                    fetched_pages.update(fetch.fetch_many_pages(missing_detail_urls))
                resolver_stats["detail_urls_discovered"] += len(detail_urls)
                resolver_stats["detail_urls_fetched"] += len([url for url in detail_urls if url in fetched_pages])
        except Exception as exc:
            warnings.append(
                {
                    "code": "static_detail_discovery_failed",
                    "message": str(exc),
                }
            )
    pages_by_url = {url: static_parse.parse_page(page) for url, page in fetched_pages.items()}

    def add_result(product: models.CardProduct, applied: dict, row: extract.OfferScanRow, engine: str) -> None:
        entry = {
            "id": product.id,
            "issuer": product.issuer,
            "product_name": product.product_name,
            "result": applied,
            "scan_row": row.model_dump(mode="json"),
            "engine": engine,
        }
        if product.id in result_index:
            results[result_index[product.id]] = entry
        else:
            result_index[product.id] = len(results)
            results.append(entry)

    def note_result(
        product: models.CardProduct,
        row: extract.OfferScanRow,
        engine: str,
        *,
        requeue_web: bool = True,
    ) -> None:
        add_result(
            product,
            {
                "committed": [],
                "proposed": [],
                "note": row.needs_review_reason,
                "source_url": row.source_url,
                "scan_confidence": row.confidence,
                "offer_status": row.offer_status,
            },
            row,
            engine,
        )
        if requeue_web:
            web_candidates[product.id] = product

    def result_counts() -> tuple[int, int]:
        committed = sum(len(x["result"].get("committed", [])) for x in results)
        proposed = sum(len(x["result"].get("proposed", [])) for x in results)
        return committed, proposed

    def publish_progress(phase: str, processed: int, current_product: str | None = None) -> None:
        if not progress_callback:
            return
        committed, proposed = result_counts()
        progress_callback(
            {
                "phase": phase,
                "processed": processed,
                "total": len(products),
                "skipped": products_skipped_total,
                "current_product": current_product,
                "committed": committed,
                "proposed": proposed,
                "cards_queued": len(products),
                "cached_urls_reused": sum(1 for page in fetched_pages.values() if page.from_cache)
                + resolver_stats["cached_urls_reused"],
                "urls_fetched": len(fetched_pages) + resolver_stats["urls_fetched"],
                "cards_resolved": len(
                    [
                        x
                        for x in results
                        if x["result"].get("committed") or x["result"].get("proposed")
                    ]
                ),
                "cards_needs_review": len([x for x in results if x["result"].get("note")]),
                "cards_needs_data": resolver_stats["cards_needs_data"],
                "web_search_requests": resolver_stats.get("web_search_requests", 0),
                "rendered_urls": rendered_urls,
                "rendered_pages": rendered_pages,
                "warnings": warnings,
            }
        )

    for processed, product in enumerate(products, start=1):
        publish_progress("static_parse", processed - 1, _product_label(product))
        best, snippet_input = _scan_product_static(
            product, pages_by_url, source_pool, product_sources.get(product.id, [])
        )
        supplemental_shortcut = bool(best) and priority_benefit_gap(product) and _has_supplemental_facts(best)
        needs_llm_structure = (
            bool(best)
            and bool(snippet_input)
            and extract.has_unstructured_supplemental(best)
            and llm_fallback
            and config.llm_available()
        )
        can_shortcut = bool(best) and _can_apply(best) and (
            best.confidence >= DETERMINISTIC_CONFIDENCE
            or (supplemental_shortcut and best.confidence >= MIN_CONFIDENCE)
        ) and not needs_llm_structure
        if can_shortcut:
            applied = _apply_scan_row(db, product, best)
            deterministic_applied += 1
            strict_rows.append(best)
            add_result(product, applied, best, "static")
            if use_web_search and include_incomplete and needs_backfill(product):
                web_candidates[product.id] = product
            publish_progress("static_parse", len(result_index), _product_label(product))
            continue

        if snippet_input:
            unresolved.append((product, best, snippet_input))
            continue

        row = best or extract.OfferScanRow(
            issuer=product.issuer,
            card_name=product.product_name,
            is_business_card=(product.ownership or "").lower() == "business",
            offer_status="needs_review",
            confidence=0.0,
            found=False,
            needs_review_reason="no_static_snippets_found",
        )
        strict_rows.append(row)
        note_result(product, row, "static")
        publish_progress("static_parse", len(result_index), _product_label(product))

    llm_batches = 0
    batch_limit = batch_size or LLM_BATCH_SIZE
    if unresolved and llm_fallback and config.llm_available():
        for batch in _chunks(unresolved, batch_limit):
            llm_batches += 1
            inputs = [item[2] for item in batch]
            publish_progress("llm_batch", len(result_index), _batch_label(batch))
            try:
                rows = extract.extract_offers_batch(inputs)
            except extract.IngestionUnavailable as exc:
                rows = []
                schema_limit = _is_llm_schema_limit_error(exc)
                notice = {
                    "product": "llm_batch",
                    "error": str(exc),
                }
                if schema_limit:
                    warnings.append(
                        {
                            "code": "llm_schema_limit",
                            "message": "Batched LLM cleanup skipped because the structured schema was too large; static extraction results were kept.",
                        }
                    )
                else:
                    errors.append(notice)
                for product, fallback, _ in batch:
                    row = fallback or extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        is_business_card=(product.ownership or "").lower() == "business",
                        offer_status="needs_review",
                        confidence=0.0,
                        found=False,
                    )
                    if schema_limit and fallback is not None:
                        row = extract.normalize_offer_scan_row(_fill_row_metadata(row, product, fallback))
                        applied = _apply_scan_row(db, product, row)
                        strict_rows.append(row)
                        add_result(product, applied, row, "static_schema_fallback")
                        if not applied.get("committed") and not applied.get("proposed"):
                            web_candidates[product.id] = product
                        else:
                            web_candidates.pop(product.id, None)
                        publish_progress("llm_batch", len(result_index), _product_label(product))
                        continue
                    row.needs_review_reason = f"llm_batch_unavailable: {exc}"
                    strict_rows.append(row)
                    note_result(product, row, "static")
                    publish_progress("llm_batch", len(result_index), _product_label(product))
                continue
            for idx, (product, fallback, _) in enumerate(batch):
                row = rows[idx] if idx < len(rows) else fallback
                if row is None:
                    row = extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        is_business_card=(product.ownership or "").lower() == "business",
                        offer_status="needs_review",
                        confidence=0.0,
                        found=False,
                        needs_review_reason="llm_returned_no_row",
                    )
                elif not _row_matches_product(row, product):
                    row = extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        is_business_card=(product.ownership or "").lower() == "business",
                        offer_status="needs_review",
                        confidence=0.0,
                        found=False,
                        needs_review_reason="llm_identity_mismatch",
                    )
                row = extract.normalize_offer_scan_row(_fill_row_metadata(row, product, fallback))
                applied = _apply_scan_row(db, product, row)
                strict_rows.append(row)
                add_result(product, applied, row, "llm_batch")
                if not applied.get("committed") and not applied.get("proposed"):
                    web_candidates[product.id] = product
                else:
                    web_candidates.pop(product.id, None)
                publish_progress("llm_batch", len(result_index), _product_label(product))
    else:
        for product, fallback, _ in unresolved:
            row = fallback or extract.OfferScanRow(
                issuer=product.issuer,
                card_name=product.product_name,
                is_business_card=(product.ownership or "").lower() == "business",
                offer_status="needs_review",
                confidence=0.0,
                found=False,
                needs_review_reason="llm_unavailable_for_exception_batch",
            )
            if row.needs_review_reason is None:
                row.needs_review_reason = "static_confidence_below_commit_threshold"
            strict_rows.append(row)
            note_result(product, row, "static")
            publish_progress("static_parse", len(result_index), _product_label(product))

    db.commit()

    rendered_requested = bool(use_rendered_fallback)
    rendered_available = rendered_requested and config.CRAWL4AI_ENABLED
    if rendered_requested and not config.CRAWL4AI_ENABLED and web_candidates:
        warnings.append(
            {
                "code": "crawl4ai_disabled",
                "message": "Rendered fallback was requested but CRAWL4AI_ENABLED is false.",
            }
        )
    if rendered_available and web_candidates:
        rendered_raw_candidates = list(web_candidates.values())
        render_urls_by_product: dict[int, list[str]] = {
            product.id: _product_render_urls(product, product_sources.get(product.id, []))
            for product in rendered_raw_candidates
            if product.id
        }
        render_urls = list(
            dict.fromkeys(url for urls_for_product in render_urls_by_product.values() for url in urls_for_product)
        )
        if render_urls:
            publish_progress("rendered_fetch", len(result_index), _batch_label(rendered_raw_candidates))
            report = rendered_fetch.render_many_pages(
                render_urls,
                timeout_ms=config.CRAWL4AI_TIMEOUT_MS,
                max_urls=config.CRAWL4AI_MAX_URLS_PER_REFRESH,
            )
            rendered_urls = report.attempted_urls
            rendered_pages = report.rendered_pages
            errors.extend(report.errors)
            warnings.extend(report.warnings)
            if report.pages:
                fetched_pages.update(report.pages)
                pages_by_url.update({url: static_parse.parse_page(page) for url, page in report.pages.items()})
            for product in rendered_raw_candidates:
                candidate_urls = render_urls_by_product.get(product.id or 0, [])
                if not candidate_urls or not any(url in report.pages for url in candidate_urls):
                    continue
                rendered_candidates += 1
                best, _ = _scan_product_static(
                    product,
                    pages_by_url,
                    source_pool,
                    product_sources.get(product.id, []),
                )
                if not best:
                    continue
                best = extract.normalize_offer_scan_row(_fill_row_metadata(best, product, None))
                applied = _apply_scan_row(db, product, best)
                strict_rows.append(best)
                add_result(product, applied, best, "crawl4ai")
                if applied.get("committed") or applied.get("proposed"):
                    rendered_results += 1
                    web_candidates.pop(product.id, None)
                publish_progress("rendered_fetch", len(result_index), _product_label(product))
            db.commit()

    # Escalate to cited web search only after static/LLM work leaves the card
    # unresolved or incomplete. This is the expensive fallback path.
    if use_web_search and include_incomplete:
        for product in products:
            if needs_backfill(product):
                web_candidates.setdefault(product.id, product)

    web_search_requests = 0
    web_search_deferred = 0
    web_search_cooldown_skipped = 0
    supplemental_search_cooldown_skipped = 0
    web_search_stale_skipped = 0
    source_urls_promoted = 0
    reference_urls_promoted = 0
    web_results = 0
    if use_web_search and not config.WEB_SEARCH_ENABLED and web_candidates:
        warnings.append(
            {
                "code": "web_search_disabled",
                "message": "Web fallback was requested but WEB_SEARCH_ENABLED is false.",
            }
        )
    if use_web_search and config.WEB_SEARCH_ENABLED and web_candidates:
        now = _utcnow()
        raw_candidates = list(web_candidates.values())
        candidates: list[models.CardProduct] = []
        for product in raw_candidates:
            if not initial_stale_by_id.get(product.id, _is_stale(product, stale_days)):
                web_search_stale_skipped += 1
                continue
            supplemental_gap = priority_benefit_gap(product)
            if supplemental_gap and not force and not _supplemental_search_cooldown_open(product, now):
                supplemental_search_cooldown_skipped += 1
                continue
            if not supplemental_gap and not _web_search_cooldown_open(product, now):
                web_search_cooldown_skipped += 1
                continue
            candidates.append(product)
        if web_search_stale_skipped:
            warnings.append(
                {
                    "code": "web_search_stale_gate",
                    "message": f"{web_search_stale_skipped} unresolved card(s) skipped because they are not stale.",
                }
            )
        if web_search_cooldown_skipped:
            warnings.append(
                {
                    "code": "web_search_cooldown",
                    "message": f"{web_search_cooldown_skipped} unresolved card(s) skipped by the web-search cooldown.",
                }
            )
        if supplemental_search_cooldown_skipped:
            warnings.append(
                {
                    "code": "supplemental_search_cooldown",
                    "message": (
                        f"{supplemental_search_cooldown_skipped} priority benefit/multiplier gap(s) "
                        "skipped by the supplemental-search cooldown."
                    ),
                }
            )
        web_batch_limit = max(1, config.WEB_SEARCH_BATCH_SIZE)
        cap = web_fallback_limit
        if cap is None:
            cap = config.WEB_SEARCH_MAX_CARDS
        if cap >= 0:
            web_search_deferred = max(0, len(candidates) - cap)
            candidates = candidates[:cap]
            if web_search_deferred:
                warnings.append(
                    {
                        "code": "web_search_deferred",
                        "message": f"{web_search_deferred} unresolved card(s) deferred by the web fallback limit.",
                    }
                )
        for batch in _chunks(candidates, web_batch_limit):
            publish_progress("web_search_batch", len(result_index), _batch_label(batch))
            attempted_at = _utcnow()
            for product in batch:
                product.last_web_search_at = attempted_at
                if priority_benefit_gap(product):
                    product.last_supplemental_search_at = attempted_at
            db.flush()
            try:
                resolution = research_resolver.resolve_products(
                    batch,
                    db=db,
                    llm_fallback=llm_fallback,
                    max_uses=max(3, min(config.WEB_SEARCH_MAX_USES_PER_BATCH, len(batch) + 2)),
                )
                for key, value in resolution.stats.items():
                    if key in resolver_stats and isinstance(value, int):
                        resolver_stats[key] += value
                web_search_requests += int(resolution.stats.get("web_search_requests") or 0)
                errors.extend(resolution.errors)
                warnings.extend(resolution.warnings)
            except Exception as exc:
                errors.append({"product": "web_search_batch", "error": str(exc)})
                for product in batch:
                    row = extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        offer_status="needs_review",
                        confidence=0.0,
                        found=False,
                        needs_review_reason=f"web_search_error: {exc}",
                    )
                    strict_rows.append(row)
                    web_candidates.pop(product.id, None)
                    note_result(product, row, "web_search", requeue_web=False)
                    publish_progress("web_search_batch", len(result_index), _product_label(product))
                continue
            results_by_product = {item.product_id: item for item in resolution.product_results}
            for product in batch:
                product_result = results_by_product.get(product.id)
                source_urls = product_result.source_urls if product_result else []
                row = product_result.row if product_result else None
                if not row:
                    warnings.append(
                        {
                            "code": "web_search_no_result",
                            "message": f"Web search returned no matching offer row for {_product_label(product)}.",
                        }
                    )
                    row = extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        offer_status="needs_review",
                        confidence=0.0,
                        found=False,
                        needs_review_reason="web_search_returned_no_matching_row",
                    )
                row = extract.normalize_offer_scan_row(_fill_row_metadata(row, product, None))
                if not _row_matches_product(row, product):
                    row = extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        offer_status="needs_review",
                        confidence=0.0,
                        found=False,
                        needs_review_reason="web_search_identity_mismatch",
                    )
                if not row.found or (row.confidence or 0.0) < MIN_CONFIDENCE:
                    row = extract.OfferScanRow(
                        issuer=product.issuer,
                        card_name=product.product_name,
                        offer_status="needs_review",
                        confidence=row.confidence if row else 0.0,
                        found=False,
                        needs_review_reason=(row.needs_review_reason if row else None) or "web_search_did_not_resolve",
                    )
                    strict_rows.append(row)
                    web_candidates.pop(product.id, None)
                    note_result(product, row, "web_search", requeue_web=False)
                    publish_progress("web_search_batch", len(result_index), _product_label(product))
                    continue
                has_supplemental = _has_supplemental_facts(row)
                if row.offer_status not in {"public", "targeted"} and not has_supplemental:
                    row.needs_review_reason = row.needs_review_reason or f"web_search_offer_status_{row.offer_status}"
                    strict_rows.append(row)
                    web_candidates.pop(product.id, None)
                    note_result(product, row, "web_search", requeue_web=False)
                    publish_progress("web_search_batch", len(result_index), _product_label(product))
                    continue
                source = _best_real_source_url(row, source_urls) or ""
                if source:
                    row.source_url = source
                else:
                    if not _is_real_offer_url(row.source_url):
                        row.source_url = None
                    if not _is_real_offer_url(row.product_url):
                        row.product_url = None
                    row.needs_review_reason = row.needs_review_reason or "web_search_missing_real_cited_url"
                    warnings.append(
                        {
                            "code": "web_search_missing_real_cited_url",
                            "message": f"Web search found {_product_label(product)} but did not return a reusable cited offer URL.",
                        }
                    )
                    strict_rows.append(row)
                    web_candidates.pop(product.id, None)
                    note_result(product, row, "web_search", requeue_web=False)
                    publish_progress("web_search_batch", len(result_index), _product_label(product))
                    continue
                ext = _row_to_extraction(row)
                applied = validate.apply_extraction(db, product, ext, source, commit=False)
                if source:
                    product.source_url = source
                    source_urls_promoted += _promote_source_urls(db, [source], product_id=product.id)
                    if card_references.promote_reference_url(db, product, source):
                        reference_urls_promoted += 1
                row.changed_fields = list(dict.fromkeys(applied.get("committed", []) + applied.get("proposed", [])))
                strict_rows.append(row)
                add_result(product, applied, row, "web_search")
                web_candidates.pop(product.id, None)
                web_results += 1
                publish_progress("web_search_batch", len(result_index), _product_label(product))
        db.commit()

    peak_fields = {"peak_offer_points", "targeted_peak_offer_points"}
    peaks_filled = sum(
        1
        for item in results
        if peak_fields.intersection(item["result"].get("committed", []))
        or peak_fields.intersection(item["result"].get("proposed", []))
    )
    valuations = backfill_valuations(db) if (products and refresh_valuations) else 0
    cache_hits = sum(1 for page in fetched_pages.values() if page.from_cache)
    committed_count = sum(len(x["result"].get("committed", [])) for x in results)
    proposed_count = sum(len(x["result"].get("proposed", [])) for x in results)
    cards_resolved = len(
        [
            x
            for x in results
            if x["result"].get("committed") or x["result"].get("proposed")
        ]
    )
    cards_needs_review = len([x for x in results if x["result"].get("note")])
    cards_needs_data = max(0, len(products) - cards_resolved)

    if progress_callback:
        progress_callback(
            {
                "phase": "complete",
                "processed": len(results),
                "total": len(products),
                "skipped": products_skipped_total,
                "committed": committed_count,
                "proposed": proposed_count,
                "cards_queued": len(products),
                "cached_urls_reused": cache_hits + resolver_stats["cached_urls_reused"],
                "urls_fetched": len(fetched_pages) + resolver_stats["urls_fetched"],
                "cards_resolved": cards_resolved,
                "cards_needs_review": cards_needs_review,
                "cards_needs_data": cards_needs_data,
                "web_search_requests": web_search_requests,
                "rendered_urls": rendered_urls,
                "rendered_pages": rendered_pages,
                "errors": errors,
                "warnings": warnings,
            }
        )

    return {
        "refreshed_count": len(results),
        "products_checked": len(products),
        "products_skipped": products_skipped_total,
        "products_pending_review": skipped_pending,
        "products_skipped_filtered": products_skipped_filtered,
        "products_deferred_by_limit": limit_deferred,
        "committed": committed_count,
        "proposed": proposed_count,
        "errors": errors,
        "warnings": warnings,
        "results": results,
        "rows": [row.model_dump(mode="json") for row in strict_rows],
        "scan": {
            "mode": "static_http_cache_parse_deterministic_then_batched_llm_web_fallback",
            "selected_products": len(products),
            "candidate_urls": len(urls),
            "fetched_urls": len(fetched_pages),
            "cache_hits": cache_hits,
            "cache_misses_or_updates": len(fetched_pages) - cache_hits,
            "deterministic_applied": deterministic_applied,
            "llm_batches": llm_batches,
            "llm_rows": len([r for r in results if r.get("engine") == "llm_batch"]),
            "rendered_fallback_used": bool(rendered_pages),
            "rendered_urls": rendered_urls,
            "rendered_pages": rendered_pages,
            "rendered_candidates": rendered_candidates,
            "rendered_results": rendered_results,
            "web_search_used": bool(use_web_search and config.WEB_SEARCH_ENABLED and web_results),
            "web_search_requests": web_search_requests,
            "web_search_deferred": web_search_deferred,
            "web_search_cooldown_skipped": web_search_cooldown_skipped,
            "supplemental_search_cooldown_skipped": supplemental_search_cooldown_skipped,
            "web_search_stale_skipped": web_search_stale_skipped,
            "web_search_results": web_results,
            "web_search_unresolved_candidates": len(web_candidates),
            "research_resolver": resolver_stats,
            "cards_queued": len(products),
            "cached_urls_reused": cache_hits + resolver_stats["cached_urls_reused"],
            "urls_fetched": len(fetched_pages) + resolver_stats["urls_fetched"],
            "cards_resolved": cards_resolved,
            "cards_needs_review": cards_needs_review,
            "cards_needs_data": cards_needs_data,
            "source_urls_promoted": source_urls_promoted,
            "reference_urls_promoted": reference_urls_promoted,
            "peak_backfill": peak_backfill,
            "refresh_valuations": refresh_valuations,
        },
        "peaks_filled": peaks_filled,
        "valuations_added": valuations,
    }


def backfill_valuations(db: Session) -> int:
    """Fill cpp_scraped for catalog currencies that have no sourced valuation yet."""
    if not config.llm_available() or not config.WEB_SEARCH_ENABLED:
        return 0

    have = {
        (v.currency or "").strip().lower()
        for v in db.scalars(select(models.Valuation)).all()
    }
    currencies = sorted(
        {
            p.currency.strip()
            for p in effective_catalog(db)
            if p.currency and p.currency.strip().lower() not in have
        }
    )
    if not currencies:
        return 0

    added = 0
    try:
        research = extract.research_point_valuations(currencies)
        source_urls = [source["url"] for source in research.get("sources", []) if source.get("url")]
        extracted = extract.extract_valuations(currencies, research.get("text", ""), source_urls=source_urls)
    except extract.IngestionUnavailable:
        return 0

    for cv in extracted:
        source_url = _normalize_url(cv.source_url) if _is_real_public_url(cv.source_url) else None
        if source_url is None and source_urls:
            source_url = _normalize_url(source_urls[0]) if _is_real_public_url(source_urls[0]) else None
        if source_url is None:
            continue
        db.add(
            models.Valuation(
                currency=cv.currency.strip(),
                cpp_scraped=cv.cpp,
                source_url=source_url,
                last_verified=_utcnow(),
            )
        )
        added += 1
    db.commit()
    return added
