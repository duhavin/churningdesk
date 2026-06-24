"""Discovery orchestration: enumerate public card identities.

Discovery is static-first. It fetches trusted public source pages, parses card
titles/URLs from structured data and card-review listings, and only falls back
to the LLM if static discovery finds nothing.
"""
from __future__ import annotations

import html as html_std
import json
import re
from dataclasses import dataclass
from typing import Any

from lxml import html
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models
from ..logic.catalog import blacklisted_keys
from ..product_identity import (
    derive_product_family,
    product_variant_key,
    reward_currency_for_product,
    reward_tag_for_currency,
)
from . import extract, fetch
from .extract import IngestionUnavailable


@dataclass(slots=True)
class DiscoveryCandidate:
    issuer: str
    product_name: str
    source_url: str | None = None
    ownership: str = "Personal"
    account_type: str = "Credit Card"
    currency: str | None = None
    eligibility_tags: list[str] | None = None
    tag: str | None = None
    reports_to_personal_credit: bool = True
    notes: str | None = None
    confidence: float = 0.7
    added_by: str = "static_discovery"


ISSUER_ALIASES: dict[str, tuple[str, ...]] = {
    "American Express": ("american express", "amex"),
    "Bank of America": ("bank of america", "bofa", "bankamericard"),
    "Barclays": ("barclays", "barclaycard"),
    "Capital One": ("capital one",),
    "Chase": ("chase", "jpmorgan", "jp morgan"),
    "Citi": ("citi", "citibank", "aadvantage"),
    "U.S. Bank": ("u.s. bank", "us bank", "u s bank"),
    "Wells Fargo": ("wells fargo",),
    "United": ("united", "mileageplus"),
    "Delta": ("delta", "skymiles"),
    "American Airlines": ("american airlines", "aadvantage"),
    "Alaska Airlines": ("alaska airlines", "alaska mileage plan"),
    "Southwest": ("southwest", "rapid rewards"),
    "JetBlue": ("jetblue", "trueblue"),
    "Hawaiian Airlines": ("hawaiian airlines", "hawaiianmiles"),
    "Marriott": ("marriott", "bonvoy"),
    "Hilton": ("hilton", "honors"),
    "Hyatt": ("hyatt", "world of hyatt"),
    "IHG": ("ihg", "one rewards"),
    "Wyndham": ("wyndham",),
    "Choice": ("choice", "privileges"),
}

ISSUER_CANONICAL = {
    alias: issuer
    for issuer, aliases in ISSUER_ALIASES.items()
    for alias in aliases
}

CARD_TITLE_WORDS = (
    "card",
    "credit card",
    "charge card",
    "sapphire",
    "ink business",
    "freedom",
    "venture",
    "platinum",
    "gold",
    "preferred",
    "premier",
    "reserve",
    "bonvoy",
    "hilton honors",
    "world of hyatt",
    "aadvantage",
    "skymiles",
    "rapid rewards",
)

GENERIC_TITLE_PHRASES = (
    "a nearly-perfect starter card",
    "annual fee",
    "ask our experts",
    "best credit card",
    "helpful tools",
    "how to",
    "lounge access",
    "premium perks",
    "sign-up bonus",
    "welcome bonus",
    "what is the best",
    "why it's in my wallet",
)


def _existing_keys(db: Session) -> set[tuple[str, str]]:
    return {
        ((p.issuer or "").strip().lower(), _product_key(p.product_name or ""))
        for p in db.scalars(select(models.CardProduct)).all()
    }


def _existing_variants(db: Session) -> set[tuple[str, str]]:
    return {
        variant
        for p in db.scalars(select(models.CardProduct)).all()
        for variant in [product_variant_key(p.issuer, p.product_name)]
        if variant is not None
    }


