"""LLM structured extraction — PUBLIC data only (§2 ingestion firewall, §4).

This module is the only place that talks to the LLM. It receives PUBLIC page
text and product identities (issuer + product name) and returns structured,
validated data. It must NEVER be passed PRIVATE fields (balances, last4,
identity, targeted offers) — note it does not import the PRIVATE models.
"""
from __future__ import annotations

import json
import os
from typing import Any
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .. import config


class IngestionUnavailable(RuntimeError):
    """Raised when an LLM action is attempted without ANTHROPIC_API_KEY."""


def _client(timeout: float = 60.0) -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise IngestionUnavailable(
            "ANTHROPIC_API_KEY is not set — discovery/refresh are disabled. "
            "Add it to your .env to enable the ingestion engine."
        )
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=timeout, max_retries=0)


def _provider_unavailable(exc: anthropic.APIError) -> IngestionUnavailable:
    detail = getattr(exc, "message", None) or str(exc)
    return IngestionUnavailable(f"Anthropic API request failed: {detail}")


# ---------------------------------------------------------------------------
#  Discovery: enumerate the reputable, churning-relevant card universe (§4.2)
# ---------------------------------------------------------------------------
class DiscoveredCard(BaseModel):
    issuer: str
    product_name: str
    ownership: str = Field(description="Personal or Business")
    account_type: str = Field(
        description="Credit Card, Charge Card, or Flexible Spending Credit Card"
    )
    currency: str | None = Field(
        default=None, description="Rewards currency, e.g. 'Ultimate Rewards', 'cash back'"
    )
    source_url: str | None = None
    eligibility_tags: list[str] | None = None
    tag: str | None = Field(
        default=None,
        description="One of: transferable, hotel_cobrand, airline_cobrand, or null",
    )
    reports_to_personal_credit: bool = Field(
        default=True,
        description="False for most small-business cards (they don't report to personal credit)",
    )
    notes: str | None = None


class DiscoveryResult(BaseModel):
    cards: list[DiscoveredCard]


