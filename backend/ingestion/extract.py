"""LLM structured extraction — PUBLIC data only (§2 ingestion firewall, §4).

This module is the only place that talks to the LLM. It receives PUBLIC page
text and product identities (issuer + product name) and returns structured,
validated data. It must NEVER be passed PRIVATE fields (balances, last4,
identity, targeted offers) — note it does not import the PRIVATE models.
"""
from __future__ import annotations

import json
import os
import re
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


def _is_schema_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "compiled grammar is too large" in text or "simplify your tool schemas" in text


def _message_text(message: Any) -> str:
    pieces: list[str] = []
    for block in getattr(message, "content", []) or []:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                pieces.append(str(block.get("text")))
            continue
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            pieces.append(str(block.text))
    return "\n".join(pieces).strip()


def _parse_json_object(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(raw[start : end + 1])


def _coerce_offer_scan_batch_payload(payload: Any) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return payload
    for row in payload["rows"]:
        if not isinstance(row, dict):
            continue
        if row.get("changed_fields") is None:
            row["changed_fields"] = []
        evidence = row.get("evidence_snippets")
        if isinstance(evidence, dict):
            row["evidence_snippets"] = {
                str(key): value
                if isinstance(value, list)
                else ([] if value is None else [str(value)])
                for key, value in evidence.items()
            }
    return payload


def _parse_offer_scan_batch_json(text: str) -> list[OfferScanRow]:
    payload = _coerce_offer_scan_batch_payload(_parse_json_object(text))
    result = OfferScanBatchResult.model_validate(payload)
    return [normalize_offer_scan_row(row) for row in result.rows]


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
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
            output_format=DiscoveryResult,
        )
    except anthropic.APIError as exc:
        raise _provider_unavailable(exc) from exc
    result = resp.parsed_output
    return result.cards if result else []


