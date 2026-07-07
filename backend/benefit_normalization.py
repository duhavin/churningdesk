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
from .text_sanitize import clean_benefit_value, clean_text, looks_like_scrape_junk

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
    "see rates and fees",
    "highest level of additional benefits",
    "how bilt points work",
    "related:",
    "keep in mind that the other",
    "cash back is earned in the form of thankyou points",
    "can be redeemed for cash back as a direct deposit",
    "instant credit limit",
    "valid doordash account",
    "must have or create",
    "you may be eligible for as high as",
    "new cardmember offer",
    "refer business owners",
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
    "s) and",
    "cash back is earned",
    "instant credit limit",
    "(instant credit limit",
    "top-tier",
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
    # Truncated scrape fragments are never valid names — no $-led exemption
    # ("$95 The Atmos Rewards Ascent Visa Signature credit..." was junk too).
    if "..." in name or "…" in name:
        return True
    if "after" in low and "spend" in low:
        return True
    # Marketing sentences welded onto a value ("$240 Business credit: Get $20
    # statement credit per month on: Fe…") — a benefit NAME never pitches.
    if re.search(r":\s*get\b", low) or re.search(r"\bget\s+\$", low):
        return True
    if looks_like_scrape_junk(name):
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
    """Canonical concept key: NAME only, punctuation/amount-format agnostic.

    'Global Entry/TSA PreCheck credit' + value '$120' and the same name with
    value '$120,' are one benefit — value/frequency stay out of the key so
    formatting drift can't duplicate rows.
    """
    name = item.get("name") if isinstance(item, dict) else item
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _richness(item: Any) -> int:
    if not isinstance(item, dict):
        return 0
    score = 0
    if item.get("value"):
        score += 4
    if item.get("frequency") not in (None, "", "unknown"):
        score += 2
    if item.get("category"):
        score += 1
    if item.get("description"):
        score += 1
    return score


_GENERIC_BENEFIT_NAMES = {
    "travel credit",
    "hotel credit",
    "dining credit",
    "airline credit",
    "statement credit",
    "credit",
    "benefit",
}

_DESCRIPTION_MAX_CHARS = 300


def _clean_description(text: Any) -> str | None:
    """Descriptions are optional color — salvage the benefit, drop the noise."""
    cleaned = clean_text(text)
    if not cleaned:
        return None
    # A stored mid-text ellipsis is truncation damage from an earlier scrape;
    # keep only the complete sentences before it.
    for marker in ("...", "…"):
        if marker in cleaned:
            head = cleaned.split(marker, 1)[0]
            cut = head.rfind(". ")
            cleaned = head[: cut + 1].strip() if cut >= 60 else ""
            break
    if not cleaned:
        return None
    if looks_like_scrape_junk(cleaned) or is_benefit_noise(cleaned):
        return None
    if len(cleaned) > _DESCRIPTION_MAX_CHARS:
        # Trim at the last full sentence that fits; else drop (a truncated
        # disclosure wall is worse than no description).
        head = cleaned[:_DESCRIPTION_MAX_CHARS]
        cut = head.rfind(". ")
        if cut < 60:
            return None
        cleaned = head[: cut + 1]
    return cleaned


