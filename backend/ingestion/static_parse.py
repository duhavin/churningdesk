"""Static HTML parsing and deterministic offer extraction.

This module turns raw fetched pages into compact snippets before any LLM call.
It prefers structured/static signals: title/meta, JSON-LD, __NEXT_DATA__,
tables, and nearby offer/legal text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import trafilatura
from lxml import html

from .fetch import FetchedPage
from .extract import OfferScanRow

SNIPPET_CHARS = 700
MAX_TEXT_LINES = 900
STOPWORDS = {
    "a",
    "an",
    "and",
    "card",
    "cards",
    "credit",
    "for",
    "from",
    "of",
    "preferred",
    "rewards",
    "the",
    "to",
    "visa",
    "world",
}
ISSUER_WORDS = {
    "american",
    "express",
    "amex",
    "bank",
    "capital",
    "one",
    "chase",
    "citi",
    "citibank",
    "wells",
    "fargo",
    "barclays",
    "us",
    "u",
    "s",
}
OFFER_KEYWORDS = (
    "welcome",
    "bonus",
    "offer",
    "earn",
    "spend",
    "annual fee",
    "points",
    "miles",
    "cash back",
    "statement credit",
    "dining",
    "restaurant",
    "restaurants",
    "grocery",
    "groceries",
    "supermarket",
    "travel",
    "airfare",
    "flights",
    "hotel",
    "hotels",
    "gas",
    "lounge",
    "priority pass",
    "global entry",
    "tsa precheck",
    "checked bag",
    "companion",
    "free night",
    "elite status",
    "uber",
    "uber cash",
    "doordash",
    "instacart",
    "resy",
    "clear",
    "travel credit",
    "dining credit",
    "airline fee credit",
    "entertainment credit",
    "anniversary bonus",
    "anniversary miles",
    "anniversary points",
    "cardmember anniversary",
    "eligible",
    "eligibility",
    "not available",
    "expires",
    "expiration",
    "limited time",
    "business",
)

PEAK_TERMS = (
    "highest ever",
    "highest-ever",
    "all time high",
    "all-time high",
    "all time best",
    "all-time best",
    "best ever",
    "best-ever",
    "record high",
    "peak offer",
    "historical high",
)


@dataclass(slots=True)
class ParsedPage:
    url: str
    final_url: str
    fetched_at: str
    content_hash: str
    title: str | None
    meta_description: str | None
    clean_text: str
    lines: list[str]
    json_ld_snippets: list[str]
    next_data_snippets: list[str]
    table_snippets: list[str]
    cache_status: str


def _compact(text: str | None, limit: int = SNIPPET_CHARS) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _normalize(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _product_tokens(product_name: str) -> set[str]:
    tokens = set(_normalize(product_name).split())
    return {t for t in tokens if len(t) > 2 and t not in STOPWORDS}


def _distinct_product_tokens(product_name: str) -> set[str]:
    return {t for t in _product_tokens(product_name) if t not in ISSUER_WORDS}


def _product_aliases(issuer: str, product_name: str) -> list[str]:
    normalized = _normalize(product_name)
    issuer_norm = _normalize(issuer)
    aliases = [normalized]
    if issuer_norm and normalized.startswith(issuer_norm):
        aliases.append(normalized[len(issuer_norm):].strip())
    aliases.append(re.sub(r"\b(credit|card|rewards|reward|visa|signature|world|elite)\b", " ", normalized))
    aliases = [re.sub(r"\s+", " ", alias).strip() for alias in aliases]
    out: list[str] = []
    for alias in aliases:
        if not alias or alias in out:
            continue
        token_count = len([t for t in alias.split() if t not in STOPWORDS and t not in ISSUER_WORDS])
        if token_count >= 2 or len(alias) >= 12:
            out.append(alias)
    return out


def _has_conflicting_variant(normalized_text: str, product_name: str) -> bool:
    product_norm = _normalize(product_name)
    conflicts = ("business", "preferred", "reserve", "platinum", "gold", "green", "premier", "priority", "elite")
    for term in conflicts:
        if term in normalized_text.split() and term not in product_norm.split():
            # "Business" is the dangerous one on broad pages; the others help
            # prevent Reserve/Preferred/Premier-style cross-card leakage.
            return True
    return False


def _context_matches_product(
    text: str | None,
    issuer: str,
    product_name: str,
    *,
    assume_product_page: bool = False,
) -> bool:
    if assume_product_page:
        return True
    normalized = _normalize(text)
    if not normalized:
        return False
    aliases = _product_aliases(issuer, product_name)
    if any(alias and alias in normalized for alias in aliases):
        return True
    tokens = _distinct_product_tokens(product_name)
    hits = len(tokens.intersection(normalized.split()))
    if hits < max(2, min(3, len(tokens))):
        return False
    return not _has_conflicting_variant(normalized, product_name)


def _safe_json_snippet(raw: str) -> str:
    raw = raw.strip()
    try:
        value = json.loads(raw)
        return _compact(json.dumps(value, sort_keys=True), 1200)
    except json.JSONDecodeError:
        return _compact(raw, 1200)


def _interesting(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in OFFER_KEYWORDS)


def parse_page(page: FetchedPage) -> ParsedPage:
    try:
        doc = html.fromstring(page.html)
    except (ValueError, html.ParserError):
        doc = None

    title = None
    meta_description = None
    json_ld_snippets: list[str] = []
    next_data_snippets: list[str] = []
    table_snippets: list[str] = []

    if doc is not None:
        titles = doc.xpath("//title/text()")
        title = _compact(titles[0], 300) if titles else None

        metas = doc.xpath(
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='description']/@content"
            " | //meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='og:description']/@content"
        )
        meta_description = _compact(metas[0], 500) if metas else None

        for script_text in doc.xpath("//script[@type='application/ld+json']/text()")[:8]:
            snippet = _safe_json_snippet(script_text)
            if snippet and (_interesting(snippet) or len(json_ld_snippets) < 2):
                json_ld_snippets.append(snippet)

        for script_text in doc.xpath("//script[@id='__NEXT_DATA__']/text()")[:2]:
            snippet = _safe_json_snippet(script_text)
            if snippet:
                next_data_snippets.append(snippet)

        for table in doc.xpath("//table")[:12]:
            rows: list[str] = []
            for tr in table.xpath(".//tr")[:16]:
                cells = [_compact(" ".join(c.itertext()), 160) for c in tr.xpath("./th|./td")]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(" | ".join(cells))
            table_text = _compact(" / ".join(rows), 900)
            if table_text and _interesting(table_text):
                table_snippets.append(table_text)

    clean_text = trafilatura.extract(page.html, include_comments=False, include_tables=True) or ""
    if not clean_text and doc is not None:
        clean_text = doc.text_content()
    lines = [_compact(line, 500) for line in clean_text.splitlines()]
    lines = [line for line in lines if line][:MAX_TEXT_LINES]

    return ParsedPage(
        url=page.url,
        final_url=page.final_url,
        fetched_at=page.fetched_at,
        content_hash=page.content_hash,
        title=title,
        meta_description=meta_description,
        clean_text=clean_text,
        lines=lines,
        json_ld_snippets=json_ld_snippets,
        next_data_snippets=next_data_snippets,
        table_snippets=table_snippets,
        cache_status=page.cache_status,
    )


def source_priority(page: ParsedPage, issuer: str, assume_product_page: bool = False) -> int:
    if assume_product_page:
        return 1
    host = (urlparse(page.final_url or page.url).hostname or "").lower()
    issuer_key = _normalize(issuer).replace(" ", "")
    official_markers = {
        "americanexpress": ("americanexpress.com", "amex"),
        "bankofamerica": ("bankofamerica.com",),
        "barclays": ("barclaycardus.com", "barclaysus.com"),
        "capitalone": ("capitalone.com",),
        "chase": ("chase.com",),
        "citi": ("citi.com",),
        "usbank": ("usbank.com",),
        "wellsfargo": ("wellsfargo.com",),
    }
    if any(marker in host for marker in official_markers.get(issuer_key, ())):
        return 1
    if any(
        marker in host
        for marker in (
            "doctorofcredit.com",
            "frequentmiler.com",
            "thepointsguy.com",
            "uscreditcardguide.com",
        )
    ):
        return 2
    return 3


def snippets_for_card(
    page: ParsedPage,
    issuer: str,
    product_name: str,
    *,
    assume_product_page: bool = False,
    max_snippets: int = 10,
) -> list[str]:
    normalized_product = _normalize(product_name)
    tokens = _product_tokens(product_name)
    snippets: list[str] = []
    seen: set[str] = set()

    def add(label: str, text: str | None) -> None:
        snippet = _compact(text, SNIPPET_CHARS)
        if not snippet:
            return
        if not _context_matches_product(snippet, issuer, product_name, assume_product_page=assume_product_page):
            return
        key = _normalize(snippet)
        if not key or key in seen:
            return
        seen.add(key)
        snippets.append(f"[{label}] {snippet}")

    for label, value in (("title", page.title), ("meta", page.meta_description)):
        if assume_product_page or normalized_product in _normalize(value) or _context_matches_product(value, issuer, product_name):
            add(label, value)

    for label, values in (
        ("json-ld", page.json_ld_snippets),
        ("next-data", page.next_data_snippets),
        ("table", page.table_snippets),
    ):
        for value in values:
            normalized_value = _normalize(value)
            token_hits = len(tokens.intersection(normalized_value.split()))
            if assume_product_page or normalized_product in normalized_value or token_hits >= 2:
                add(label, value)

    scored: list[tuple[int, int]] = []
    for idx, line in enumerate(page.lines):
        normalized = _normalize(line)
        words = set(normalized.split())
        token_hits = len(tokens.intersection(words))
        score = 0
        if normalized_product and normalized_product in normalized:
            score += 10
        score += min(token_hits, 4) * 2
        if _normalize(issuer) in normalized:
            score += 2
        if _interesting(line):
            score += 3
        if assume_product_page and _interesting(line):
            score += 4
        if re.search(r"\d+(?:\.\d+)?\s+(?:x|points?|miles?|%)\b.{0,90}\b(?:per\s+\$?1|per\s+dollar|cash back|on|at|for|in)\b", line, re.I):
            score += 8
        if score >= 4:
            scored.append((score, idx))

    for _, idx in sorted(scored, reverse=True)[:max_snippets]:
        window = " ".join(page.lines[max(0, idx - 1) : min(len(page.lines), idx + 2)])
        add("text", window)
        if len(snippets) >= max_snippets:
            break

    if not snippets and assume_product_page:
        for line in page.lines:
            if _interesting(line):
                add("text", line)
            if len(snippets) >= max_snippets:
                break

    return snippets[:max_snippets]


def _sentences(snippets: list[str]) -> list[str]:
    text = " ".join(snippets)
    text = re.sub(r"\bU\.S\.", "US", text)
    chunks = re.split(r"(?<=[.!?])\s+| / |\n+", text)
    return [_compact(c, 500) for c in chunks if c and len(c.strip()) > 8]


def _number(value: str) -> float:
    return float(value.replace(",", "").replace("$", ""))


def _window_months(value: str, unit: str) -> int:
    n = int(value)
    if unit.lower().startswith("day"):
        return max(1, round(n / 30))
    return n


def _field(evidence: dict[str, list[str]], field: str, sentence: str) -> None:
    evidence.setdefault(field, [])
    compact = _compact(sentence, 350)
    if compact and compact not in evidence[field]:
        evidence[field].append(compact)


def _extract_bonus(sentences: list[str], evidence: dict[str, list[str]]) -> tuple[float | None, str | None]:
    point_units = r"points?|miles?|bonus miles?|bonus points?|skymiles|aadvantage miles|avios"
    for sentence in sentences:
        low = sentence.lower()
        if not any(k in low for k in ("welcome", "bonus", "earn", "offer", "cash back", "statement credit")):
            continue
        if any(term in low for term in PEAK_TERMS):
            continue
        cash = re.search(r"\$([\d,]+(?:\.\d+)?)\s*(?:cash back|statement credit|bonus|welcome)", sentence, re.I)
        if cash:
            _field(evidence, "bonus_amount", sentence)
            _field(evidence, "bonus_unit", sentence)
            return _number(cash.group(1)), "cash back"
        points = re.search(rf"(\d{{1,3}}(?:,\d{{3}})+|\d{{4,6}})\s+({point_units})", sentence, re.I)
        if points:
            _field(evidence, "bonus_amount", sentence)
            _field(evidence, "bonus_unit", sentence)
            return _number(points.group(1)), points.group(2).lower()
    return None, None


def _extract_spend(sentences: list[str], evidence: dict[str, list[str]]) -> tuple[float | None, int | None]:
    patterns = (
        r"spend(?:ing)?\s+\$?([\d,]+(?:\.\d+)?).{0,100}?(?:first|within|in|during|over).{0,40}?(\d+)\s*(months?|days?)",
        r"after\s+you\s+spend\s+\$?([\d,]+(?:\.\d+)?).{0,100}?(\d+)\s*(months?|days?)",
    )
    for sentence in sentences:
        if "spend" not in sentence.lower():
            continue
        for pattern in patterns:
            match = re.search(pattern, sentence, re.I)
            if match:
                _field(evidence, "spend_requirement", sentence)
                _field(evidence, "spend_window_months", sentence)
                return _number(match.group(1)), _window_months(match.group(2), match.group(3))
    return None, None


def _extract_peak(sentences: list[str], evidence: dict[str, list[str]]) -> tuple[float | None, str | None]:
    point_units = r"points?|miles?|bonus miles?|bonus points?|skymiles|aadvantage miles|avios"
    for sentence in sentences:
        low = sentence.lower()
        if not any(term in low for term in PEAK_TERMS):
            continue
        if any(term in low for term in ("targeted", "incognito", "prequalified", "phone offer", "mail offer", "as high as")):
            continue
        points = re.search(rf"(\d{{1,3}}(?:,\d{{3}})+|\d{{4,6}})\s+({point_units})", sentence, re.I)
        if points:
            _field(evidence, "peak_bonus_amount", sentence)
            _field(evidence, "peak_bonus_unit", sentence)
            _field(evidence, "peak_offer_source", sentence)
            return _number(points.group(1)), points.group(2).lower()
    return None, None


def _extract_annual_fee(sentences: list[str], evidence: dict[str, list[str]]) -> float | None:
    for sentence in sentences:
        low = sentence.lower()
        if "annual fee" not in low:
            continue
        if "no annual fee" in low or "$0 annual fee" in low:
            _field(evidence, "annual_fee", sentence)
            return 0.0
        if "fee credit" in low:
            continue
        match = re.search(r"\$([\d,]+(?:\.\d+)?)\s+(?:annual\s+)?fee|annual fee(?: is|:| of)?\s+\$?([\d,]+(?:\.\d+)?)", sentence, re.I)
        if match:
            _field(evidence, "annual_fee", sentence)
            return _number(match.group(1) or match.group(2))
    return None


def _extract_expiration(sentences: list[str], evidence: dict[str, list[str]]) -> str | None:
    for sentence in sentences:
        if re.search(r"\b(expires|expiration|offer ends|limited time|through|valid until)\b", sentence, re.I):
            _field(evidence, "offer_expiration", sentence)
            return sentence
    return None


def _extract_eligibility(sentences: list[str], evidence: dict[str, list[str]]) -> str | None:
    hits: list[str] = []
    for sentence in sentences:
        if re.search(r"\b(eligible|eligibility|not available|previous|lifetime|48 months|24 months|5/24|bonus.*not)\b", sentence, re.I):
            hits.append(sentence)
            _field(evidence, "eligibility_language", sentence)
    return " ".join(hits[:3]) if hits else None


_CATEGORY_ALIASES = {
    "dining": ("dining", "restaurant", "restaurants"),
    "groceries": ("grocery", "groceries", "supermarket", "supermarkets"),
    "travel": ("travel", "airfare", "flights", "hotel", "hotels", "rental car"),
    "gas": ("gas", "gas station", "gas stations", "fuel"),
    "everyday": ("everyday", "every purchase", "all purchases", "all other", "everything else", "other purchases"),
}


def _category_for(text: str) -> str | None:
    categories = _categories_for(text)
    return categories[0] if categories else None


def _categories_for(text: str) -> list[str]:
    low = text.lower()
    categories: list[str] = []
    for category, aliases in _CATEGORY_ALIASES.items():
        if any(alias in low for alias in aliases):
            categories.append(category)
    return categories


def _extract_earn_multipliers(sentences: list[str], evidence: dict[str, list[str]]) -> dict[str, float] | None:
    out: dict[str, float] = {}
    category_tail = r"([^;|]{0,120}?)(?=\s+\d+(?:\.\d+)?\s*(?:x|points?|miles?|%)\b|[;|]|$)"

    def record(category_text: str, raw_value: str, sentence: str) -> None:
        categories = _categories_for(category_text)
        if not categories:
            return
        value = float(raw_value)
        for category in categories:
            if value > out.get(category, 0):
                out[category] = value
                _field(evidence, "earn_multipliers", sentence)

    for sentence in sentences:
        low = sentence.lower()
        if not any(k in low for k in ("x", "points", "miles", "cash back", "earn", "% back")):
            continue
        for match in re.finditer(rf"(\d+(?:\.\d+)?)\s*x\s*(?:points?|miles?)?(?:\s+(?:on|at|for|in))?\s+{category_tail}", sentence, re.I):
            record(match.group(2), match.group(1), sentence)
        for match in re.finditer(rf"earn\s+(\d+(?:\.\d+)?)\s*(?:x|points?|miles?).{{0,80}}?\b(?:on|at|for|in)\s+{category_tail}", sentence, re.I):
            record(match.group(2), match.group(1), sentence)
        for match in re.finditer(rf"(\d+(?:\.\d+)?)\s+(?:points?|miles?)\s+per\s+\$?1.{{0,80}}?\b(?:on|at|for|in)\s+{category_tail}", sentence, re.I):
            record(match.group(2), match.group(1), sentence)
        for match in re.finditer(rf"(\d+(?:\.\d+)?)\s+(?:points?|miles?)\s+per\s+dollar.{{0,80}}?\b(?:on|at|for|in)\s+{category_tail}", sentence, re.I):
            record(match.group(2), match.group(1), sentence)
        for match in re.finditer(rf"(\d+(?:\.\d+)?)%\s+(?:cash back|back).{{0,60}}?\b(?:on|at|for|in)\s+{category_tail}", sentence, re.I):
            record(match.group(2), match.group(1), sentence)
    return out or None


def _best_category_uses(multipliers: dict[str, float] | None) -> dict[str, str] | None:
    if not multipliers:
        return None
    return {category: f"{value:g}x" for category, value in sorted(multipliers.items())}


def _extract_benefits(sentences: list[str], evidence: dict[str, list[str]]) -> list[str] | None:
    hits: list[str] = []
    benefit_terms = (
        "credit",
        "lounge",
        "airport lounge",
        "priority pass",
        "global entry",
        "tsa precheck",
        "tsa pre",
        "clear",
        "checked bag",
        "free checked bag",
        "companion",
        "companion pass",
        "free night",
        "anniversary",
        "elite status",
        "priority boarding",
        "cell phone",
        "travel protection",
        "purchase protection",
        "trip delay",
        "trip cancellation",
        "rental car",
        "uber",
        "uber cash",
        "doordash",
        "instacart",
        "resy",
        "travel credit",
        "dining credit",
        "airline fee credit",
        "entertainment credit",
        "anniversary bonus",
        "anniversary miles",
        "anniversary points",
        "cardmember anniversary",
        "resort credit",
        "hotel credit",
    )
    for sentence in sentences:
        low = sentence.lower()
        if not any(term in low for term in benefit_terms):
            continue
        compact = _compact(sentence, 180)
        if compact and compact not in hits:
            hits.append(compact)
            _field(evidence, "card_benefits", sentence)
        if len(hits) >= 8:
            break
    return hits or None


def _extract_downgrade_paths(sentences: list[str], evidence: dict[str, list[str]]) -> list[str] | None:
    hits: list[str] = []
    for sentence in sentences:
        if not re.search(r"\b(downgrade|product change|convert)\b", sentence, re.I):
            continue
        compact = _compact(sentence, 180)
        if compact and compact not in hits:
            hits.append(compact)
            _field(evidence, "downgrade_paths", sentence)
    return hits[:5] or None


def _product_found(snippets: list[str], issuer: str, product_name: str, assume_product_page: bool) -> bool:
    if assume_product_page:
        return True
    joined = _normalize(" ".join(snippets))
    normalized_product = _normalize(product_name)
    if normalized_product and normalized_product in joined:
        return True
    if _context_matches_product(" ".join(snippets), issuer, product_name):
        return True
    tokens = _distinct_product_tokens(product_name)
    hits = len(tokens.intersection(joined.split()))
    return hits >= max(2, min(3, len(tokens)))


def deterministic_offer_row(
    *,
    page: ParsedPage,
    issuer: str,
    product_name: str,
    ownership: str | None = None,
    snippets: list[str],
    assume_product_page: bool = False,
) -> OfferScanRow:
    evidence: dict[str, list[str]] = {}
    sentences = _sentences(snippets)
    bonus_amount, bonus_unit = _extract_bonus(sentences, evidence)
    spend_requirement, spend_window_months = _extract_spend(sentences, evidence)
    peak_bonus_amount, peak_bonus_unit = _extract_peak(sentences, evidence)
    annual_fee = _extract_annual_fee(sentences, evidence)
    offer_expiration = _extract_expiration(sentences, evidence)
    eligibility_language = _extract_eligibility(sentences, evidence)
    earn_multipliers = _extract_earn_multipliers(sentences, evidence)
    card_benefits = _extract_benefits(sentences, evidence)
    downgrade_paths = _extract_downgrade_paths(sentences, evidence)

    joined_low = " ".join(sentences).lower()
    is_targeted = any(k in joined_low for k in ("targeted", "invitation only", "selected to apply", "pre-selected"))
    affiliate_terms = (
        "affiliate link",
        "affiliate links",
        "affiliate commission",
        "we may earn",
        "referral link",
        "referral links",
    )
    is_affiliate = not assume_product_page and any(k in joined_low for k in affiliate_terms)
    is_expired = any(
        k in joined_low
        for k in (
            "offer expired",
            "expired offer",
            "offer has ended",
            "offer ended",
            "no longer available",
            "no longer accepting applications",
        )
    )
    is_business = (ownership or "").lower() == "business" or "business" in product_name.lower()
    product_found = _product_found(snippets, issuer, product_name, assume_product_page)

    if is_expired:
        offer_status = "expired"
    elif is_targeted:
        offer_status = "targeted"
    elif is_affiliate:
        offer_status = "affiliate"
    elif bonus_amount is not None:
        offer_status = "public"
    else:
        offer_status = "unknown"

    confidence = 0.25
    if product_found:
        confidence += 0.2
    if bonus_amount is not None:
        confidence += 0.25
    if spend_requirement is not None:
        confidence += 0.12
    if annual_fee is not None:
        confidence += 0.08
    has_supplemental_facts = bool(earn_multipliers or card_benefits or downgrade_paths)
    if earn_multipliers:
        confidence += 0.12
    if card_benefits:
        confidence += 0.1
    if downgrade_paths:
        confidence += 0.04
    if source_priority(page, issuer, assume_product_page) == 1:
        confidence += 0.08
        if assume_product_page and has_supplemental_facts:
            confidence += 0.12
    if offer_status != "public" and not (earn_multipliers or card_benefits or downgrade_paths):
        confidence -= 0.1
    confidence = max(0.0, min(0.92, confidence))

    needs_review_reason = None
    if not product_found:
        needs_review_reason = "product_not_matched_in_static_snippets"
    elif bonus_amount is None:
        needs_review_reason = "bonus_not_found_deterministically"
    elif offer_status != "public":
        needs_review_reason = f"offer_status_{offer_status}"
    elif confidence < 0.78:
        needs_review_reason = "low_static_confidence"

    return OfferScanRow(
        issuer=issuer,
        card_name=product_name,
        product_url=page.final_url or page.url,
        source_url=page.final_url or page.url,
        fetched_at=page.fetched_at,
        content_hash=page.content_hash,
        bonus_amount=bonus_amount,
        bonus_unit=bonus_unit,
        spend_requirement=spend_requirement,
        spend_window_months=spend_window_months,
        peak_bonus_amount=peak_bonus_amount,
        peak_bonus_unit=peak_bonus_unit,
        peak_offer_source=page.final_url or page.url if peak_bonus_amount is not None else None,
        annual_fee=annual_fee,
        earn_multipliers=earn_multipliers,
        best_category_uses=_best_category_uses(earn_multipliers),
        card_benefits=card_benefits,
        downgrade_paths=downgrade_paths,
        offer_expiration=offer_expiration,
        eligibility_language=eligibility_language,
        is_business_card=is_business,
        is_targeted=is_targeted,
        offer_status=offer_status,
        source_priority=source_priority(page, issuer, assume_product_page),
        confidence=round(confidence, 2),
        evidence_snippets=evidence,
        changed_fields=[],
        needs_review_reason=needs_review_reason,
        found=product_found and bool(snippets),
    )