def research_card_universe(issuers: list[str], max_uses: int | None = None) -> dict[str, Any]:
    """Cited web research for the public card universe.

    This mirrors WEwards Control's faster path: one broad search report, then a
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
    return _run_web_research(system, prompt, uses, max_tokens=int(os.getenv("ANTHROPIC_DISCOVERY_RESEARCH_MAX_TOKENS", "4000")))


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
            max_tokens=int(os.getenv("ANTHROPIC_DISCOVERY_MAX_TOKENS", "4000")),
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


class StructuredBenefit(BaseModel):
    """Clean public benefit shape stored in CardProduct.card_benefits."""

    name: str = Field(description="Short user-facing benefit name, e.g. '$300 travel credit'")
    value: str | None = Field(default=None, description="Benefit amount/value, e.g. '$300' or '10,000 miles'")
    frequency: Literal[
        "monthly",
        "quarterly",
        "semiannual",
        "annual",
        "anniversary",
        "one_time",
        "ongoing",
        "unknown",
    ] = "unknown"
    category: str | None = Field(default=None, description="travel, dining, groceries, gas, lounge, hotel, airline, etc.")
    description: str | None = Field(default=None, description="One concise supporting detail, not raw page text")
    evidence: str | None = Field(default=None, description="Short exact snippet supporting this benefit")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


BenefitValue = StructuredBenefit | dict[str, Any] | str


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
    card_benefits: list[BenefitValue] | None = None
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
    card_benefits: list[BenefitValue] | None = Field(
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


_CATEGORY_CANONICAL = {
    "restaurant": "dining",
    "restaurants": "dining",
    "dining": "dining",
    "grocery": "groceries",
    "groceries": "groceries",
    "supermarket": "groceries",
    "supermarkets": "groceries",
    "travel": "travel",
    "airfare": "travel",
    "flight": "travel",
    "flights": "travel",
    "hotel": "travel",
    "hotels": "travel",
    "gas": "gas",
    "fuel": "gas",
    "everyday": "everyday",
    "every purchase": "everyday",
    "all purchases": "everyday",
    "other": "everyday",
}

BENEFIT_DISCLOSURE_NOISE = (
    "while we don't cover all available",
    "we don't cover all available",
    "editorial content is not influenced",
    "not influenced by nor subject to review",
    "subject to review by any credit card company",
    "credit card company, bank or partner",
    "our editorial team creates and maintains",
)

_BENEFIT_NOISE = (
    "[json-ld]",
    "@context",
    "schema.org",
    "aggregaterating",
    "breadcrumblist",
    "feesandcommissionsspecification",
    "pay over time",
    "payment plan",
    "at checkout",
    "credit card members may have the option",
    "orders totaling",
    "break up credit card purchases",
    "start a plan",
    "pricing and terms",
    "to learn more",
    "please visit",
    "rates and fees",
    "terms and conditions",
    "terms apply",
    "privacy",
    "cookie",
    "doesn't include",
    "does not include",
    "not all offers",
    "no longer available",
    *BENEFIT_DISCLOSURE_NOISE,
)


def _clean_public_text(value: Any, limit: int = 180) -> str | None:
    text = str(value or "")
    text = re.sub(r"\[(?:text|title|meta|json-ld|next-data|table)\]\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _benefit_frequency(text: str | None) -> str:
    low = (text or "").lower()
    if "anniversary" in low or "cardmember year" in low:
        return "anniversary"
    if "semiannual" in low or "semi-annual" in low or "january through june" in low or "july through december" in low:
        return "semiannual"
    if "monthly" in low or "each month" in low or "per month" in low:
        return "monthly"
    if "quarterly" in low or "each quarter" in low:
        return "quarterly"
    if "annual" in low or "each year" in low or "per year" in low:
        return "annual"
    if "one-time" in low or "once" in low:
        return "one_time"
    if "lounge" in low or "status" in low or "membership" in low or "protection" in low:
        return "ongoing"
    return "unknown"


def _benefit_category(text: str | None) -> str | None:
    low = (text or "").lower()
    for category, terms in {
        "travel": ("travel", "global entry", "tsa precheck", "clear", "rental car", "trip", "lounge"),
        "dining": ("dining", "restaurant", "resy", "uber", "dunkin"),
        "hotel": ("hotel", "free night", "resort"),
        "airline": ("airline", "flight", "checked bag", "priority boarding"),
        "shopping": ("purchase protection", "cell phone", "walmart", "saks", "instacart"),
        "anniversary": ("anniversary",),
    }.items():
        if any(term in low for term in terms):
            return category
    return None


def _benefit_value(text: str | None) -> str | None:
    if not text:
        return None
    cash = re.search(r"\$[\d,]+(?:\.\d+)?", text)
    if cash:
        return cash.group(0)
    points = re.search(r"\b(\d{1,3}(?:,\d{3})+|\d{4,6})\s+(points?|miles?|skymiles|avios)\b", text, re.I)
    if points:
        return f"{points.group(1)} {points.group(2)}"
    return None


def _short_benefit_name(text: str, value: str | None, frequency: str, category: str | None) -> str:
    low = text.lower()
    if "global entry" in low or "tsa precheck" in low:
        return "Global Entry/TSA PreCheck credit"
    if "priority pass" in low:
        return "Priority Pass lounge access"
    if "capital one lounge" in low:
        return "Capital One lounge access"
    if "lounge" in low:
        return "airport lounge access"
    if "uber" in low:
        return "Uber Cash"
    if "resy" in low:
        return "Resy credit"
    if "dunkin" in low:
        return "Dunkin credit"
    if "travel" in low and "credit" in low:
        return f"{value or ''} travel credit".strip()
    if "hotel" in low and "credit" in low:
        return f"{value or ''} hotel credit".strip()
    if "dining" in low and "credit" in low:
        return f"{value or ''} dining credit".strip()
    if "anniversary" in low and value:
        return f"{value} anniversary bonus"
    if "anniversary" in low:
        return "anniversary bonus"
    text = re.split(r"[.;|]", text, maxsplit=1)[0]
    text = re.sub(r"\b(up to|statement|benefit|enrollment required|terms apply)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|.")
    if value and "credit" in low and value not in text:
        text = f"{value} {text}".strip()
    if len(text) > 56:
        text = text[:53].rstrip() + "..."
    if not text and category:
        text = f"{frequency if frequency != 'unknown' else ''} {category} benefit".strip()
    return text or "card benefit"


def _normalize_benefit_item(item: BenefitValue) -> dict[str, Any] | None:
    if isinstance(item, StructuredBenefit):
        item = item.model_dump(mode="json", exclude_none=True)
    if isinstance(item, dict):
        raw_text = " ".join(
            str(item.get(key) or "")
            for key in ("name", "benefit", "title", "description", "notes", "detail", "value", "evidence")
        )
        clean_text = _clean_public_text(raw_text, 500)
        if not clean_text or any(token in clean_text.lower() for token in _BENEFIT_NOISE):
            return None
        raw_name = _clean_public_text(
            item.get("name") or item.get("benefit") or item.get("title") or clean_text,
            80,
        )
        raw_value = _clean_public_text(item.get("value") or item.get("annual_value") or item.get("amount"), 60)
        frequency = str(item.get("frequency") or item.get("cadence") or _benefit_frequency(clean_text)).strip().lower()
        if frequency not in {"monthly", "quarterly", "semiannual", "annual", "anniversary", "one_time", "ongoing", "unknown"}:
            frequency = _benefit_frequency(clean_text)
        category = _clean_public_text(item.get("category") or item.get("type") or _benefit_category(clean_text), 40)
        evidence = _clean_public_text(item.get("evidence") or item.get("source_snippet") or clean_text, 220)
        name_seed = " ".join(part for part in (raw_name, clean_text) if part)
        name = _short_benefit_name(name_seed or clean_text, raw_value or _benefit_value(clean_text), frequency, category)
        out: dict[str, Any] = {
            "name": name,
            "value": raw_value or _benefit_value(clean_text),
            "frequency": frequency,
            "category": category,
            "description": _clean_public_text(item.get("description") or item.get("notes") or item.get("detail"), 180),
            "evidence": evidence,
        }
        confidence = item.get("confidence")
        try:
            if confidence is not None:
                out["confidence"] = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            pass
    else:
        clean_text = _clean_public_text(item, 500)
        if not clean_text or any(token in clean_text.lower() for token in _BENEFIT_NOISE):
            return None
        value = _benefit_value(clean_text)
        frequency = _benefit_frequency(clean_text)
        category = _benefit_category(clean_text)
        out = {
            "name": _short_benefit_name(clean_text, value, frequency, category),
            "value": value,
            "frequency": frequency,
            "category": category,
            "description": clean_text if len(clean_text) <= 180 else None,
            "evidence": clean_text[:220],
        }
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def _normalize_card_benefits(items: list[BenefitValue] | None) -> list[dict[str, Any]] | None:
    if not items:
        return None
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        benefit = _normalize_benefit_item(item)
        if not benefit:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", f"{benefit.get('name')} {benefit.get('value')} {benefit.get('frequency')}".lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(benefit)
        if len(out) >= 12:
            break
    return out or None


def _normalize_category_key(value: str | None) -> str | None:
    low = " ".join(str(value or "").lower().split())
    if not low:
        return None
    for term, category in _CATEGORY_CANONICAL.items():
        if term in low:
            return category
    return re.sub(r"[^a-z0-9_]+", "_", low).strip("_")[:40] or None


def _normalize_earn_multipliers(value: dict[str, float] | None) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, float] = {}
    for raw_category, raw_multiplier in value.items():
        category = _normalize_category_key(raw_category)
        if not category:
            continue
        try:
            multiplier = float(raw_multiplier)
        except (TypeError, ValueError):
            continue
        if multiplier <= 0 or multiplier > 25:
            continue
        out[category] = max(out.get(category, 0), multiplier)
    return out or None


def _normalize_best_category_uses(
    value: dict[str, str] | None,
    earn_multipliers: dict[str, float] | None,
) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for category, multiplier in (earn_multipliers or {}).items():
        out[category] = f"{multiplier:g}x"
    if isinstance(value, dict):
        for raw_category, raw_note in value.items():
            category = _normalize_category_key(raw_category)
            note = _clean_public_text(raw_note, 80)
            if not category or not note:
                continue
            note = re.sub(r"\b(\d+(?:\.\d+)?x)\s*[-–]\s*\1\b", r"\1", note, flags=re.I)
            out[category] = note
    return out or None


def normalize_offer_scan_row(row: OfferScanRow) -> OfferScanRow:
    """Clean LLM/static supplemental fields into a stable PUBLIC catalog shape."""
    row.earn_multipliers = _normalize_earn_multipliers(row.earn_multipliers)
    row.best_category_uses = _normalize_best_category_uses(row.best_category_uses, row.earn_multipliers)
    row.card_benefits = _normalize_card_benefits(row.card_benefits)
    if row.card_benefits:
        evidence = []
        for benefit in row.card_benefits:
            snippet = benefit.get("evidence") if isinstance(benefit, dict) else None
            if snippet:
                evidence.append(str(snippet))
        if evidence:
            row.evidence_snippets["card_benefits"] = list(dict.fromkeys(evidence[:8]))
    if row.earn_multipliers:
        row.evidence_snippets.setdefault("earn_multipliers", row.evidence_snippets.get("best_category_uses", []))
    return row


def has_unstructured_supplemental(row: OfferScanRow | None) -> bool:
    if row is None:
        return False
    benefits = row.card_benefits or []
    has_unstructured_benefits = any(isinstance(item, str) for item in benefits)
    has_use_without_multiplier = bool(row.best_category_uses) and not row.earn_multipliers
    return has_unstructured_benefits or has_use_without_multiplier


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
        "issuer, card_name, product_url, source_url, "
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
        "- earn_multipliers is a normalized object like {\"dining\": 4, \"travel\": 3}; use categories "
        "dining, groceries, travel, gas, everyday when possible.\n"
        "- best_category_uses is short user-facing category guidance, never duplicated text like '4x-4x'. "
        "For plain earn rates, use compact values such as {\"dining\": \"4x\"}.\n"
        "- card_benefits must be a clean array of objects, not raw page fragments. Each object should use "
        "{name, value, frequency, category, description, evidence, confidence}. frequency must be one of "
        "monthly, quarterly, semiannual, annual, anniversary, one_time, ongoing, unknown. Include ALL notable "
        "public perks/credits/anniversary benefits, especially recurring monthly/semiannual/annual credits, "
        "lounge access, free night/anniversary bonuses, Uber/Resy/dining/travel credits. Keep names short "
        "for UI display, e.g. '$300 travel credit', 'Uber Cash', 'Priority Pass lounge access'. "
        "Do not include JSON-LD/title/meta boilerplate, payment-plan text, article navigation, or generic terms text. "
        "downgrade_paths lists product-change targets.\n"
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
            max_tokens=min(4000, 600 + len(cards) * 450),
            messages=[{"role": "user", "content": prompt}],
            output_format=OfferScanBatchResult,
        )
    except anthropic.APIError as exc:
        if _is_schema_limit_error(exc):
            fallback_prompt = (
                f"{prompt}\n\n"
                "The strict schema parser is unavailable for this request. Return ONLY valid JSON with this shape:\n"
                "{\"rows\":[{\"issuer\":\"...\",\"card_name\":\"...\",\"found\":true,\"confidence\":0.0}]}\n"
                "Include every relevant field from the requested row shape when supported by snippets. "
                "Use null or omit unsupported fields. No markdown, no prose."
            )
            try:
                fallback = client.messages.create(
                    model=config.ANTHROPIC_MODEL,
                    max_tokens=min(4000, 600 + len(cards) * 450),
                    messages=[{"role": "user", "content": fallback_prompt}],
                )
            except anthropic.APIError as fallback_exc:
                raise _provider_unavailable(fallback_exc) from fallback_exc
            try:
                return _parse_offer_scan_batch_json(_message_text(fallback))
            except Exception as parse_exc:
                raise IngestionUnavailable(f"Anthropic JSON fallback parse failed: {parse_exc}") from parse_exc
        raise _provider_unavailable(exc) from exc
    result = resp.parsed_output
    return [normalize_offer_scan_row(row) for row in result.rows] if result else []


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
        "- 'best_category_uses' = {category: short note} for travel/dining/groceries/gas/everyday; keep it compact "
        "and never duplicate values like '4x-4x'.\n"
        "- 'card_benefits' = clean benefit objects with {name, value, frequency, category, description, evidence, confidence}. "
        "Include all notable perks/credits/anniversary benefits, including recurring monthly/semiannual/annual "
        "credits and lounge/free-night/anniversary point benefits. Do not include raw JSON-LD/title/meta/article boilerplate; "
        "'downgrade_paths' = product-change targets.\n"
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
    ext = resp.parsed_output
    if ext and ext.card_benefits:
        ext.card_benefits = _normalize_card_benefits(ext.card_benefits)
    if ext and ext.earn_multipliers:
        normalized = _normalize_earn_multipliers({item.category: item.multiplier for item in ext.earn_multipliers})
        ext.earn_multipliers = [EarnMultiplier(category=k, multiplier=v) for k, v in (normalized or {}).items()] or None
        ext.best_category_uses = _normalize_best_category_uses(ext.best_category_uses, normalized)
    return ext


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


def _tool_payload(message: Any) -> dict[str, Any]:
    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use":
            return getattr(block, "input", {}) or {}
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("input", {}) or {}
    raise IngestionUnavailable("LLM response did not contain the required tool-use payload.")