def _structured_existing(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    out: list[Any] = []
    for item in items:
        is_structured = isinstance(item, dict)
        if is_structured:
            name = clean_text(item.get("name") or item.get("benefit") or item.get("title"))
            value = clean_benefit_value(item.get("value") or item.get("annual_value") or item.get("amount"))
            frequency = clean_text(item.get("frequency") or item.get("cadence")) or "unknown"
            category = clean_text(item.get("category") or item.get("type")) or None
            description = _clean_description(item.get("description") or item.get("notes") or item.get("detail"))
            evidence = _clean_description(item.get("evidence") or item.get("source_snippet"))
            confidence = item.get("confidence")
        else:
            name = clean_text(item)
            value = None
            frequency = "unknown"
            category = None
            description = None
            evidence = None
            confidence = None
        if not name:
            continue
        if _is_bad_benefit_name(name):
            continue
        # Noise check on the NAME (plus value/frequency), not the whole row:
        # a real "$600 hotel credit" must survive a junk description — the
        # description was already salvaged/dropped above.
        if is_benefit_noise(f"{name} {value or ''} {frequency}"):
            continue
        if not is_structured and is_benefit_noise(item):
            continue
        # Long $-led raw fragments stay: the benefit tracker summarizes them
        # into short labels downstream. Everything else long is scrape spill.
        money_led = bool(re.match(r"^\$\s*[1-9][0-9,]*(?:\.\d+)?", name)) and _has_benefit_signal(name)
        if not is_structured and len(name) > 170 and not money_led:
            continue
        if (is_structured and len(name) > 96) or not _has_benefit_signal(name):
            continue
        # Specificity gate: a generic label with no value, no cadence, and no
        # category is scrape residue, not a trackable benefit.
        if (
            _norm(name) in _GENERIC_BENEFIT_NAMES
            and not value
            and frequency in ("", "unknown")
            and not category
        ):
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
    """One row per benefit concept; on collision keep the richer entry."""
    by_key: dict[str, Any] = {}
    order: list[str] = []
    for item in items:
        key = _benefit_key(item)
        if not key:
            continue
        if key not in by_key:
            by_key[key] = item
            order.append(key)
        elif _richness(item) > _richness(by_key[key]):
            by_key[key] = item
    return [by_key[key] for key in order]


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


def _chase_shared_protections(text: str, allow_reference: bool) -> list[dict[str, Any]]:
    """Purchase/warranty/trip protections shared by Chase Freedom-family cards."""
    benefits: list[dict[str, Any]] = []
    if allow_reference or "purchase protection" in text:
        benefits.append(
            _benefit(
                "Purchase protection",
                None,
                "ongoing",
                "protection",
                "Covers new purchases against damage or theft for 120 days, up to $500 per claim.",
                "Purchase protection covers eligible purchases for 120 days.",
            )
        )
    if allow_reference or "extended warranty" in text:
        benefits.append(
            _benefit(
                "Extended warranty",
                None,
                "ongoing",
                "protection",
                "Extends eligible U.S. manufacturer warranties of three years or less by one additional year.",
                "Extended warranty protection on eligible purchases.",
            )
        )
    if allow_reference or "trip cancellation" in text:
        benefits.append(
            _benefit(
                "Trip cancellation/interruption insurance",
                None,
                "ongoing",
                "travel",
                "Reimbursement for eligible prepaid, non-refundable travel when a covered situation cancels or cuts the trip short.",
                "Trip cancellation and interruption insurance on eligible bookings.",
            )
        )
    return benefits


def _freedom_family_benefits(source_url: str | None, text: str, allow_reference: bool, *, flex: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    if not host.endswith("chase.com"):
        return []
    benefits: list[dict[str, Any]] = []
    if allow_reference or _has_any(text, ("dashpass", "doordash")):
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
                "$10 quarterly DoorDash promo",
                "$10",
                "quarterly",
                "dining",
                "Quarterly DoorDash promo credit for DashPass members on eligible orders.",
                "DashPass members receive a $10 quarterly promo credit.",
            )
        )
    if flex and (allow_reference or "cell phone" in text):
        benefits.append(
            _benefit(
                "Cell phone protection",
                "$800",
                "ongoing",
                "protection",
                "Covers damage or theft up to $800 per claim (two claims per 12 months, $50 deductible) when the monthly bill is paid with the card.",
                "Cell phone protection up to $800 per claim when you pay your monthly bill with the card.",
            )
        )
    benefits.extend(_chase_shared_protections(text, allow_reference))
    return benefits