def discover_cards(issuers: list[str]) -> list[DiscoveredCard]:
    """Ask the LLM to enumerate card *identities* for the given issuers."""
    client = _client()
    issuer_list = ", ".join(issuers)
    prompt = (
        "Enumerate the reputable, churning-relevant credit cards (cards that carry "
        "welcome bonuses) for these issuers and co-brand programs: "
        f"{issuer_list}.\n\n"
        "Include personal AND business variants. EXCLUDE store-only/retail cards and "
        "subprime cards. Use each card's current official product name. Do not invent "
        "offer values — only identities. Return as many real, current products as you "
        "can recall."
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
            output_format=DiscoveryResult,
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    result = resp.parsed_output
    return result.cards if result else []


def research_card_universe(issuers: list[str], max_uses: int | None = None) -> dict[str, Any]:
    """Cited web research for the public card universe.

    This mirrors Churn Control's faster path: one broad search report, then a
    compact structured identity pass. It only handles PUBLIC product identity.
    """
    issuer_scope = ", ".join(issuers)
    system = (
        "You are doing public web research for a private credit-card churning tracker. "
        "Search only for public US credit-card product identities from reputable national "
        "banks, airline cards, and hotel cards. Never ask for or infer private user data."
    )
    prompt = (
        "Search the web for the current reputable US churning-relevant credit-card universe "
        "for this scope. Focus on major issuers and established airline/hotel co-brands with "
        "welcome bonuses. Exclude store-only, retail-only, debit, prepaid, secured, subprime, "
        "and obscure local cards unless a source clearly frames them as broadly churning-relevant. "
        "Return a compact report grouped by issuer with citations. Do not include private data.\n\n"
        f"Scope: {issuer_scope}"
    )
    uses = max_uses if max_uses is not None else int(os.getenv("WEB_SEARCH_DISCOVERY_MAX_USES", "8"))
    return _run_web_research(system, prompt, uses, max_tokens=int(os.getenv("ANTHROPIC_DISCOVERY_RESEARCH_MAX_TOKENS", "5000")))


def discover_cards_from_research(
    issuers: list[str],
    research_text: str,
    source_urls: list[str] | None = None,
) -> list[DiscoveredCard]:
    """Extract card identities from cited public research text."""
    if not research_text.strip():
        return []
    client = _client()
    tool = {
        "name": "record_card_universe",
        "description": "Return reputable churning-relevant credit-card product identities only.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "issuer": {"type": "string"},
                            "product_name": {"type": "string"},
                            "ownership": {"type": "string", "enum": ["Personal", "Business"]},
                            "account_type": {"type": "string"},
                            "currency": {"type": ["string", "null"]},
                            "source_url": {"type": ["string", "null"]},
                            "eligibility_tags": {"type": "array", "items": {"type": "string"}},
                            "tag": {"type": ["string", "null"]},
                            "reports_to_personal_credit": {"type": "boolean"},
                            "notes": {"type": ["string", "null"]},
                        },
                        "required": [
                            "issuer",
                            "product_name",
                            "ownership",
                            "account_type",
                            "reports_to_personal_credit",
                            "eligibility_tags",
                        ],
                    },
                }
            },
            "required": ["cards"],
        },
    }
    prompt = (
        "Extract reputable, churning-relevant US credit-card product identities from the "
        "public web research report below. Use only supported card identities from the report. "
        "Do not extract offer amounts. Exclude store-only, retail-only, debit, prepaid, secured, "
        "subprime, closed invite-only, and obscure local cards unless explicitly in scope.\n\n"
        "For each card return issuer, product_name, ownership, account_type, currency, source_url, "
        "eligibility_tags, tag, reports_to_personal_credit, and notes. Use tags only from: "
        "transferable, hotel_cobrand, airline_cobrand, future_trip, or null. Useful eligibility "
        "tags include requires_under_524, sapphire_48mo, chase_ink_90, amex_once_per_lifetime, "
        "citi_spacing, capital_one_recent_sensitive, closed_to_new_applicants.\n\n"
        f"REQUESTED ISSUER SCOPE:\n{json.dumps(issuers, ensure_ascii=True)}\n\n"
        f"KNOWN SOURCE URLS:\n{json.dumps(source_urls or [], ensure_ascii=True)}\n\n"
        "RESEARCH REPORT:\n"
        f"{research_text}"
    )
    try:
        message = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=int(os.getenv("ANTHROPIC_DISCOVERY_MAX_TOKENS", "5000")),
            temperature=0,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    cards = _tool_payload(message).get("cards") or []
    out: list[DiscoveredCard] = []
    for card in cards:
        out.append(
            DiscoveredCard(
                issuer=str(card.get("issuer") or "").strip(),
                product_name=str(card.get("product_name") or "").strip(),
                ownership=card.get("ownership") or "Personal",
                account_type=card.get("account_type") or "Credit Card",
                currency=card.get("currency"),
                source_url=card.get("source_url"),
                eligibility_tags=card.get("eligibility_tags") or None,
                tag=card.get("tag"),
                reports_to_personal_credit=bool(card.get("reports_to_personal_credit", True)),
                notes=card.get("notes"),
            )
        )
    return out


# ---------------------------------------------------------------------------
#  Offer extraction from a PUBLIC page (§4.4, §4.5)
# ---------------------------------------------------------------------------
class EarnMultiplier(BaseModel):
    category: str
    multiplier: float


OfferStatus = Literal["public", "targeted", "affiliate", "expired", "unknown", "needs_review"]


class OfferScanRow(BaseModel):
    """Strict row shape for the optimized scan pipeline."""

    issuer: str
    card_name: str
    product_url: str | None = None
    source_url: str | None = None
    fetched_at: str | None = None
    content_hash: str | None = None
    bonus_amount: float | None = None
    bonus_unit: str | None = None
    spend_requirement: float | None = None
    spend_window_months: int | None = None
    peak_bonus_amount: float | None = None
    peak_bonus_unit: str | None = None
    peak_spend_requirement: float | None = None
    peak_offer_date: str | None = None
    peak_offer_source: str | None = None
    referral_bonus_amount: float | None = None
    referral_bonus_unit: str | None = None
    annual_fee: float | None = None
    first_year_credit_value: float | None = None
    earn_multipliers: dict[str, float] | None = None
    best_category_uses: dict[str, str] | None = None
    card_benefits: list[str] | None = None
    downgrade_paths: list[str] | None = None
    eligibility_tags: list[str] | None = None
    reports_to_personal_credit: bool | None = None
    offer_expiration: str | None = None
    eligibility_language: str | None = None
    is_business_card: bool = False
    is_targeted: bool = False
    offer_status: OfferStatus = "unknown"
    source_priority: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_snippets: dict[str, list[str]] = Field(default_factory=dict)
    changed_fields: list[str] = Field(default_factory=list)
    needs_review_reason: str | None = None
    found: bool = True