def _normalize(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _requested_issuers(issuers: list[str]) -> set[str]:
    requested: set[str] = set()
    for issuer in issuers:
        normalized = _normalize(issuer)
        canonical = ISSUER_CANONICAL.get(normalized, issuer.strip())
        requested.add(canonical)
    return requested


def _issuer_matches(issuer: str, requested: set[str]) -> bool:
    if issuer in requested:
        return True
    issuer_aliases = set(ISSUER_ALIASES.get(issuer, (issuer.lower(),)))
    for req in requested:
        if issuer_aliases.intersection(ISSUER_ALIASES.get(req, (req.lower(),))):
            return True
    return False


def _issuer_from_text(text: str, requested: set[str]) -> str | None:
    normalized = f" {_normalize(text)} "
    for issuer in requested:
        for alias in ISSUER_ALIASES.get(issuer, (issuer.lower(),)):
            if f" {_normalize(alias)} " in normalized:
                return issuer
    for issuer, aliases in ISSUER_ALIASES.items():
        for alias in aliases:
            if f" {_normalize(alias)} " in normalized:
                return issuer
    return None


def _correct_discovered_issuer(
    issuer: str,
    product_name: str,
    source_url: str | None,
    notes: str | None,
    requested: set[str],
) -> str | None:
    """Correct obvious research grouping mistakes before inserting identity rows."""
    issuer = issuer.strip()
    product_low = _normalize(product_name)
    context = " ".join([product_name, source_url or "", notes or ""])
    context_norm = _normalize(context)
    major_issuers = {
        "Chase": ("chase",),
        "American Express": ("american express", "amex"),
        "Capital One": ("capital one",),
        "Citi": ("citi", "citibank"),
        "Bank of America": ("bank of america", "bofa"),
        "Wells Fargo": ("wells fargo",),
        "U.S. Bank": ("u s bank", "us bank", "u.s. bank"),
        "Barclays": ("barclays", "barclaycard"),
    }
    issuer_norm = _normalize(issuer)
    for canonical, aliases in major_issuers.items():
        if issuer == canonical:
            continue
        if any(f" {_normalize(alias)} " in f" {context_norm} " for alias in aliases):
            # Keep co-brand/program issuers when the product name actually
            # contains that program (World of Hyatt, Marriott Bonvoy, etc.).
            if issuer_norm and issuer_norm in product_low:
                return issuer
            return canonical
    inferred = _issuer_from_text(context, requested)
    if inferred and (not issuer or not _issuer_matches(issuer, requested)):
        return inferred
    return issuer if _issuer_matches(issuer, requested) else None


def _clean_product_name(title: str) -> str | None:
    title = html_std.unescape(re.sub(r"\s+", " ", title)).strip()
    if not title:
        return None
    low_original = title.lower()
    if re.match(r"^\d{1,2}\.\d{1,2}\s+", title):
        return None
    if any(phrase in low_original for phrase in GENERIC_TITLE_PHRASES):
        return None
    if any(term in low_original for term in ("credit union", "discontinued", "expired", "removed")):
        return None
    title = re.sub(r"\s+[-|]\s+.*$", "", title)
    title = re.sub(r"\s+Review\b.*$", "", title, flags=re.I)
    title = re.sub(r"\s+\[\d{4}\.\d+\s+Update\].*$", "", title, flags=re.I)
    title = re.sub(r"\s+\(\d{4}\.\d+\s+Update:.*?\)", "", title, flags=re.I)
    title = re.sub(r"\s+\(Formerly .*?\)", "", title, flags=re.I)
    title = re.sub(r"\s+\([A-Z0-9]{2,6}\)", "", title)
    title = re.sub(r"\s+\(.*?Offer.*?\)", "", title, flags=re.I)
    title = re.sub(r"\s+\(.*?Update.*?\)", "", title, flags=re.I)
    title = title.strip(" -:|")
    if len(title) < 8:
        return None
    low = title.lower()
    if "bank account" in low or "checking" in low or "savings" in low:
        return None
    if not any(word in low for word in CARD_TITLE_WORDS):
        return None
    return title


def _infer_ownership(text: str) -> str:
    return "Business" if "business" in text.lower() else "Personal"


def _infer_rewards(product_name: str, issuer: str) -> tuple[str | None, str | None]:
    currency = reward_currency_for_product(issuer, product_name)
    return currency, reward_tag_for_currency(currency)


def _candidate_from_title(
    *,
    title: str,
    source_url: str | None,
    context: str,
    requested: set[str],
    brand: str | None = None,
) -> DiscoveryCandidate | None:
    product_name = _clean_product_name(title)
    if not product_name:
        return None

    issuer = None
    if brand:
        issuer = _issuer_from_text(brand, requested)
    issuer = issuer or _issuer_from_text(" ".join([title, source_url or ""]), requested)
    if not issuer or not _issuer_matches(issuer, requested):
        return None

    ownership = _infer_ownership(" ".join([title, source_url or "", context]))
    currency, tag = _infer_rewards(product_name, issuer)
    return DiscoveryCandidate(
        issuer=issuer,
        product_name=product_name,
        source_url=source_url,
        ownership=ownership,
        currency=currency,
        tag=tag,
        reports_to_personal_credit=ownership != "Business",
        confidence=0.85 if source_url else 0.65,
    )


def _dedupe(candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    out: dict[tuple[str, str], DiscoveryCandidate] = {}
    for candidate in candidates:
        key = product_variant_key(candidate.issuer, candidate.product_name) or (
            candidate.issuer.strip().lower(),
            _product_key(candidate.product_name),
        )
        old = out.get(key)
        if not old or candidate.confidence > old.confidence:
            out[key] = candidate
    return sorted(out.values(), key=lambda c: (c.issuer, c.product_name))


def _product_key(product_name: str) -> str:
    key = _normalize(product_name)
    key = re.sub(r"\b(credit|card|rewards|reward|visa|signature|world|elite)\b", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _extract_next_data(doc, requested: set[str]) -> list[DiscoveryCandidate]:
    candidates: list[DiscoveryCandidate] = []
    for raw in doc.xpath("//script[@id='__NEXT_DATA__']/text()"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _walk_dicts(payload):
            title = item.get("title") or item.get("name")
            brand = item.get("brand") or item.get("issuer")
            if not isinstance(title, str):
                continue
            source_url = item.get("pdpUrl") or item.get("reviewLink") or item.get("url")
            if source_url is not None and not isinstance(source_url, str):
                source_url = None
            if not isinstance(brand, str) and not source_url:
                continue
            candidate = _candidate_from_title(
                title=title,
                source_url=source_url,
                context=json.dumps(item, ensure_ascii=True)[:3000],
                requested=requested,
                brand=brand if isinstance(brand, str) else None,
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def _extract_wordpress_reviews(doc, requested: set[str]) -> list[DiscoveryCandidate]:
    candidates: list[DiscoveryCandidate] = []
    anchors = doc.xpath("//h1/a|//h2/a|//h3/a|//article//a")
    for anchor in anchors:
        title = anchor.text_content().strip()
        if "review" not in title.lower() and "credit card" not in title.lower():
            continue
        source_url = anchor.get("href")
        ancestor_text = ""
        for ancestor in anchor.xpath("ancestor::*[contains(@class, 'post')][1]"):
            ancestor_text = ancestor.text_content()
        candidate = _candidate_from_title(
            title=title,
            source_url=source_url,
            context=ancestor_text,
            requested=requested,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def discover_static_cards(issuers: list[str], source_urls: list[str] | None = None) -> tuple[list[DiscoveryCandidate], dict]:
    requested = _requested_issuers(issuers)
    urls = source_urls or config.DEFAULT_SOURCES
    pages = fetch.fetch_many_pages(urls)
    candidates: list[DiscoveryCandidate] = []
    for page in pages.values():
        try:
            doc = html.fromstring(page.html)
        except (ValueError, html.ParserError):
            continue
        candidates.extend(_extract_next_data(doc, requested))
        candidates.extend(_extract_wordpress_reviews(doc, requested))
    return _dedupe(candidates), {
        "mode": "static_http_source_discovery",
        "sources_requested": len(urls),
        "sources_fetched": len(pages),
        "cache_hits": sum(1 for page in pages.values() if page.from_cache),
    }


def _llm_discovery(issuers: list[str]) -> tuple[list[DiscoveryCandidate], str | None]:
    try:
        cards = extract.discover_cards(issuers)
    except IngestionUnavailable as exc:
        return [], str(exc)

    candidates = []
    requested = _requested_issuers(issuers)
    for card in cards:
        issuer = _correct_discovered_issuer(
            card.issuer,
            card.product_name,
            card.source_url,
            card.notes,
            requested,
        )
        if not issuer:
            continue
        currency, tag = _infer_rewards(card.product_name, issuer)
        candidates.append(
            DiscoveryCandidate(
                issuer=issuer,
                product_name=card.product_name.strip(),
                ownership=card.ownership or "Personal",
                account_type=card.account_type or "Credit Card",
                currency=card.currency or currency,
                source_url=card.source_url,
                eligibility_tags=card.eligibility_tags,
                tag=card.tag or tag,
                reports_to_personal_credit=(
                    card.reports_to_personal_credit
                    if card.reports_to_personal_credit is not None
                    else (card.ownership or "Personal").lower() != "business"
                ),
                notes=card.notes,
                confidence=0.6,
                added_by="llm_discovery",
            )
        )
    return _dedupe(candidates), None


def _web_research_discovery(issuers: list[str]) -> tuple[list[DiscoveryCandidate], str | None, dict | None]:
    try:
        research = extract.research_card_universe(issuers)
        source_urls = [source["url"] for source in research.get("sources", []) if source.get("url")]
        cards = extract.discover_cards_from_research(issuers, research["text"], source_urls)
    except IngestionUnavailable as exc:
        return [], str(exc), None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}", None

    candidates = []
    requested = _requested_issuers(issuers)
    for card in cards:
        issuer = _correct_discovered_issuer(
            card.issuer,
            card.product_name,
            card.source_url,
            card.notes,
            requested,
        )
        if not issuer:
            continue
        currency, tag = _infer_rewards(card.product_name, issuer)
        candidates.append(
            DiscoveryCandidate(
                issuer=issuer,
                product_name=card.product_name.strip(),
                ownership=card.ownership or "Personal",
                account_type=card.account_type or "Credit Card",
                currency=card.currency or currency,
                source_url=card.source_url or (source_urls[0] if source_urls else None),
                eligibility_tags=card.eligibility_tags,
                tag=card.tag or tag,
                reports_to_personal_credit=(
                    card.reports_to_personal_credit
                    if card.reports_to_personal_credit is not None
                    else (card.ownership or "Personal").lower() != "business"
                ),
                notes=card.notes,
                confidence=0.78,
                added_by="llm_discovery",
            )
        )
    return _dedupe(candidates), None, {
        "web_search_requests": research.get("web_search_requests", 0),
        "sources": source_urls,
    }


def run_discovery(db: Session, issuers: list[str] | None = None) -> dict:
    issuers = issuers or config.DISCOVERY_ISSUERS
    from .schedule import active_source_urls

    discovered, scan = discover_static_cards(issuers, active_source_urls(db))
    llm_error = None
    web_error = None
    web_scan = None
    discovery_mode = "static"

    if config.WEB_SEARCH_ENABLED and config.llm_available():
        researched, web_error, web_scan = _web_research_discovery(issuers)
        if researched:
            discovered = _dedupe([*discovered, *researched])
            discovery_mode = "static_plus_web_research" if scan.get("sources_fetched") else "web_research"

    if not discovered and config.llm_available():
        discovered, llm_error = _llm_discovery(issuers)
        discovery_mode = "llm_fallback"

    blacklist = {
        (issuer, _product_key(product_name))
        for issuer, product_name in blacklisted_keys(db)
    }
    existing = _existing_keys(db)
    existing_variants = _existing_variants(db)

    added: list[dict] = []
    skipped_blacklist = 0
    for candidate in discovered:
        key = (candidate.issuer.strip().lower(), _product_key(candidate.product_name))
        if key in blacklist:
            skipped_blacklist += 1
            continue
        variant = product_variant_key(candidate.issuer, candidate.product_name)
        if key in existing or (variant is not None and variant in existing_variants):
            continue
        currency = candidate.currency or reward_currency_for_product(candidate.issuer, candidate.product_name)
        product = models.CardProduct(
            issuer=candidate.issuer.strip(),
            product_name=candidate.product_name.strip(),
            product_family=derive_product_family(candidate.issuer, candidate.product_name),
            ownership=candidate.ownership or "Personal",
            account_type=candidate.account_type or "Credit Card",
            currency=currency,
            eligibility_tags=candidate.eligibility_tags,
            tag=candidate.tag or reward_tag_for_currency(currency),
            reports_to_personal_credit=candidate.reports_to_personal_credit,
            added_by=candidate.added_by,
            discovery_reviewed=False,
            source_url=candidate.source_url,
            notes=candidate.notes,
        )
        db.add(product)
        existing.add(key)
        if variant is not None:
            existing_variants.add(variant)
        added.append(
            {
                "issuer": candidate.issuer,
                "product_name": candidate.product_name,
                "source_url": candidate.source_url,
                "confidence": candidate.confidence,
            }
        )

    db.commit()
    return {
        "requested_issuers": issuers,
        "discovery_mode": discovery_mode,
        "scan": scan,
        "web_scan": web_scan,
        "discovered_count": len(discovered),
        "added_count": len(added),
        "added": added,
        "skipped_blacklisted": skipped_blacklist,
        "llm_error": llm_error,
        "web_error": web_error,
    }