def _ink_premier_benefits(source_url: str | None, text: str, allow_reference: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    if not host.endswith("chase.com"):
        return []
    benefits: list[dict[str, Any]] = []
    if allow_reference or "cell phone" in text:
        benefits.append(
            _benefit(
                "Cell phone protection",
                "$1,000",
                "ongoing",
                "protection",
                "Covers damage or theft up to $1,000 per claim (three claims per 12 months, $100 deductible) when the monthly bill is paid with the card.",
                "Cell phone protection up to $1,000 per claim when the monthly bill is paid with the card.",
            )
        )
    if allow_reference or "purchase protection" in text:
        benefits.append(
            _benefit(
                "Purchase protection",
                None,
                "ongoing",
                "protection",
                "Covers new purchases against damage or theft for 120 days, up to $10,000 per claim.",
                "Purchase protection for eligible business purchases.",
            )
        )
    if allow_reference or "extended warranty" in text:
        benefits.append(
            _benefit(
                "Extended warranty",
                None,
                "ongoing",
                "protection",
                "Extends eligible manufacturer warranties of three years or less by one additional year.",
                "Extended warranty protection on eligible purchases.",
            )
        )
    return benefits


def _amex_business_gold_benefits(source_url: str | None, text: str, allow_reference: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    if not host.endswith("americanexpress.com"):
        return []
    benefits: list[dict[str, Any]] = []
    if allow_reference or _has_any(text, ("fedex", "grubhub", "office supply", "$240 business credit", "flexible business credit")):
        benefits.append(
            _benefit(
                "$20 monthly flexible business credit",
                "$20",
                "monthly",
                "business",
                "Monthly statement credit on eligible U.S. purchases at FedEx, Grubhub, and office supply stores; enrollment required.",
                "$240 Flexible Business Credit: up to $20 in statement credits each month at FedEx, Grubhub, and office supply stores.",
            )
        )
    if allow_reference or _has_any(text, ("hotel collection", "two-night minimum")):
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
    if allow_reference or "extended warranty" in text:
        benefits.append(
            _benefit(
                "Extended warranty",
                None,
                "ongoing",
                "protection",
                "Extends eligible manufacturer warranties on covered purchases.",
                "Extended warranty on eligible purchases.",
            )
        )
    if allow_reference or "purchase protection" in text:
        benefits.append(
            _benefit(
                "Purchase protection",
                None,
                "ongoing",
                "protection",
                "Covers eligible purchases against accidental damage or theft for 90 days.",
                "Purchase protection on eligible purchases.",
            )
        )
    return benefits


def _sapphire_reserve_business_benefits(source_url: str | None, text: str, allow_reference: bool) -> list[dict[str, Any]]:
    host = _source_host(source_url)
    if not host.endswith("chase.com"):
        return []
    benefits: list[dict[str, Any]] = []
    if allow_reference or _has_any(text, ("$300 annual travel credit", "annual travel credit", "$300 travel")):
        benefits.append(
            _benefit(
                "$300 annual travel credit",
                "$300",
                "annual",
                "travel",
                "Annual statement credit automatically applied to travel purchases.",
                "$300 Annual Travel Credit on travel purchases each account anniversary year.",
            )
        )
    if allow_reference or "the edit" in text:
        benefits.append(
            _benefit(
                "$250 semiannual The Edit hotel credit",
                "$250",
                "semiannual",
                "hotel",
                "Statement credit for prepaid The Edit hotel bookings, split into January-June and July-December halves ($500 total per year).",
                "Up to $500 annually ($250 semiannually) for prepaid hotel bookings through The Edit.",
            )
        )
    if allow_reference or _has_any(text, ("priority pass", "sapphire lounge", "lounge access")):
        benefits.append(
            _benefit(
                "Airport lounge access",
                None,
                "membership",
                "travel",
                "Chase Sapphire Lounge by The Club locations plus Priority Pass Select membership after enrollment.",
                "Complimentary access to Chase Sapphire Lounges and Priority Pass Select.",
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
    if allow_reference or _has_any(text, ("dashpass", "doordash")):
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
    if allow_reference or "lyft" in text:
        benefits.append(
            _benefit(
                "$10 monthly Lyft credit",
                "$10",
                "monthly",
                "travel",
                "Monthly in-app Lyft credit plus elevated points on eligible Lyft rides through the issuer end date.",
                "$10 in-app Lyft credit each month with eligible rides.",
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
    builders = {
        ("american_express", "amex_gold"): _amex_gold_benefits,
        ("american_express", "amex_business_gold"): _amex_business_gold_benefits,
        ("chase", "chase_sapphire_preferred"): _sapphire_preferred_benefits,
        ("chase", "chase_sapphire_business"): _sapphire_reserve_business_benefits,
        ("chase", "chase_ink_premier"): _ink_premier_benefits,
        ("capital_one", "capital_one_venture_x_personal"): _venture_x_benefits,
    }
    known: list[dict[str, Any]] = []
    if variant == ("chase", "chase_freedom_unlimited"):
        known = _freedom_family_benefits(source_url, text, allow_reference, flex=False)
    elif variant == ("chase", "chase_freedom_flex"):
        known = _freedom_family_benefits(source_url, text, allow_reference, flex=True)
    elif variant in builders:
        known = builders[variant](source_url, text, allow_reference)
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
