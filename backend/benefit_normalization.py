"""PUBLIC benefit normalization for known issuer-source patterns.

These helpers do not read private household state. They convert verified public
source text into compact, trackable benefit objects so UI surfaces do not depend
on raw issuer page fragments.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .product_identity import product_variant_key


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _source_host(source_url: str | None) -> str:
    return (urlparse(source_url or "").hostname or "").lower().removeprefix("www.")


def _raw_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    pieces: list[str] = []
    for item in items:
        if isinstance(item, dict):
            pieces.extend(str(item.get(key) or "") for key in ("name", "value", "frequency", "category", "description", "evidence"))
        else:
            pieces.append(str(item or ""))
    return _norm(" ".join(pieces))


def _benefit(
    name: str,
    value: str | None,
    frequency: str,
    category: str,
    description: str,
    evidence: str,
    confidence: float = 0.95,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "frequency": frequency,
        "category": category,
        "description": description,
        "evidence": evidence,
        "confidence": confidence,
    }
    if value:
        out["value"] = value
    return out


def _benefit_key(item: dict[str, Any]) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        f"{item.get('name')} {item.get('value')} {item.get('frequency')}".lower(),
    ).strip()


def _structured_existing(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("benefit") or "").strip()
        if not name:
            continue
        low = _norm(item)
        if any(token in low for token in ("[json-ld]", "@context", "[title]", "[meta]", "pay over time", "payment plan")):
            continue
        cleaned = {
            "name": name,
            "value": item.get("value") or item.get("annual_value") or item.get("amount"),
            "frequency": item.get("frequency") or item.get("cadence") or "unknown",
            "category": item.get("category") or item.get("type"),
            "description": item.get("description") or item.get("notes") or item.get("detail"),
            "evidence": item.get("evidence") or item.get("source_snippet"),
            "confidence": item.get("confidence"),
        }
        out.append({key: value for key, value in cleaned.items() if value not in (None, "", [], {})})
    return out


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = _benefit_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _amex_gold_benefits(source_url: str | None, text: str, allow_reference: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    official = host.endswith("americanexpress.com")
    if not official:
        return []
    allow = allow_reference or bool(text)
    if not allow:
        return []
    benefits: list[dict[str, Any]] = []

    if allow_reference or _has_any(text, ("dining credit", "grubhub", "buffalo wild wings", "five guys", "cheesecake factory", "wonder")):
        benefits.append(
            _benefit(
                "$10 monthly dining credit",
                "$10",
                "monthly",
                "dining",
                "Monthly statement credit at participating dining partners: Grubhub/Seamless, Buffalo Wild Wings, Five Guys, The Cheesecake Factory, and Wonder.",
                "$120 Dining Credit: earn up to $10 in statement credits monthly at participating partners.",
            )
        )
    if allow_reference or _has_any(text, ("uber cash", "uber account", "orders and rides")):
        benefits.append(
            _benefit(
                "$10 monthly Uber Cash",
                "$10",
                "monthly",
                "dining",
                "Monthly Uber Cash after adding the Gold Card to an Uber account; usable for U.S. Uber rides and orders.",
                "$120 Uber Cash: get $10 in Uber Cash each month.",
            )
        )
    if allow_reference or _has_any(text, ("resy", "january through june", "july through december")):
        benefits.append(
            _benefit(
                "$50 semiannual Resy credit",
                "$50",
                "semiannual",
                "dining",
                "Statement credit at qualifying U.S. Resy restaurants, split into the current January-June or July-December half-year.",
                "$100 Resy Credit: up to $50 January-June and up to $50 July-December.",
            )
        )
    if allow_reference or _has_any(text, ("dunkin", "dunkin'")):
        benefits.append(
            _benefit(
                "$7 monthly Dunkin credit",
                "$7",
                "monthly",
                "dining",
                "Monthly statement credit for eligible U.S. Dunkin purchases.",
                "$84 Dunkin Credit: earn up to $7 in monthly statement credits.",
            )
        )
    if allow_reference or _has_any(text, ("hotel collection", "eligible charges", "two-night minimum")):
        benefits.append(
            _benefit(
                "$100 Hotel Collection credit",
                "$100",
                "ongoing",
                "hotel",
                "Eligible charge credit on The Hotel Collection bookings through Amex Travel with a two-night minimum.",
                "The Hotel Collection: $100 credit toward eligible charges on qualifying bookings.",
            )
        )
    return benefits


def _sapphire_preferred_benefits(source_url: str | None, text: str, allow_reference: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    official = host.endswith("chase.com")
    if not official:
        return []
    allow = allow_reference or bool(text)
    if not allow:
        return []
    benefits: list[dict[str, Any]] = []

    if allow_reference or _has_any(text, ("chase travel hotel credit", "hotel stays purchased through chase travel")):
        benefits.append(
            _benefit(
                "$100 Chase Travel hotel credit",
                "$100",
                "cardmember_year",
                "hotel",
                "Statement credit each account anniversary year for hotel stays purchased through Chase Travel.",
                "$100 Chase Travel Hotel Credit each account anniversary year.",
            )
        )
    if allow_reference or _has_any(text, ("global entry", "tsa precheck", "nexus")):
        benefits.append(
            _benefit(
                "Global Entry/TSA/NEXUS credit",
                "$120",
                "one_time",
                "travel",
                "Application fee credit every four years for Global Entry, TSA PreCheck, or NEXUS.",
                "Up to $120 application fee credit every four years.",
            )
        )
    if allow_reference or _has_any(text, ("dashpass", "doordash", "$10 promo")):
        benefits.append(
            _benefit(
                "DashPass membership",
                None,
                "membership",
                "dining",
                "Complimentary DashPass membership when activated by the issuer deadline.",
                "Complimentary DashPass membership with eligible activation.",
            )
        )
        benefits.append(
            _benefit(
                "$10 monthly DoorDash promo",
                "$10",
                "monthly",
                "dining",
                "Monthly DashPass promo for groceries, retail orders, and more.",
                "DashPass members get a $10 promo each month.",
            )
        )
    if allow_reference or "apple tv" in text:
        benefits.append(
            _benefit(
                "Apple TV membership",
                None,
                "one_time",
                "streaming",
                "Complimentary Apple TV membership when activated by the issuer deadline.",
                "Apple TV benefit available with eligible activation.",
            )
        )
    if allow_reference or "lyft" in text:
        benefits.append(
            _benefit(
                "5x Lyft rides",
                None,
                "ongoing",
                "travel",
                "Earn elevated points on eligible Lyft rides through the issuer end date.",
                "Earn 5x total points on Lyft rides.",
            )
        )
    if allow_reference or "peloton" in text:
        benefits.append(
            _benefit(
                "5x Peloton purchases",
                None,
                "ongoing",
                "fitness",
                "Earn elevated points on eligible Peloton equipment and accessory purchases.",
                "Earn 5x total points on eligible Peloton purchases.",
            )
        )
    return benefits


def _venture_x_benefits(source_url: str | None, text: str, allow_reference: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    trusted = host.endswith("capitalone.com") or host.endswith("thepointsguy.com")
    if not trusted:
        return []
    allow = allow_reference or bool(text)
    if not allow:
        return []
    benefits: list[dict[str, Any]] = []

    if allow_reference or _has_any(text, ("$300", "annual travel credit", "capital one travel credit")):
        benefits.append(
            _benefit(
                "$300 Capital One Travel credit",
                "$300",
                "annual",
                "travel",
                "Annual statement credit for bookings made through Capital One Travel.",
                "$300 annual statement credit for select travel booked through Capital One Travel.",
            )
        )
    if allow_reference or _has_any(text, ("10,000 bonus miles", "10,000 anniversary", "account anniversary")):
        benefits.append(
            _benefit(
                "10k anniversary miles",
                "10,000 miles",
                "anniversary",
                "travel",
                "Anniversary bonus miles after each account anniversary while the account remains open.",
                "Cardholders receive 10,000 bonus miles every account anniversary.",
            )
        )
    if allow_reference or _has_any(text, ("global entry", "tsa precheck")):
        benefits.append(
            _benefit(
                "Global Entry/TSA credit",
                "$120",
                "one_time",
                "travel",
                "Application fee statement credit for Global Entry or TSA PreCheck.",
                "Statement credit of up to $120 for TSA PreCheck or Global Entry application fee.",
            )
        )
    if allow_reference or _has_any(text, ("priority pass", "capital one lounge", "lounge access")):
        benefits.append(
            _benefit(
                "Priority Pass",
                None,
                "membership",
                "travel",
                "Primary cardholder lounge access includes Capital One Lounges/Landings and Priority Pass participating lounges.",
                "Primary cardholder receives Priority Pass membership and access to Capital One lounge locations.",
            )
        )
    return benefits


def normalize_public_benefits(
    issuer: str | None,
    product_name: str | None,
    source_url: str | None,
    items: Any,
    *,
    allow_reference: bool = False,
) -> list[dict[str, Any]] | None:
    """Return compact public benefit objects for a known product/source.

    ``allow_reference`` is intended only for issuer-official pages that were
    recently verified but previously stored noisy snippets. In normal ingestion,
    the source text itself should trigger the rows.
    """
    variant = product_variant_key(issuer, product_name)
    text = _raw_text(items)
    known: list[dict[str, Any]] = []
    if variant == ("american_express", "amex_gold"):
        known = _amex_gold_benefits(source_url, text, allow_reference)
    elif variant == ("chase", "chase_sapphire_preferred"):
        known = _sapphire_preferred_benefits(source_url, text, allow_reference)
    elif variant == ("capital_one", "capital_one_venture_x_personal"):
        known = _venture_x_benefits(source_url, text, allow_reference)
    else:
        return None

    if known:
        return _dedupe(known)
    existing = _structured_existing(items)
    return _dedupe(existing) or None


def normalize_known_public_facts(
    issuer: str | None,
    product_name: str | None,
    source_url: str | None,
    *,
    allow_reference: bool = False,
) -> dict[str, Any] | None:
    """Return compact deterministic facts for known, recently verified products."""

    variant = product_variant_key(issuer, product_name)
    if variant == ("american_express", "amex_gold") and _source_host(source_url).endswith("americanexpress.com"):
        return {
            "annual_fee": 325.0,
            "earn_multipliers": {
                "dining": 4.0,
                "groceries": 4.0,
                "prepaid_hotels": 5.0,
                "flights": 3.0,
                "prepaid_car_rentals": 2.0,
                "everyday": 1.0,
            },
            "best_category_uses": {
                "dining": "4x restaurants worldwide",
                "groceries": "4x U.S. supermarkets",
                "travel": "5x prepaid hotels / 3x flights",
                "everyday": "1x",
            },
            "card_benefits": _amex_gold_benefits(source_url, "", allow_reference=True),
        }
    if variant == ("chase", "chase_sapphire_preferred") and _source_host(source_url).endswith("chase.com"):
        return {
            "annual_fee": 95.0,
            "earn_multipliers": {
                "travel": 5.0,
                "dining": 3.0,
                "gas": 3.0,
                "groceries": 3.0,
                "streaming": 3.0,
                "other_travel": 2.0,
                "everyday": 1.0,
            },
            "best_category_uses": {
                "travel": "5x Chase Travel / 2x other travel",
                "dining": "3x",
                "gas": "3x gas stations and EV charging",
                "groceries": "3x online grocery only",
                "everyday": "1x",
            },
            "card_benefits": _sapphire_preferred_benefits(source_url, "", allow_reference=True),
        }
    if variant == ("capital_one", "capital_one_venture_x_personal") and (
        _source_host(source_url).endswith("capitalone.com") or _source_host(source_url).endswith("thepointsguy.com")
    ):
        return {
            "annual_fee": 395.0,
            "earn_multipliers": {
                "travel": 10.0,
                "flights": 5.0,
                "vacation_rentals": 5.0,
                "everyday": 2.0,
            },
            "best_category_uses": {
                "travel": "10x hotels/rental cars; 5x flights via Capital One Travel",
                "everyday": "2x",
            },
            "card_benefits": _venture_x_benefits(source_url, "", allow_reference=True),
        }
    return None
