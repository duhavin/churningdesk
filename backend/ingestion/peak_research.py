"""Dedicated historic-peak resolver (2026-07-06).

The peak offer is the reference scale for apply timing, so it gets its own
narrow pipeline instead of riding the fragile batch extractor:

  one card -> cached peak-history web research -> one small structured LLM
  extraction -> verbatim-quote verification -> trusted-source gate -> apply.

Hard rules:
- The quoted evidence must literally contain the peak number (comma or
  plain form) or the value is discarded — the model cannot assert a peak
  it did not read.
- The cited source must be a peak-trusted domain (Doctor of Credit,
  Frequent Miler, US Credit Card Guide, TPG) or the issuer itself.
- A "peak" below the card's known current offer is rejected as nonsense.
- Nothing here reads PRIVATE tables.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models, source_quality
from . import extract, research_resolver, validate

# Rotation + cooldown: without it the first N peak-less products in table
# order burn LLM calls every refresh while later cards starve.
PEAK_RESEARCH_COOLDOWN_DAYS = int(os.getenv("WEWARDS_PEAK_RESEARCH_COOLDOWN_DAYS", "7"))


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class PeakFinding(BaseModel):
    found: bool = Field(description="True ONLY if the research text explicitly states an all-time-high/historical-best public offer for exactly this card")
    peak_points: int | None = Field(default=None, description="The highest publicly available welcome bonus in points/miles")
    peak_min_spend: float | None = None
    seen_date: str | None = Field(default=None, description="When that peak ran, e.g. '2024-11' or 'November 2024', if stated")
    source_url: str | None = Field(default=None, description="The cited URL the peak came from")
    quote: str | None = Field(default=None, description="Short VERBATIM quote from the research text stating the peak")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _number_in_quote(points: int, quote: str) -> bool:
    plain = str(points)
    comma = f"{points:,}"
    k_form = f"{points // 1000}k" if points % 1000 == 0 else None
    low = quote.lower()
    return plain in quote or comma in quote or (k_form is not None and k_form in low)


def _trusted_peak_source(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    # Strict host matching: notdoctorofcredit.com and purchase.com must fail;
    # sub.chase.com must pass.
    if source_quality.host_in(host, validate.PEAK_TRUSTED_HOSTS):
        return True
    return source_quality.host_in(host, research_resolver.ISSUER_DOMAINS.values())


def _extract_peak(product: models.CardProduct, research_text: str) -> PeakFinding | None:
    client = extract._client()
    prompt = (
        "You are extracting ONE fact from credit-card research text: the highest "
        "PUBLICLY AVAILABLE welcome offer ever recorded (the historical peak) for exactly this card:\n"
        f"  issuer: {product.issuer}\n  card: {product.product_name}\n\n"
        "Rules:\n"
        "- Public offers only — ignore targeted/incognito/referral-elevated highs.\n"
        "- The peak must be explicitly stated in the text; do not infer or use outside knowledge.\n"
        "- quote must be a short verbatim excerpt containing the number.\n"
        "- If the text names a different card variant (Business vs Personal, Plus vs Premier), found=false.\n"
        "- If nothing explicit, found=false.\n\n"
        f"RESEARCH TEXT:\n{research_text[:12000]}"
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
            output_format=PeakFinding,
        )
    except Exception:
        return None
    return resp.parsed_output


def research_missing_peaks(db: Session, *, limit: int | None = None) -> dict:
    """Resolve peak_offer_points for cards missing it. Returns a report.

    Candidate rotation (2026-07-16): products attempted within
    PEAK_RESEARCH_COOLDOWN_DAYS are skipped, never-attempted products go
    first, then the oldest attempts — so unresolvable cards stop burning LLM
    calls every refresh and later cards are no longer starved.
    """
    now = _utcnow()
    all_missing = list(
        db.scalars(
            select(models.CardProduct).where(models.CardProduct.peak_offer_points.is_(None))
        ).all()
    )
    candidates = []
    cooldown_skipped = 0
    for product in all_missing:
        attempted = product.peak_research_attempted_at
        if attempted is not None and (now - attempted).days < PEAK_RESEARCH_COOLDOWN_DAYS:
            cooldown_skipped += 1
            continue
        candidates.append(product)
    candidates.sort(
        key=lambda p: (
            p.peak_research_attempted_at is not None,
            p.peak_research_attempted_at or dt.datetime.min,
            p.id or 0,
        )
    )
    products = candidates[:limit] if limit else candidates

    filled: list[dict] = []
    skipped: list[dict] = []
    for product in products:
        plan = [q for q in research_resolver.build_research_plan(product, db) if q.query_type == "peak_history"]
        if not plan:
            skipped.append({"product": product.product_name, "reason": "no_query"})
            continue
        results, _stats, errors, _warnings = research_resolver.run_searches(plan, max_uses=2)
        text = ""
        for query in plan:
            hit = results.get(query.query)
            if hit:
                text += ("\n" + (hit.text or ""))
        if not text.strip():
            if not errors:
                # A real (non-outage) empty result still counts as an attempt;
                # a search outage must not burn the cooldown.
                product.peak_research_attempted_at = now
            skipped.append({"product": product.product_name, "reason": "no_research_text"})
            continue
        product.peak_research_attempted_at = now

        finding = _extract_peak(product, text)
        if not finding or not finding.found or not finding.peak_points:
            skipped.append({"product": product.product_name, "reason": "not_stated_in_sources"})
            continue
        quote = finding.quote or ""
        if not _number_in_quote(finding.peak_points, quote):
            skipped.append({"product": product.product_name, "reason": "quote_does_not_contain_number"})
            continue
        # Only the URL the extractor actually cites for the quote counts.
        # Falling back to the research pass's first source would attribute the
        # peak to a page that may never have stated it.
        source_url = finding.source_url
        if not source_url:
            skipped.append({"product": product.product_name, "reason": "no_cited_source_url"})
            continue
        if not _trusted_peak_source(source_url):
            skipped.append({"product": product.product_name, "reason": "untrusted_source", "source": source_url})
            continue
        current = product.current_offer_effective or product.current_offer_points or 0
        if current and finding.peak_points < current:
            skipped.append({"product": product.product_name, "reason": "below_current_offer"})
            continue
        if not (1000 <= finding.peak_points <= 500000):
            skipped.append({"product": product.product_name, "reason": "implausible"})
            continue

        ext = extract.OfferExtraction(
            found=True,
            # Raw model confidence — the deterministic trusted-source gates
            # above decide adoption, not an inflated confidence floor.
            confidence=finding.confidence,
            peak_offer_points=finding.peak_points,
            peak_offer_min_spend=finding.peak_min_spend,
            peak_offer_date=finding.seen_date,
            peak_offer_source=source_url,
            offer_status="public",
            evidence_snippets={
                "peak_offer_points": [f"src={source_url} :: {quote[:300]}"],
                "peak_bonus_amount": [quote[:300]],
            },
        )
        result = validate.apply_extraction(db, product, ext, source_url, commit=False)
        filled.append(
            {
                "product": product.product_name,
                "peak": finding.peak_points,
                "date": finding.seen_date,
                "source": source_url,
                "committed": result.get("committed"),
                "proposed": result.get("proposed"),
            }
        )
    db.commit()
    auto = validate.auto_resolve_pending_changes(db)
    return {
        "checked": len(products),
        "cooldown_skipped": cooldown_skipped,
        "filled": filled,
        "filled_count": len(filled),
        "skipped": skipped,
        "auto_review": {k: auto[k] for k in ("approved_count", "rejected_count", "pending_count")},
    }