class OfferSnippetInput(BaseModel):
    issuer: str
    card_name: str
    ownership: str | None = None
    candidate_urls: list[str] = Field(default_factory=list)
    snippets: list[str] = Field(default_factory=list)
    deterministic_guess: dict | None = None


class OfferScanBatchResult(BaseModel):
    rows: list[OfferScanRow]


class OfferExtraction(BaseModel):
    found: bool = Field(description="True only if this exact product was found on the page")
    confidence: float = Field(description="0.0-1.0 confidence in the extracted values")
    currency: str | None = None
    annual_fee: float | None = None
    current_offer_points: int | None = None
    current_offer_cash: float | None = None
    current_offer_min_spend: float | None = None
    current_offer_window_months: int | None = None
    peak_offer_points: int | None = Field(
        default=None, description="All-time best/peak welcome bonus ('target/goal')"
    )
    peak_offer_min_spend: float | None = None
    peak_offer_date: str | None = Field(
        default=None, description="When the peak offer was seen, if known"
    )
    peak_offer_source: str | None = None
    targeted_peak_offer_points: int | None = Field(
        default=None,
        description="Highest TARGETED/invite-only/incognito/phone offer ever seen (kept separate from public peak)",
    )
    targeted_peak_offer_cash: float | None = None
    targeted_peak_offer_source: str | None = None
    targeted_peak_offer_date: str | None = None
    referral_bonus_points: int | None = Field(
        default=None,
        description="Points the existing cardholder earns for a 'refer a friend' referral",
    )
    referral_bonus_cash: float | None = None
    best_category_uses: dict[str, str] | None = Field(
        default=None,
        description="{category: short note} for travel/dining/groceries/gas/everyday earn strengths",
    )
    card_benefits: list[str] | None = Field(
        default=None, description="List of notable perks/credits this card carries"
    )
    downgrade_paths: list[str] | None = Field(
        default=None, description="Product-change/downgrade target card names"
    )
    first_year_credit_value: float | None = None
    earn_multipliers: list[EarnMultiplier] | None = None
    eligibility_tags: list[str] | None = Field(
        default=None,
        description="e.g. requires_under_524, amex_once_per_lifetime, sapphire_48mo",
    )
    reports_to_personal_credit: bool | None = None
    offer_expiration: str | None = None
    eligibility_language: str | None = None
    is_targeted: bool | None = None
    offer_status: OfferStatus = "unknown"
    source_priority: int | None = None
    evidence_snippets: dict[str, list[str]] = Field(default_factory=dict)
    changed_fields: list[str] = Field(default_factory=list)
    needs_review_reason: str | None = None
    source_url: str | None = None
    product_url: str | None = None
    fetched_at: str | None = None
    content_hash: str | None = None


class OfferExtractionBatchResult(BaseModel):
    offers: list[OfferExtraction]


