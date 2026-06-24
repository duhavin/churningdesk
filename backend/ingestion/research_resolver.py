"""Targeted card research resolver for unresolved public catalog products.

This module is still part of PUBLIC ingestion. It does not read private user
models and it never opens a browser. It plans focused web-search queries,
caches query results, fetches cited URLs through PageCache, runs source-specific
static adapters, then optionally sends compact snippets to the LLM.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from lxml import html

from sqlalchemy.orm import Session

from .. import card_references, config, models
from ..product_identity import product_display_name, product_reference
from . import extract, fetch, static_parse, validate

SEARCH_CACHE_TTL_DAYS = int(os.getenv("WEB_SEARCH_QUERY_CACHE_DAYS", str(config.WEB_SEARCH_COOLDOWN_DAYS)))
MAX_RESEARCH_SNIPPETS = 10
SNIPPET_CHARS = 550
MAX_DETAIL_LINKS_PER_PRODUCT = 3

QUERY_TYPES = ("current_offer", "issuer_page", "peak_history", "benefits")

ISSUER_DOMAINS = {
    "american express": "americanexpress.com",
    "amex": "americanexpress.com",
    "bank of america": "bankofamerica.com",
    "barclays": "barclaycardus.com",
    "capital one": "capitalone.com",
    "chase": "chase.com",
    "citi": "citi.com",
    "citibank": "citi.com",
    "u.s. bank": "usbank.com",
    "us bank": "usbank.com",
    "wells fargo": "wellsfargo.com",
}

SOURCE_HOSTS = {
    "doctorofcredit.com": "doctor_of_credit",
    "frequentmiler.com": "frequent_miler",
    "uscreditcardguide.com": "us_credit_card_guide",
    "thepointsguy.com": "the_points_guy",
}
DETAIL_LINK_SOURCE_KINDS = {"doctor_of_credit", "frequent_miler", "us_credit_card_guide", "the_points_guy"}

BLOCKED_URL_TERMS = (
    "login",
    "logon",
    "signin",
    "sign-in",
    "account",
    "applynow",
    "application",
    "prequal",
    "pre-qual",
    "preapproved",
    "pre-approved",
    "captcha",
    "auth",
    "secure",
)


@dataclass(slots=True)
class ResearchQuery:
    product_id: int
    query_type: str
    query: str


@dataclass(slots=True)
class QueryResult:
    query: str
    text: str
    sources: list[dict[str, str]]
    from_cache: bool = False


@dataclass(slots=True)
class ProductResolution:
    product_id: int
    row: extract.OfferScanRow | None
    engine: str
    source_urls: list[str] = field(default_factory=list)
    snippets_sent_to_llm: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResearchResolution:
    product_results: list[ProductResolution]
    stats: dict
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


class SearchCache:
    """Tiny JSON cache for search query text + cited source URLs."""

    def __init__(self, root: Path | None = None, ttl_days: int = SEARCH_CACHE_TTL_DAYS) -> None:
        self.root = root or Path(os.getenv("CHURN_SEARCH_CACHE_DIR", "data/search_cache"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.ttl_days = ttl_days
        self._lock = threading.Lock()
        self._index = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def get(self, query: str) -> QueryResult | None:
        with self._lock:
            item = self._index.get(_query_hash(query))
        if not item:
            return None
        fetched_at = _parse_iso(item.get("fetched_at"))
        if not fetched_at or (_utcnow() - fetched_at).days >= self.ttl_days:
            return None
        return QueryResult(
            query=query,
            text=item.get("text") or "",
            sources=list(item.get("sources") or []),
            from_cache=True,
        )

    def put(self, query: str, text: str, sources: list[dict[str, str]]) -> None:
        with self._lock:
            self._index[_query_hash(query)] = {
                "query": query,
                "text": text,
                "sources": sources,
                "fetched_at": _utcnow().isoformat() + "Z",
            }
            self._save()


def _issuer_domain(issuer: str | None) -> str | None:
    low = (issuer or "").strip().lower()
    return ISSUER_DOMAINS.get(low)


def _reference(product: models.CardProduct, db: Session | None = None) -> dict:
    return card_references.reference_for_product(db, product.issuer, product.product_name) or product_reference(
        product.issuer, product.product_name
    )


def _reference_names(product: models.CardProduct, db: Session | None = None) -> list[str]:
    ref = _reference(product, db)
    return list(
        dict.fromkeys(
            name
            for name in [
                ref.get("display_name"),
                *(ref.get("search_terms") or []),
                product.product_name,
            ]
            if name
        )
    )


def build_research_plan(product: models.CardProduct, db: Session | None = None) -> list[ResearchQuery]:
    ref = _reference(product, db)
    card_name = (
        ref.get("display_name")
        or product_display_name(product.issuer, product.product_name)
        or product.product_name
    )
    issuer = (product.issuer or "").strip()
    domain = ref.get("issuer_domain") or _issuer_domain(issuer)
    issuer_query = (
        f"site:{domain} {card_name} welcome offer benefits"
        if domain
        else f"{issuer} {card_name} official welcome offer benefits"
    )
    query_specs = {
        "current_offer": f"{issuer} {card_name} welcome offer bonus spend annual fee",
        "issuer_page": issuer_query,
        "peak_history": f"{card_name} highest ever offer peak bonus Doctor of Credit US Credit Card Guide Frequent Miler",
        "benefits": f"{card_name} benefits credits earn rates terms",
    }
    return [
        ResearchQuery(product_id=product.id, query_type=query_type, query=query_specs[query_type])
        for query_type in QUERY_TYPES
    ]


def _research_prompt(queries: list[ResearchQuery]) -> str:
    lines = "\n".join(f"[Q{i + 1}] {q.query_type}: {q.query}" for i, q in enumerate(queries))
    return (
        "Run public web search for these credit-card research queries. For each query, return a compact "
        "section labeled with its Q number. Prefer official issuer pages, Doctor of Credit, US Credit Card "
        "Guide, Frequent Miler, and The Points Guy. Include cited URLs. Do not use private, login, "
        "prequalified, CAPTCHA, or targeted account pages.\n\n"
        f"{lines}"
    )


def _run_uncached_search(queries: list[ResearchQuery], max_uses: int) -> dict:
    system = (
        "You research public US credit-card offer and benefit pages for a local churning tracker. "
        "Use only public sources. Do not use private account pages, login pages, CAPTCHA pages, or browser automation."
    )
    return extract._run_web_research(system, _research_prompt(queries), max_uses=max_uses, max_tokens=4500)


def _split_research_sections(queries: list[ResearchQuery], text: str) -> dict[str, str]:
    """Split a Q-labeled research response into per-query text blocks."""
    if not text.strip():
        return {}
    markers = list(re.finditer(r"(?im)^\s*\[?Q(\d+)\]?\s*[:.-]", text))
    if not markers:
        return {}
    by_index: dict[int, str] = {}
    for idx, marker in enumerate(markers):
        number = int(marker.group(1))
        start = marker.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
        section = text[start:end].strip()
        if section:
            by_index[number] = section
    out: dict[str, str] = {}
    for index, query in enumerate(queries, start=1):
        section = by_index.get(index)
        if section:
            out[query.query] = section
    return out


def run_searches(
    queries: list[ResearchQuery],
    *,
    cache: SearchCache | None = None,
    max_uses: int | None = None,
) -> tuple[dict[str, QueryResult], dict, list[dict], list[dict]]:
    cache = cache or SearchCache()
    results: dict[str, QueryResult] = {}
    errors: list[dict] = []
    warnings: list[dict] = []
    unique: list[ResearchQuery] = []
    seen: set[str] = set()
    for query in queries:
        if query.query in seen:
            continue
        seen.add(query.query)
        cached = cache.get(query.query)
        if cached:
            results[query.query] = cached
        else:
            unique.append(query)

    web_search_requests = 0
    query_sections_parsed = 0
    if unique:
        try:
            research = _run_uncached_search(
                unique,
                max_uses=max_uses or max(3, min(config.WEB_SEARCH_MAX_USES_PER_BATCH, len(unique))),
            )
            web_search_requests += int(research.get("web_search_requests") or 0)
            text = research.get("text") or ""
            sources = list(research.get("sources") or [])
            sections = _split_research_sections(unique, text)
            query_sections_parsed += len(sections)
            for query in unique:
                query_text = sections.get(query.query) or text
                cache.put(query.query, query_text, sources)
                results[query.query] = QueryResult(query=query.query, text=query_text, sources=sources, from_cache=False)
        except Exception as exc:
            errors.append({"product": "research_search_batch", "error": str(exc)})
            for query in unique:
                warnings.append(
                    {
                        "code": "research_search_failed",
                        "product_id": query.product_id,
                        "query_type": query.query_type,
                        "message": str(exc),
                    }
                )

    stats = {
        "search_queries": len(queries),
        "unique_search_queries": len(seen),
        "cached_search_queries": len([r for r in results.values() if r.from_cache]),
        "uncached_search_queries": len(unique),
        "query_sections_parsed": query_sections_parsed,
        "web_search_requests": web_search_requests,
    }
    return results, stats, errors, warnings


def _is_public_research_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    low = url.lower()
    if any(term in low for term in BLOCKED_URL_TERMS):
        return False
    path = (parsed.path or "").lower()
    if path.startswith("/oc/") or path.startswith("/credit-cards/redirect/"):
        return False
    if validate._is_non_offer_source(url):
        return False
    if "anthropic web search" in low:
        return False
    return True


def _source_kind(url: str | None, issuer: str | None) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    issuer_domain = _issuer_domain(issuer)
    if issuer_domain and issuer_domain in host:
        return "issuer"
    for marker, kind in SOURCE_HOSTS.items():
        if marker in host:
            return kind
    return "generic"


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _text_matches_product(text: str, product: models.CardProduct) -> bool:
    normalized = _norm(text)
    for name in _reference_names(product):
        name_norm = _norm(name)
        if name_norm and name_norm in normalized:
            return True
    tokens = {
        token
        for token in _norm(product.product_name).split()
        if len(token) > 2 and token not in {"the", "card", "credit", "rewards", "reward", "from", "american", "express"}
    }
    return len(tokens.intersection(normalized.split())) >= max(2, min(3, len(tokens)))


def _meaningful_name_tokens(name: str | None) -> set[str]:
    return {
        token
        for token in _norm(name).split()
        if token
        and token
        not in {
            "the",
            "card",
            "credit",
            "rewards",
            "reward",
            "from",
            "personal",
            "business",
        }
    }


def _has_variant_conflict(product: models.CardProduct, text: str | None) -> bool:
    hay_tokens = set(_norm(text).split())
    if not hay_tokens:
        return False
    product_tokens = _meaningful_name_tokens(product.product_name)
    product_is_business = (product.ownership or "").lower() == "business" or "business" in product_tokens
    if "business" in hay_tokens and not product_is_business:
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
        text_hits = hay_tokens & group
        if product_hits and text_hits and product_hits.isdisjoint(text_hits):
            return True

    if "venture" in product_tokens and "venture" in hay_tokens:
        product_has_x = "x" in product_tokens
        text_has_x = "x" in hay_tokens
        if product_has_x != text_has_x:
            return True
    return False


def _link_matches_product(product: models.CardProduct, label: str, url: str, context: str = "") -> bool:
    raw_haystack = f"{label} {context} {urlparse(url).path}"
    if _has_variant_conflict(product, raw_haystack):
        return False
    haystack = _norm(raw_haystack)
    hay_tokens = set(haystack.split())
    for name in _reference_names(product):
        tokens = _meaningful_name_tokens(name)
        # Require at least two meaningful tokens. This avoids matching every
        # "Gold" or "Venture" link on broad roundup pages.
        if len(tokens) >= 2 and tokens.issubset(hay_tokens):
            return True
        normalized = _norm(name)
        if len(tokens) >= 2 and normalized and normalized in haystack:
            return True
    return False


def _detail_link_score(label: str, url: str, context: str = "") -> tuple[int, int]:
    haystack = f"{label} {context} {url}".lower()
    score = 0
    for term, weight in (
        ("read our review", 8),
        ("review", 5),
        ("benefit", 4),
        ("guide", 3),
        ("card", 1),
        ("offer", 1),
    ):
        if term in haystack:
            score += weight
    # Shorter paths are usually the actual review/detail page, not archive pages.
    path_len = len(urlparse(url).path or "")
    return score, -path_len


def _anchor_context(anchor) -> str:
    """Nearby row/list/paragraph text for generic links like 'Read our review'."""
    pieces: list[str] = []
    current = anchor
    for _ in range(4):
        current = current.getparent()
        if current is None:
            break
        tag = (current.tag or "").lower()
        if tag in {"tr", "li", "p", "article", "section", "div"}:
            text = re.sub(r"\s+", " ", " ".join(current.itertext())).strip()
            if text:
                pieces.append(text)
        if tag in {"tr", "li", "article", "section"}:
            break
    return " ".join(pieces)[:900]


def _detail_urls_from_page(
    product: models.CardProduct,
    page: fetch.FetchedPage,
    existing_urls: set[str],
) -> list[str]:
    kind = _source_kind(page.final_url or page.url, product.issuer)
    if kind not in DETAIL_LINK_SOURCE_KINDS:
        return []
    try:
        doc = html.fromstring(page.html)
    except (ValueError, html.ParserError):
        return []
    base = page.final_url or page.url
    base_host = (urlparse(base).hostname or "").lower()
    candidates: list[tuple[tuple[int, int], str]] = []
    for anchor in doc.xpath("//a[@href]"):
        href = anchor.get("href")
        if not href:
            continue
        url = urljoin(base, href)
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() != base_host:
            continue
        normalized = url.split("#", 1)[0].rstrip("/")
        if normalized in existing_urls or not _is_public_research_url(normalized):
            continue
        label = re.sub(r"\s+", " ", " ".join(anchor.itertext())).strip()
        context = _anchor_context(anchor)
        if not _link_matches_product(product, label, normalized, context):
            continue
        candidates.append((_detail_link_score(label, normalized, context), normalized))
    candidates.sort(reverse=True)
    return list(dict.fromkeys(url for _score, url in candidates))[:MAX_DETAIL_LINKS_PER_PRODUCT]


def _discover_detail_urls(
    products: list[models.CardProduct],
    fetched_pages: dict[str, fetch.FetchedPage],
    sources_by_product: dict[int, list[str]],
) -> dict[int, list[str]]:
    detail_urls: dict[int, list[str]] = {}
    for product in products:
        existing = set(sources_by_product.get(product.id, []))
        found: list[str] = []
        for source_url in sources_by_product.get(product.id, []):
            page = fetched_pages.get(source_url)
            if not page:
                continue
            for url in _detail_urls_from_page(product, page, existing | set(found)):
                found.append(url)
        if found:
            detail_urls[product.id] = list(dict.fromkeys(found))
    return detail_urls


def _looks_like_product_page(page: static_parse.ParsedPage, product: models.CardProduct) -> bool:
    haystack = _norm(" ".join([page.final_url or page.url, page.title or "", page.meta_description or ""]))
    for name in _reference_names(product):
        normalized = _norm(name)
        if normalized and normalized in haystack:
            return True
        slug = normalized.replace(" ", "-")
        if slug and slug in (page.final_url or page.url).lower():
            return True
    tokens = {
        token
        for token in _norm(product.product_name).split()
        if len(token) > 2 and token not in {"the", "card", "credit", "rewards", "reward", "from", "american", "express"}
    }
    return len(tokens.intersection(haystack.split())) >= max(2, min(3, len(tokens)))


def _research_text_snippets(
    product: models.CardProduct,
    query_results: list[QueryResult],
    source_urls: list[str],
) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    source = source_urls[0] if source_urls else None
    terms = (
        "welcome",
        "bonus",
        "spend",
        "annual fee",
        "highest",
        "peak",
        "benefit",
        "credit",
        "earn",
        "x ",
        "lounge",
        "uber",
        "dining",
        "travel",
        "grocery",
        "gas",
        "referral",
    )
    for result in query_results:
        text = result.text or ""
        if not text:
            continue
        chunks = [chunk.strip(" -\t") for chunk in re.split(r"(?:\n{1,}|\.\s+)", text) if chunk.strip()]
        for chunk in chunks:
            low = chunk.lower()
            if not any(term in low for term in terms):
                continue
            if not _text_matches_product(chunk, product):
                continue
            compact = re.sub(r"\s+", " ", chunk).strip()
            if not compact:
                continue
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            prefix = f"src={source or 'cited_web_research'} p=2 adapter=web_research_text"
            snippets.append(f"{prefix}\n{compact[:SNIPPET_CHARS]}")
            if len(snippets) >= MAX_RESEARCH_SNIPPETS:
                return snippets
    return snippets


def _metadata_row(
    product: models.CardProduct,
    pages: list[static_parse.ParsedPage],
) -> extract.OfferScanRow | None:
    if not pages:
        return None
    page = pages[0]
    return extract.OfferScanRow(
        issuer=product.issuer,
        card_name=product.product_name,
        product_url=page.final_url or page.url,
        source_url=page.final_url or page.url,
        fetched_at=page.fetched_at,
        content_hash=page.content_hash,
        is_business_card=(product.ownership or "").lower() == "business",
        offer_status="unknown",
        confidence=0.0,
        found=False,
    )


def _assume_product_page(kind: str, page: static_parse.ParsedPage, product: models.CardProduct) -> bool:
    if kind == "issuer":
        return _looks_like_product_page(page, product)
    path = (urlparse(page.final_url or page.url).path or "").lower()
    for candidate in _reference_names(product):
        name = re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
        if name and name in path:
            return True
    return False


def _adapter_row_for_page(
    product: models.CardProduct,
    page: static_parse.ParsedPage,
) -> extract.OfferScanRow | None:
    kind = _source_kind(page.final_url or page.url, product.issuer)
    assume_product_page = _assume_product_page(kind, page, product)
    snippets = static_parse.snippets_for_card(
        page,
        product.issuer,
        product.product_name,
        assume_product_page=assume_product_page,
        max_snippets=MAX_RESEARCH_SNIPPETS,
    )
    if not snippets:
        return None
    row = static_parse.deterministic_offer_row(
        page=page,
        issuer=product.issuer,
        product_name=product.product_name,
        ownership=product.ownership,
        snippets=snippets,
        assume_product_page=assume_product_page,
    )
    row.source_url = page.final_url or page.url
    row.product_url = row.product_url or page.final_url or page.url
    row.fetched_at = page.fetched_at
    row.content_hash = page.content_hash
    if kind != "generic":
        row.confidence = min(0.95, row.confidence + 0.05)
    row.evidence_snippets.setdefault("source_adapter", []).append(kind)
    return row


def _safe_supplemental_source(product: models.CardProduct, row: extract.OfferScanRow) -> bool:
    url = row.source_url or row.product_url
    if not url or _has_variant_conflict(product, f"{url} {row.product_url or ''}"):
        return False
    kind = _source_kind(url, product.issuer)
    if kind == "issuer" and row.source_priority == 1:
        return True
    if kind in DETAIL_LINK_SOURCE_KINDS and _link_matches_product(product, "", url):
        return True
    return False


def _row_score(product: models.CardProduct, row: extract.OfferScanRow) -> tuple:
    public_offer = int(row.offer_status == "public" and row.bonus_amount is not None)
    peak = int(row.peak_bonus_amount is not None)
    benefits = int(bool(row.card_benefits))
    multipliers = int(bool(row.earn_multipliers))
    safe_supplemental = int((benefits or multipliers) and _safe_supplemental_source(product, row))
    return (public_offer, peak, safe_supplemental, benefits + multipliers, row.confidence or 0, -row.source_priority)


def _best_row(product: models.CardProduct, rows: list[extract.OfferScanRow]) -> extract.OfferScanRow | None:
    if not rows:
        return None
    return max(rows, key=lambda row: _row_score(product, row))


def _snippets_for_llm(product: models.CardProduct, pages: list[static_parse.ParsedPage]) -> list[str]:
    snippets: list[str] = []
    for page in pages:
        kind = _source_kind(page.final_url or page.url, product.issuer)
        assume = _assume_product_page(kind, page, product)
        for snippet in static_parse.snippets_for_card(
            page,
            product.issuer,
            product.product_name,
            assume_product_page=assume,
            max_snippets=MAX_RESEARCH_SNIPPETS,
        ):
            source = page.final_url or page.url
            priority = static_parse.source_priority(page, product.issuer, assume)
            snippets.append(f"src={source} p={priority} adapter={kind}\n{snippet[:SNIPPET_CHARS]}")
            if len(snippets) >= MAX_RESEARCH_SNIPPETS:
                return snippets
    return snippets


def _llm_input(
    product: models.CardProduct,
    snippets: list[str],
    best: extract.OfferScanRow | None,
    source_urls: list[str],
) -> extract.OfferSnippetInput:
    return extract.OfferSnippetInput(
        issuer=product.issuer,
        card_name=product.product_name,
        ownership=product.ownership,
        candidate_urls=[
            url
            for url in dict.fromkeys(
                [
                    best.source_url if best else None,
                    best.product_url if best else None,
                    *source_urls,
                ]
            )
            if url
        ],
        snippets=snippets,
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


def _fallback_row(product: models.CardProduct, reason: str) -> extract.OfferScanRow:
    return extract.OfferScanRow(
        issuer=product.issuer,
        card_name=product.product_name,
        is_business_card=(product.ownership or "").lower() == "business",
        offer_status="needs_review",
        confidence=0.0,
        found=False,
        needs_review_reason=reason,
    )


def resolve_products(
    products: list[models.CardProduct],
    *,
    db: Session | None = None,
    llm_fallback: bool = True,
    max_uses: int | None = None,
) -> ResearchResolution:
    queries = [query for product in products for query in build_research_plan(product, db)]
    results_by_query, search_stats, errors, warnings = run_searches(queries, max_uses=max_uses)
    query_results_by_product: dict[int, list[QueryResult]] = {product.id: [] for product in products}
    sources_by_product: dict[int, list[str]] = {
        product.id: card_references.reference_source_urls(db, product) if db is not None else []
        for product in products
    }
    for query in queries:
        result = results_by_query.get(query.query)
        if not result:
            continue
        query_results_by_product.setdefault(query.product_id, []).append(result)
        urls = [source.get("url") for source in result.sources if _is_public_research_url(source.get("url"))]
        sources_by_product.setdefault(query.product_id, []).extend(urls)
    for product_id, urls in list(sources_by_product.items()):
        sources_by_product[product_id] = list(dict.fromkeys(urls))

    all_urls = list(dict.fromkeys(url for urls in sources_by_product.values() for url in urls))
    fetched_pages = fetch.fetch_many_pages(all_urls) if all_urls else {}
    detail_urls_by_product = _discover_detail_urls(products, fetched_pages, sources_by_product)
    detail_urls = list(dict.fromkeys(url for urls in detail_urls_by_product.values() for url in urls))
    if detail_urls:
        for product_id, urls in detail_urls_by_product.items():
            sources_by_product.setdefault(product_id, []).extend(urls)
            sources_by_product[product_id] = list(dict.fromkeys(sources_by_product[product_id]))
        missing_detail_urls = [url for url in detail_urls if url not in fetched_pages]
        if missing_detail_urls:
            fetched_pages.update(fetch.fetch_many_pages(missing_detail_urls))
    parsed_pages = {url: static_parse.parse_page(page) for url, page in fetched_pages.items()}
    cache_hits = sum(1 for page in fetched_pages.values() if page.from_cache)

    adapter_rows_by_product: dict[int, list[extract.OfferScanRow]] = {}
    llm_candidates: list[tuple[models.CardProduct, extract.OfferScanRow | None, extract.OfferScanRow | None, list[str], list[str]]] = []
    product_results: list[ProductResolution] = []
    products_by_id = {product.id: product for product in products}

    for product in products:
        rows: list[extract.OfferScanRow] = []
        product_pages = [parsed_pages[url] for url in sources_by_product.get(product.id, []) if url in parsed_pages]
        for page in product_pages:
            row = _adapter_row_for_page(product, page)
            if row:
                rows.append(row)
        adapter_rows_by_product[product.id] = rows
        best = _best_row(product, rows)
        source_urls = sources_by_product.get(product.id, [])
        page_snippets = _snippets_for_llm(product, product_pages)
        research_snippets = _research_text_snippets(
            product,
            query_results_by_product.get(product.id, []),
            source_urls,
        )
        snippets = list(dict.fromkeys([*page_snippets, *research_snippets]))[:MAX_RESEARCH_SNIPPETS]
        metadata = best or _metadata_row(product, product_pages)
        needs_llm_structure = (
            bool(best)
            and bool(snippets)
            and extract.has_unstructured_supplemental(best)
            and llm_fallback
            and config.llm_available()
        )
        if best and _row_score(product, best) >= (1, 0, 0, 0, 0.78, -5) and not needs_llm_structure:
            product_results.append(
                ProductResolution(
                    product_id=product.id,
                    row=best,
                    engine=f"research_adapter:{_source_kind(best.source_url, product.issuer)}",
                    source_urls=source_urls,
                )
            )
        elif snippets and llm_fallback and config.llm_available():
            llm_candidates.append((product, best, metadata, snippets, source_urls))
        else:
            product_results.append(
                ProductResolution(
                    product_id=product.id,
                    row=best or _fallback_row(product, "research_resolver_no_supported_cited_page"),
                    engine="research_adapter",
                    source_urls=sources_by_product.get(product.id, []),
                )
            )

    llm_batches = 0
    snippets_sent = 0
    for start in range(0, len(llm_candidates), config.WEB_SEARCH_BATCH_SIZE or 4):
        batch = llm_candidates[start : start + (config.WEB_SEARCH_BATCH_SIZE or 4)]
        llm_batches += 1
        inputs = [_llm_input(product, snippets, best, source_urls) for product, best, _metadata, snippets, source_urls in batch]
        snippets_sent += sum(len(item.snippets) for item in inputs)
        try:
            rows = extract.extract_offers_batch(inputs)
        except Exception as exc:
            errors.append({"product": "research_resolver_llm_batch", "error": str(exc)})
            rows = []
        for idx, (product, best, metadata, snippets, source_urls) in enumerate(batch):
            fallback = best or metadata
            row = rows[idx] if idx < len(rows) else fallback
            if row is None:
                row = _fallback_row(product, "research_resolver_llm_returned_no_row")
            elif fallback:
                row.source_url = row.source_url or fallback.source_url
                row.product_url = row.product_url or fallback.product_url
                row.fetched_at = row.fetched_at or fallback.fetched_at
                row.content_hash = row.content_hash or fallback.content_hash
                if not row.evidence_snippets:
                    row.evidence_snippets = fallback.evidence_snippets
            row = extract.normalize_offer_scan_row(row)
            product_results.append(
                ProductResolution(
                    product_id=product.id,
                    row=row,
                    engine="research_llm_compact_snippets",
                    source_urls=source_urls,
                    snippets_sent_to_llm=snippets,
                )
            )

    resolved = len([result for result in product_results if result.row and result.row.found and result.row.confidence >= 0.5])
    needs_data = len(products) - resolved
    stats = {
        **search_stats,
        "cards_queued": len(products),
        "urls_from_search": len(all_urls),
        "urls_fetched": len(fetched_pages),
        "detail_urls_discovered": len(detail_urls),
        "detail_urls_fetched": len([url for url in detail_urls if url in fetched_pages]),
        "cached_urls_reused": cache_hits,
        "adapter_rows": sum(len(rows) for rows in adapter_rows_by_product.values()),
        "llm_batches": llm_batches,
        "llm_snippets_sent": snippets_sent,
        "research_text_snippets": sum(
            len(_research_text_snippets(product, query_results_by_product.get(product.id, []), sources_by_product.get(product.id, [])))
            for product in products_by_id.values()
        ),
        "cards_resolved": resolved,
        "cards_needs_data": needs_data,
    }
    return ResearchResolution(product_results=product_results, stats=stats, errors=errors, warnings=warnings)
