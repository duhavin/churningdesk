"""PUBLIC source quality helpers for product-specific card facts.

These checks are intentionally conservative. A broad roundup page can be useful
as a link hub, but it should not become the product's source of truth for
current offer, fee, spend, or benefit facts.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


ISSUER_DOMAINS = {
    "american express": "americanexpress.com",
    "amex": "americanexpress.com",
    "chase": "chase.com",
    "capital one": "capitalone.com",
    "citi": "citi.com",
    "citibank": "citi.com",
    "bank of america": "bankofamerica.com",
    "bofa": "bankofamerica.com",
    "wells fargo": "wellsfargo.com",
    "u.s. bank": "usbank.com",
    "us bank": "usbank.com",
    "bilt": "bilt.com",
    "delta": "delta.com",
    "marriott": "marriott.com",
}

IDENTITY_STOPWORDS = {
    "the",
    "card",
    "credit",
    "charge",
    "rewards",
    "reward",
    "from",
    "visa",
    "mastercard",
    "world",
    "elite",
    "signature",
    "american",
    "express",
    "capital",
    "one",
}

BROAD_SOURCE_HOSTS = {
    "doctorofcredit.com",
    "frequentmiler.com",
    "thepointsguy.com",
    "uscreditcardguide.com",
}

BROAD_PATH_MARKERS = (
    "/best",
    "best-current-credit-card",
    "best-credit-card",
    "best-card",
    "current-credit-card-sign",
    "current-offers",
    "credit-cards/best",
    "sign-up-bonuses",
    "signup-bonuses",
    "card-sign-bonuses",
)

COBRAND_CONFLICT_TERMS = {
    "delta",
    "skymiles",
    "marriott",
    "bonvoy",
    "hyatt",
    "hilton",
    "ihg",
    "united",
    "southwest",
    "jetblue",
    "alaska",
    "aadvantage",
    "wyndham",
    "bilt",
}

VARIANT_CONFLICT_GROUPS = (
    {"preferred", "reserve"},
    {"flex", "unlimited"},
    {"cash", "preferred", "unlimited", "premier"},
    {"gold", "platinum", "green", "blue"},
    {"bold", "boundless", "brilliant", "bevy", "bountiful"},
)


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(str(url).strip())
    if not parts.scheme or not parts.netloc:
        return str(url or "").strip()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def source_host(url: str | None) -> str:
    return (urlsplit(normalize_url(url)).hostname or "").lower()


def _tokens(value: str | None) -> set[str]:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return {token for token in text.split() if token}


def _meaningful_product_tokens(product_name: str | None) -> set[str]:
    return {token for token in _tokens(product_name) if token not in IDENTITY_STOPWORDS}


def expected_issuer_domain(issuer: str | None) -> str | None:
    low = re.sub(r"[^a-z0-9]+", " ", (issuer or "").lower()).strip()
    return ISSUER_DOMAINS.get(low)


def is_broad_source_url(url: str | None) -> bool:
    normalized = normalize_url(url).lower()
    if not normalized:
        return False
    host = source_host(normalized)
    if host.startswith("www."):
        host = host[4:]
    path = urlsplit(normalized).path.lower()
    if host in BROAD_SOURCE_HOSTS and any(marker in path for marker in BROAD_PATH_MARKERS):
        return True
    return False


def has_variant_conflict(issuer: str | None, product_name: str | None, text: str | None) -> bool:
    """Reject close-name or co-brand crossovers for product-specific fields."""
    haystack = _tokens(text)
    if not haystack:
        return False
    product_tokens = _tokens(" ".join(part for part in (issuer, product_name) if part))
    product_meaningful = _meaningful_product_tokens(product_name)

    product_is_business = "business" in product_tokens
    if "business" in haystack and not product_is_business:
        return True

    for term in COBRAND_CONFLICT_TERMS:
        if term in haystack and term not in product_tokens:
            return True

    for group in VARIANT_CONFLICT_GROUPS:
        product_hits = product_meaningful & group
        text_hits = haystack & group
        if product_hits and text_hits and product_hits.isdisjoint(text_hits):
            return True

    if "venture" in product_meaningful and "venture" in haystack:
        product_has_x = "x" in product_meaningful
        text_has_x = "x" in haystack
        if product_has_x != text_has_x:
            return True

    return False


def is_official_issuer_source(issuer: str | None, url: str | None) -> bool:
    host = source_host(url)
    domain = expected_issuer_domain(issuer)
    return bool(domain and (host == domain or host.endswith("." + domain)))


def source_supports_product(
    issuer: str | None,
    product_name: str | None,
    url: str | None,
    evidence_text: str | None = None,
) -> bool:
    normalized = normalize_url(url)
    if not normalized:
        return False
    if has_variant_conflict(issuer, product_name, f"{normalized} {evidence_text or ''}"):
        return False
    product_tokens = _meaningful_product_tokens(product_name)
    if not product_tokens:
        return False
    haystack = _tokens(f"{normalized} {evidence_text or ''}")
    overlap = len(product_tokens & haystack)
    required = 1 if len(product_tokens) == 1 else min(2, len(product_tokens))
    if overlap >= required:
        return True
    return is_official_issuer_source(issuer, normalized) and overlap >= 1


def is_safe_product_source(
    issuer: str | None,
    product_name: str | None,
    url: str | None,
    evidence_text: str | None = None,
) -> bool:
    if not source_supports_product(issuer, product_name, url, evidence_text):
        return False
    return not is_broad_source_url(url)


def is_verified_auto_adopt_source(
    issuer: str | None,
    product_name: str | None,
    url: str | None,
    evidence_text: str | None = None,
) -> bool:
    """True for current official product pages safe enough to auto-adopt facts."""
    return bool(
        is_official_issuer_source(issuer, url)
        and is_safe_product_source(issuer, product_name, url, evidence_text)
    )


def source_quality_issue(issuer: str | None, product_name: str | None, url: str | None) -> str | None:
    if not url:
        return "missing_source"
    if has_variant_conflict(issuer, product_name, url):
        return "source_identity_conflict"
    if is_broad_source_url(url):
        return "broad_source_not_product_truth"
    if not is_safe_product_source(issuer, product_name, url):
        return "source_not_product_specific"
    return None