def extract_offers_batch(cards: list[OfferSnippetInput]) -> list[OfferScanRow]:
    """Extract public offers from compact snippets for multiple cards at once.

    The model is explicitly limited to the supplied snippets; this is not a web
    search/browser workflow.
    """
    if not cards:
        return []
    client = _client()
    payload = [c.model_dump(mode="json") for c in cards]
    prompt = (
        "You are extracting PUBLIC credit-card welcome offer data from compact "
        "snippets already fetched by a static HTTP pipeline. Use ONLY the supplied "
        "snippets. Do not browse, search the web, infer from memory, or use private "
        "targeted account pages.\n\n"
        "Return exactly one strict JSON row per input card using these fields: "
        "issuer, card_name, product_url, source_url, fetched_at, content_hash, "
        "bonus_amount, bonus_unit, spend_requirement, spend_window_months, "
        "peak_bonus_amount, peak_bonus_unit, peak_spend_requirement, peak_offer_date, "
        "referral_bonus_amount, referral_bonus_unit, first_year_credit_value, "
        "earn_multipliers, best_category_uses, card_benefits, downgrade_paths, "
        "annual_fee, offer_expiration, eligibility_language, is_business_card, "
        "is_targeted, offer_status, source_priority, confidence, evidence_snippets, "
        "changed_fields, needs_review_reason, found.\n\n"
        "Rules:\n"
        "- bonus_amount/bonus_unit/spend_requirement describe the CURRENT public offer.\n"
        "- peak_bonus_amount/peak_bonus_unit/peak_spend_requirement/peak_offer_date describe the "
        "ALL-TIME BEST (peak) welcome bonus ever offered for this card — the target/goal. Only fill "
        "these when the snippets explicitly mention a historical/record-high offer; otherwise leave "
        "them null.\n"
        "- referral_bonus_amount/referral_bonus_unit describe the 'refer a friend' bonus the "
        "EXISTING cardholder earns for referring someone; leave null if not mentioned.\n"
        "- earn_multipliers is an object like {\"dining\": 4, \"travel\": 3}; best_category_uses "
        "is short user-facing category guidance.\n"
        "- card_benefits lists notable public perks/credits; downgrade_paths lists product-change targets.\n"
        "- first_year_credit_value is the cited dollar value of first-year credits only; leave null if not cited.\n"
        "- offer_status must be one of public, targeted, affiliate, expired, unknown, needs_review.\n"
        "- source_priority: 1 official issuer page, 2 trusted secondary source, 3 other.\n"
        "- evidence_snippets must map each extracted field to short exact snippets that support it.\n"
        "- If snippets do not support a field, leave it null and explain in needs_review_reason.\n"
        "- If the exact product is not present, set found=false and confidence below 0.5.\n\n"
        "INPUT:\n"
        f"{json.dumps(payload, ensure_ascii=True)}"
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=min(8000, 1600 + len(cards) * 1050),
            messages=[{"role": "user", "content": prompt}],
            output_format=OfferScanBatchResult,
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    result = resp.parsed_output
    return result.rows if result else []


def extract_offer(
    issuer: str, product_name: str, page_text: str
) -> OfferExtraction | None:
    """Extract one product's PUBLIC offer fields from clean page text."""
    client = _client()
    prompt = (
        f"From the page text below, extract the CURRENT public welcome offer and "
        f"related facts for this specific card:\n\n"
        f"Issuer: {issuer}\nProduct: {product_name}\n\n"
        "Rules:\n"
        "- Set found=false if this exact card is not described on the page.\n"
        "- 'current_offer_points' = the current PUBLIC sign-up bonus in points/miles.\n"
        "- 'peak_offer_points' = the all-time best PUBLIC bonus ever offered (the target). "
        "If the text only shows the current offer, leave peak null.\n"
        "- Do NOT treat targeted/invite-only/incognito/phone/'as high as' offers as the public "
        "offer; put those high-water marks in 'targeted_peak_offer_points' (with source/date).\n"
        "- 'referral_bonus_points'/'referral_bonus_cash' = what an existing cardholder earns to refer.\n"
        "- 'best_category_uses' = {category: short note} for travel/dining/groceries/gas/everyday.\n"
        "- 'card_benefits' = notable perks/credits; 'downgrade_paths' = product-change targets.\n"
        "- 'currency' is the loyalty currency (e.g. Chase Ultimate Rewards), never USD.\n"
        "- Use numbers only (no commas). Leave unknown fields null.\n\n"
        "=== PAGE TEXT START ===\n"
        f"{page_text}\n"
        "=== PAGE TEXT END ==="
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            output_format=OfferExtraction,
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    return resp.parsed_output


# ---------------------------------------------------------------------------
#  Point valuations (cpp) from published sources (§4.5)
# ---------------------------------------------------------------------------
class CurrencyValuation(BaseModel):
    currency: str
    cpp: float = Field(description="Cents per point/mile, e.g. 1.7")
    source_url: str | None = None


class ValuationResult(BaseModel):
    valuations: list[CurrencyValuation]


def extract_valuations(
    currencies: list[str],
    research_text: str | None = None,
    source_urls: list[str] | None = None,
) -> list[CurrencyValuation]:
    """Extract sourced cpp values from a cited public research report."""
    client = _client()
    if not research_text:
        return []
    prompt = (
        "Extract point/mile valuations in cents per point ('cpp') for these rewards "
        "currencies from the cited public research report below. Use only values "
        "supported by the report; do not infer from memory. Include a real source_url "
        "for each row from the known source URLs when possible.\n\n"
        f"{', '.join(currencies)}\n\n"
        f"KNOWN SOURCE URLS:\n{json.dumps(source_urls or [], ensure_ascii=True)}\n\n"
        "RESEARCH REPORT:\n"
        f"{research_text}"
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            output_format=ValuationResult,
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    result = resp.parsed_output
    return result.valuations if result else []


def research_point_valuations(currencies: list[str], max_uses: int | None = None) -> dict[str, Any]:
    """Cited web research for valuation refreshes."""
    system = (
        "You research public loyalty-program point valuations. Return concise cited "
        "facts only; no private account data."
    )
    prompt = (
        "Find current reputable published valuation estimates, in cents per point/mile, "
        "for these rewards currencies. Prefer sources that publish valuation tables "
        "or methodology. Include the currency name, cpp value, and cited URL.\n\n"
        f"CURRENCIES:\n{json.dumps(currencies, ensure_ascii=True)}"
    )
    uses = max_uses if max_uses is not None else int(os.getenv("WEB_SEARCH_VALUATION_MAX_USES", "5"))
    return _run_web_research(system, prompt, uses, max_tokens=3000)


# ---------------------------------------------------------------------------
#  Web-search fallback (cited) — used only for cards static scraping can't fix
# ---------------------------------------------------------------------------
def _web_search_tool(max_uses: int) -> dict:
    tool = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
        "user_location": {"type": "approximate", "country": "US", "timezone": "America/Los_Angeles"},
    }
    allowed = [item.strip() for item in os.getenv("WEB_SEARCH_ALLOWED_DOMAINS", "").split(",") if item.strip()]
    blocked = [item.strip() for item in os.getenv("WEB_SEARCH_BLOCKED_DOMAINS", "").split(",") if item.strip()]
    if allowed:
        tool["allowed_domains"] = allowed
    if blocked:
        tool["blocked_domains"] = blocked
    return tool


def _citation_to_dict(citation: Any) -> dict[str, str]:
    if isinstance(citation, dict):
        return {
            "url": citation.get("url", ""),
            "title": citation.get("title", ""),
            "cited_text": citation.get("cited_text", ""),
        }
    return {
        "url": getattr(citation, "url", ""),
        "title": getattr(citation, "title", ""),
        "cited_text": getattr(citation, "cited_text", ""),
    }


def _message_text_sources_and_usage(message: Any) -> dict[str, Any]:
    texts: list[str] = []
    sources: list[dict[str, str]] = []
    errors: list[str] = []

    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None)
        if isinstance(block, dict):
            block_type = block.get("type")
        if block_type == "text":
            text = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
            if text:
                texts.append(text)
            citations = getattr(block, "citations", None) if not isinstance(block, dict) else block.get("citations")
            for citation in citations or []:
                source = _citation_to_dict(citation)
                if source["url"]:
                    sources.append(source)
        elif block_type == "web_search_tool_result":
            content = getattr(block, "content", None) if not isinstance(block, dict) else block.get("content")
            if isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
                errors.append(content.get("error_code", "unknown_web_search_error"))
            elif isinstance(content, list):
                for item in content:
                    url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                    if not url:
                        continue
                    sources.append(
                        {
                            "url": url,
                            "title": item.get("title", "") if isinstance(item, dict) else getattr(item, "title", ""),
                            "cited_text": item.get("page_age", "") if isinstance(item, dict) else getattr(item, "page_age", ""),
                        }
                    )

    if errors:
        raise IngestionUnavailable(f"Anthropic web search failed: {', '.join(errors)}")

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if source["url"] in seen:
            continue
        seen.add(source["url"])
        deduped.append(source)

    usage = getattr(message, "usage", None)
    server_tool_use = getattr(usage, "server_tool_use", None) if usage else None
    requests = 0
    if isinstance(server_tool_use, dict):
        requests = int(server_tool_use.get("web_search_requests") or 0)
    elif server_tool_use is not None:
        requests = int(getattr(server_tool_use, "web_search_requests", 0) or 0)

    return {
        "text": "\n\n".join(texts).strip(),
        "sources": deduped,
        "web_search_requests": requests,
    }


def _run_web_research(system: str, prompt: str, max_uses: int, max_tokens: int = 3000) -> dict[str, Any]:
    """Run cited Anthropic web search and return compact text + source URLs."""
    client = _client(timeout=float(os.getenv("ANTHROPIC_WEB_TIMEOUT_SECONDS", "180")))
    try:
        msg = client.messages.create(
            model=config.ANTHROPIC_SEARCH_MODEL,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            tools=[_web_search_tool(max_uses)],
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    result = _message_text_sources_and_usage(msg)
    if not result["text"]:
        raise IngestionUnavailable("Anthropic web search returned no research text.")
    return result


def research_offers_text(cards: list[dict], max_uses: int | None = None) -> str:
    """Cited web research for current + all-time-peak offers across several cards."""
    lines = "\n".join(
        f"- {c.get('issuer','').strip()} {c.get('product_name','').strip()}" for c in cards
    )
    system = (
        "You research PUBLIC credit-card offer data for a private churning tracker. "
        "Search current public pages only; never request or infer private user data. "
        "Prefer official issuer pages and reputable offer-history trackers."
    )
    prompt = (
        "Search the web for public offer data for each card below. For each, report: current "
        "welcome offer points/cash, minimum spend and spend window (months), annual fee, "
        "first-year statement credits, the ALL-TIME-BEST public welcome offer (points + date) "
        "and SEPARATELY any highest targeted/incognito/phone offer, refer-a-friend bonus, "
        "key benefits, and best spend categories. Cite source URLs and dates. Keep each card "
        "in its own clearly labeled section. Do not include private user data.\n\n"
        f"{lines}"
    )
    uses = max_uses if max_uses is not None else max(4, min(12, len(cards) * 2))
    return _run_web_research(system, prompt, uses, max_tokens=4000)["text"]


def research_product_offers(cards: list[dict], max_uses: int | None = None) -> dict[str, Any]:
    """Churn Control-style batched cited research for unresolved products."""
    lines = "\n".join(
        f"- {c.get('issuer','').strip()} {c.get('product_name','').strip()}" for c in cards
    )
    system = (
        "You are doing public web research for a private credit-card churning tracker. "
        "Search current public pages only. Never request, use, or infer private user information. "
        "Prefer official issuer pages and reputable offer-history trackers. Keep each card's findings "
        "in a clearly labeled section."
    )
    prompt = (
        "Search the web for public offer data for each card below. For each card, cite current welcome "
        "offer points/cash, minimum spend and spend window, annual fee, statement credits/first-year "
        "credits, earn multipliers, all-time-best or highest-ever public offer and date if available, "
        "highest targeted/incognito/phone/prequalified offer separately if found, whether it reports to "
        "personal credit, referral bonus, benefits, downgrade paths, and issuer eligibility rules/tags. "
        "Return compact sections with source URLs and dates when available. Do not include private data.\n\n"
        f"{lines}"
    )
    uses = max_uses if max_uses is not None else max(4, min(config.WEB_SEARCH_MAX_USES_PER_BATCH, len(cards) + 2))
    max_tokens = min(8000, 2500 + len(cards) * 550)
    return _run_web_research(system, prompt, uses, max_tokens=max_tokens)


def extract_researched_offers(
    cards: list[dict],
    research_text: str,
    source_urls: list[str] | None = None,
) -> list[OfferExtraction]:
    """Parse compact cited research into one OfferExtraction per input card."""
    if not cards or not research_text.strip():
        return []
    client = _client()
    payload = [{"issuer": c.get("issuer"), "product_name": c.get("product_name")} for c in cards]
    prompt = (
        "Parse the compact public web-research report below into structured card "
        "offer facts. Return exactly one object per input card in the same order. "
        "Use only the report text; do not browse or infer private/targeted account data.\n\n"
        "Rules:\n"
        "- current_offer_* fields must describe the current PUBLIC offer only.\n"
        "- Put invite-only/incognito/prequalified/phone/mail/as-high-as offers in "
        "targeted_peak_offer_* fields, not public current or public peak fields.\n"
        "- peak_offer_points is the all-time best PUBLIC welcome offer; never lower "
        "than current_offer_points when both are known.\n"
        "- Include source_url/product_url when the report gives a cited URL.\n"
        "- Mark found=false and confidence below 0.5 if the report does not cover the card.\n\n"
        f"INPUT CARDS:\n{json.dumps(payload, ensure_ascii=True)}\n\n"
        f"KNOWN SOURCE URLS:\n{json.dumps(source_urls or [], ensure_ascii=True)}\n\n"
        "RESEARCH REPORT:\n"
        f"{research_text}"
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=min(8000, 1200 + len(cards) * 900),
            messages=[{"role": "user", "content": prompt}],
            output_format=OfferExtractionBatchResult,
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    result = resp.parsed_output
    return result.offers if result else []


def _tool_payload(message: Any) -> dict[str, Any]:
    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use":
            return getattr(block, "input", {}) or {}
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("input", {}) or {}
    raise IngestionUnavailable("LLM response did not contain the required tool-use payload.")


def _research_offer_tool_schema() -> dict[str, Any]:
    row_props: dict[str, Any] = {
        "issuer": {"type": "string"},
        "card_name": {"type": "string"},
        "product_url": {"type": ["string", "null"]},
        "source_url": {"type": ["string", "null"]},
        "bonus_amount": {"type": ["number", "null"]},
        "bonus_unit": {"type": ["string", "null"]},
        "spend_requirement": {"type": ["number", "null"]},
        "spend_window_months": {"type": ["integer", "null"]},
        "peak_bonus_amount": {"type": ["number", "null"]},
        "peak_bonus_unit": {"type": ["string", "null"]},
        "peak_spend_requirement": {"type": ["number", "null"]},
        "peak_offer_date": {"type": ["string", "null"]},
        "peak_offer_source": {"type": ["string", "null"]},
        "referral_bonus_amount": {"type": ["number", "null"]},
        "referral_bonus_unit": {"type": ["string", "null"]},
        "annual_fee": {"type": ["number", "null"]},
        "first_year_credit_value": {"type": ["number", "null"]},
        "earn_multipliers": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "number"},
        },
        "best_category_uses": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
        },
        "card_benefits": {"type": ["array", "null"], "items": {"type": "string"}},
        "downgrade_paths": {"type": ["array", "null"], "items": {"type": "string"}},
        "eligibility_tags": {"type": ["array", "null"], "items": {"type": "string"}},
        "reports_to_personal_credit": {"type": ["boolean", "null"]},
        "offer_expiration": {"type": ["string", "null"]},
        "eligibility_language": {"type": ["string", "null"]},
        "is_business_card": {"type": "boolean"},
        "is_targeted": {"type": "boolean"},
        "offer_status": {
            "type": "string",
            "enum": ["public", "targeted", "affiliate", "expired", "unknown", "needs_review"],
        },
        "source_priority": {"type": "integer", "minimum": 1, "maximum": 5},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_snippets": {"type": "array", "items": {"type": "string"}},
        "needs_review_reason": {"type": ["string", "null"]},
        "found": {"type": "boolean"},
    }
    return {
        "name": "record_researched_offer_rows",
        "description": "Record compact public credit-card offer rows from cited research.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": row_props,
                        "required": [
                            "issuer",
                            "card_name",
                            "is_business_card",
                            "is_targeted",
                            "offer_status",
                            "source_priority",
                            "confidence",
                            "evidence_snippets",
                            "found",
                        ],
                    },
                }
            },
            "required": ["rows"],
        },
    }


