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

BENEFIT_DISCLOSURE_NOISE = (
    "while we don't cover all available",
    "we don't cover all available",
    "editorial content is not influenced",
    "not influenced by nor subject to review",
    "subject to review by any credit card company",
    "credit card company, bank or partner",
    "our editorial team creates and maintains",
    "rates and fees",
    "terms and conditions",
    "terms apply",
    "to learn more",
    "please visit",
    "privacy",
    "cookie",
    "doesn't include",
    "does not include",
    "not all offers",
    "no longer available",
    "welcome offer",
    "welcome bonus",
    "new cardmember offer",
    "new cardmember",
    "sign-up bonus",
    "signup bonus",
    "best sign-up",
    "best signup",
    "our best offer",
    "best new business credit card",
    "opens offer details",
    "apply to know if",
    "find out your exact",
    "if you're approved",
    "if you are approved",
    "interest rates",
    "interest rates & charges",
    "pricing details",
    "introductory rate",
    "balance transfers",
    "credit card review",
    "card review",
    "contents best",
    "read our review",
    "current credit card sign bonuses",
    "best current credit card",
    "if you already have any",
    "creditworthiness",
    "variable apr",
    "apr for purchases",
    "flex plans",
    "credit score may be impacted",
    "please review",
    "guide to benefits",
    "original post",
    "launched today",
    "these introductory and promotional",
    "not everyone will qualify",
    "cardmember offer",
    "earn more than ever",
    "same low annual fee",
)

BENEFIT_SIGNAL_TERMS = (
    "statement credit",
    "travel credit",
    "hotel credit",
    "dining credit",
    "airline credit",
    "flight credit",
    "rideshare credit",
    "uber cash",
    "bilt cash",
    "coupon book",
    "lounge",
    "priority pass",
    "global entry",
    "tsa precheck",
    "nexus",
    "clear",
    "companion",
    "free night",
    "anniversary",
    "dashpass",
    "doordash",
    "uber",
    "resy",
    "dunkin",
    "instacart",
    "lyft",
    "checked bag",
    "preferred boarding",
    "award discount",
    "cell phone protection",
    "purchase protection",
    "extended warranty",
    "insurance",
    "wi-fi",
    "wifi",
)

BENEFIT_DISQUALIFY_TERMS = (
    "credit card",
    "creditworthiness",
    "variable apr",
    "introductory rate",
    "balance transfer",
    "after you spend",
    "welcome",
    "new cardmember",
    "apply",
    "approved",
    "pricing",
    "interest",
    "review",
    "original post",
    "launched today",
    "credit score",
    "redeem for cash back",
)

BENEFIT_NAME_PREFIX_NOISE = (
    "one credit",
    "the credit",
    "you can receive",
    "after that",
    "but ",
    "please review",
    "the american express",
    "1x ",
    "2x ",
    "3x ",
    "4x ",
    "5x ",
    "card has",
    "wyndham rewards",
    "ink business",
    "sapphire reserve",
    "both the",
    "free nights can require",
    "individuals whose",
)


def _norm(value: Any) -> str:
    text = str(value or "").lower().replace("\u2018", "'").replace("\u2019", "'")
    return " ".join(text.split())


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


def is_benefit_noise(value: Any) -> bool:
    if isinstance(value, dict):
        low = _norm(
            " ".join(
                str(value.get(key) or "")
                for key in (
                    "name",
                    "benefit",
                    "title",
                    "value",
                    "frequency",
                    "category",
                    "description",
                    "notes",
                    "detail",
                    "evidence",
                    "source_snippet",
                )
            )
        )
    else:
        low = _norm(value)
    if not low:
        return True
    if "[text]" in low:
        return True
    if not isinstance(value, dict) and ('\\"name\\"' in low or '"name":' in low or "https://" in low):
        return True
    if re.search(r"earn\s+[0-9,]+\s+(?:bonus\s+)?(?:points|miles)", low) and "anniversary" not in low:
        return True
    if "after you spend" in low and any(term in low for term in ("points", "miles", "bonus", "cash back")):
        return True
    return any(
        token in low
        for token in (
            "[json-ld]",
            "@context",
            "schema.org",
            "aggregaterating",
            "breadcrumblist",
            "feesandcommissionsspecification",
            "[title]",
            "[meta]",
            "pay over time",
            "payment plan",
            "at checkout",
            "orders totaling",
            "break up credit card purchases",
            "start a plan",
            "pricing and terms",
            *BENEFIT_DISCLOSURE_NOISE,
        )
    )