def _row_from_tool_payload(payload: dict[str, Any], fallback: dict[str, Any] | None = None) -> OfferScanRow:
    fallback = fallback or {}
    evidence_list = [str(item) for item in payload.get("evidence_snippets") or [] if str(item).strip()]
    evidence: dict[str, list[str]] = {}
    for field in (
        "bonus_amount",
        "bonus_unit",
        "spend_requirement",
        "spend_window_months",
        "peak_bonus_amount",
        "peak_offer_source",
        "referral_bonus_amount",
        "annual_fee",
        "first_year_credit_value",
        "earn_multipliers",
        "best_category_uses",
        "card_benefits",
        "downgrade_paths",
        "eligibility_tags",
        "reports_to_personal_credit",
        "eligibility_language",
    ):
        if payload.get(field) is not None and evidence_list:
            evidence[field] = evidence_list[:4]
    return OfferScanRow(
        issuer=str(payload.get("issuer") or fallback.get("issuer") or ""),
        card_name=str(payload.get("card_name") or fallback.get("product_name") or fallback.get("card_name") or ""),
        product_url=payload.get("product_url"),
        source_url=payload.get("source_url"),
        bonus_amount=payload.get("bonus_amount"),
        bonus_unit=payload.get("bonus_unit"),
        spend_requirement=payload.get("spend_requirement"),
        spend_window_months=payload.get("spend_window_months"),
        peak_bonus_amount=payload.get("peak_bonus_amount"),
        peak_bonus_unit=payload.get("peak_bonus_unit"),
        peak_spend_requirement=payload.get("peak_spend_requirement"),
        peak_offer_date=payload.get("peak_offer_date"),
        peak_offer_source=payload.get("peak_offer_source"),
        referral_bonus_amount=payload.get("referral_bonus_amount"),
        referral_bonus_unit=payload.get("referral_bonus_unit"),
        annual_fee=payload.get("annual_fee"),
        first_year_credit_value=payload.get("first_year_credit_value"),
        earn_multipliers=payload.get("earn_multipliers"),
        best_category_uses=payload.get("best_category_uses"),
        card_benefits=payload.get("card_benefits"),
        downgrade_paths=payload.get("downgrade_paths"),
        eligibility_tags=payload.get("eligibility_tags"),
        reports_to_personal_credit=payload.get("reports_to_personal_credit"),
        offer_expiration=payload.get("offer_expiration"),
        eligibility_language=payload.get("eligibility_language"),
        is_business_card=bool(payload.get("is_business_card")),
        is_targeted=bool(payload.get("is_targeted")),
        offer_status=payload.get("offer_status") or "unknown",
        source_priority=int(payload.get("source_priority") or 2),
        confidence=float(payload.get("confidence") or 0.0),
        evidence_snippets=evidence,
        needs_review_reason=payload.get("needs_review_reason"),
        found=bool(payload.get("found")),
    )


def extract_researched_offer_rows(
    cards: list[dict],
    research_text: str,
    source_urls: list[str] | None = None,
) -> list[OfferScanRow]:
    """Parse cited research into compact rows using a small explicit tool schema.

    This intentionally avoids the larger OfferExtraction Pydantic grammar, which
    can exceed Anthropic's strict compiled grammar limits during web fallback.
    """
    if not cards or not research_text.strip():
        return []
    client = _client()
    tool = _research_offer_tool_schema()
    payload = [{"issuer": c.get("issuer"), "product_name": c.get("product_name")} for c in cards]
    prompt = (
        "Parse the compact public web-research report below into one offer row per input card. "
        "Every row must carry the exact issuer and card_name it supports. Use only the report text "
        "and cited URLs. Do not browse again and do "
        "not infer private/targeted account data.\n\n"
        "Rules:\n"
        "- bonus_amount/bonus_unit are the CURRENT PUBLIC welcome offer only.\n"
        "- If the offer is cash or statement credit, use bonus_unit='cash back'.\n"
        "- If the offer is points/miles, use the loyalty currency when known, otherwise points or miles.\n"
        "- peak_bonus_amount is the all-time-best PUBLIC welcome offer only; leave null if not cited.\n"
        "- Targeted/incognito/phone/prequalified/mail/as-high-as offers are offer_status='targeted' and "
        "is_targeted=true. Do not mark them public.\n"
        "- referral_bonus_amount/referral_bonus_unit are what the existing cardholder earns for referring.\n"
        "- earn_multipliers is an object like {\"dining\": 4, \"travel\": 3}; best_category_uses is short user-facing notes.\n"
        "- Include benefits, downgrade paths, eligibility tags, first-year credits, and reports_to_personal_credit only when cited.\n"
        "- found=false with confidence below 0.5 if the report does not cover the exact card.\n"
        "- evidence_snippets should be short copied snippets from the research report, not full pages.\n\n"
        f"INPUT CARDS:\n{json.dumps(payload, ensure_ascii=True)}\n\n"
        f"KNOWN SOURCE URLS:\n{json.dumps(source_urls or [], ensure_ascii=True)}\n\n"
        "RESEARCH REPORT:\n"
        f"{research_text}"
    )
    try:
        message = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=min(8000, 1200 + len(cards) * 800),
            temperature=0,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    rows_payload = _tool_payload(message).get("rows") or []
    rows = []
    for row_payload in rows_payload:
        rows.append(_row_from_tool_payload(row_payload))
    return rows