def _has_benefit_signal(text: str) -> bool:
    low = _norm(text)
    if not low:
        return False
    if any(term in low for term in BENEFIT_DISQUALIFY_TERMS):
        allowed_exceptions = (
            "statement credit",
            "travel credit",
            "hotel credit",
            "dining credit",
            "airline credit",
            "flight credit",
            "rideshare credit",
            "global entry",
            "tsa precheck",
            "priority pass",
            "lounge",
            "companion",
            "free night",
            "checked bag",
            "purchase protection",
            "extended warranty",
        )
        if not any(term in low for term in allowed_exceptions):
            return False
    if any(term in low for term in BENEFIT_SIGNAL_TERMS):
        return True
    has_money = bool(re.search(r"\$\s*[1-9][0-9,]*(?:\.\d+)?", low))
    if has_money and any(
        term in low
        for term in (
            "credit",
            "coupon",
            "pass",
            "lounge",
            "companion",
            "protection",
            "insurance",
            "benefit",
        )
    ):
        return True
    if re.search(r"\b[1-9][0-9,]*\s+(?:points|miles)\b", low) and any(
        term in low for term in ("anniversary", "companion", "award")
    ):
        return True
    return False


def _is_bad_benefit_name(name: str) -> bool:
    low = _norm(name)
    if not low:
        return True
    if any(low.startswith(prefix) for prefix in BENEFIT_NAME_PREFIX_NOISE):
        return True
    if re.match(r"^\d{1,2},\s+\d{4}\b", low):
        return True
    if "..." in name and not low.startswith("$"):
        return True
    if "after" in low and "spend" in low:
        return True
    if re.search(r"\$\s*[1-9][0-9]{0,2}(?:,[0-9]{3})+[^.]{0,40}\bcredit\b", low):
        return True
    if re.search(r"\$\s*[1-9][0-9]{3,}[^.]{0,40}\bcredit\b", low):
        return True
    if low == "anniversary bonus":
        return True
    return False


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


def _benefit_key(item: Any) -> str:
    if not isinstance(item, dict):
        return re.sub(r"[^a-z0-9]+", " ", str(item or "").lower()).strip()
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        f"{item.get('name')} {item.get('value')} {item.get('frequency')}".lower(),
    ).strip()


def _structured_existing(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    out: list[Any] = []
    for item in items:
        is_structured = isinstance(item, dict)
        if is_structured:
            name = str(item.get("name") or item.get("benefit") or item.get("title") or "").strip()
            value = item.get("value") or item.get("annual_value") or item.get("amount")
            frequency = item.get("frequency") or item.get("cadence") or "unknown"
            category = item.get("category") or item.get("type")
            description = item.get("description") or item.get("notes") or item.get("detail")
            evidence = item.get("evidence") or item.get("source_snippet")
            confidence = item.get("confidence")
        else:
            name = str(item or "").strip()
            value = None
            frequency = "unknown"
            category = None
            description = None
            evidence = None
            confidence = None
        if not name:
            continue
        if is_benefit_noise(item):
            continue
        if _is_bad_benefit_name(name):
            continue
        if not is_structured and len(name) > 170:
            continue
        name_signal = _has_benefit_signal(name)
        if (is_structured and len(name) > 96) or not name_signal:
            continue
        if not is_structured:
            out.append(name)
            continue
        cleaned = {
            "name": name,
            "value": value,
            "frequency": frequency,
            "category": category,
            "description": description,
            "evidence": evidence,
            "confidence": confidence,
        }
        out.append({key: value for key, value in cleaned.items() if value not in (None, "", [], {})})
    return out


def _dedupe(items: list[Any]) -> list[Any]:
    merged: list[Any] = []
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
        return _dedupe(_structured_existing(items)) or None

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
