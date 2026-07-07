"""PUBLIC text sanitation: mojibake repair + scrape-artifact stripping.

Every string that ingestion stores on PUBLIC catalog rows should pass through
``clean_text`` (names, sources, notes, benefit names/descriptions, category
notes). This is the single place that knows about encoding damage and
marketing/nav cruft markers, so parsers and normalizers don't each grow their
own half-fixes.

Never used on PRIVATE household data — private fields are user-entered and
encrypted, not scraped.
"""
from __future__ import annotations

import re
from typing import Any

# UTF-8 bytes mis-decoded as cp1252 produce visible sequences like "â€™"
# for an apostrophe. Build the repair map from the actual transform (never
# typed by hand -- the sequences contain invisible characters), longest-first
# so multi-byte sequences win before their prefixes.
def _build_mojibake_map() -> list[tuple[str, str]]:
    targets = "‘’“”–—•… é®™"
    pairs: list[tuple[str, str]] = []
    for ch in targets:
        try:
            seq = ch.encode("utf-8").decode("cp1252")
        except UnicodeDecodeError:
            continue
        if seq != ch:
            pairs.append((seq, " " if ch in (" ", "•") else ch))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


_MOJIBAKE = _build_mojibake_map()

# Symbols that are legal but never useful in decision copy: trademark family,
# daggers, replacement char. ® ™ ℠ © † ‡ � — strip entirely.
_STRIP_CHARS = "®™℠©†‡�Ⓡ"

_WS_RE = re.compile(r"[ \t\f\v]+")


def clean_text(value: Any) -> str:
    """Repair mojibake, strip scrape symbols, collapse whitespace."""
    text = str(value or "")
    if not text:
        return ""
    for bad, good in _MOJIBAKE:
        if bad in text:
            text = text.replace(bad, good)
    # Stray "Â" from a double-encoded NBSP after the pair repairs above.
    text = text.replace("Â", "")
    text = text.translate({ord(ch): None for ch in _STRIP_CHARS})
    # Fancy quotes/dashes → plain equivalents (tabular copy stays clean).
    text = (
        text.replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .replace(" ", " ")
    )
    text = _WS_RE.sub(" ", text)
    # Whitespace before punctuation left behind by symbol stripping.
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return text.strip()


_MONEY_VALUE_RE = re.compile(
    r"^\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*$"
)
_POINTS_VALUE_RE = re.compile(
    r"^([0-9][0-9,]*)(?:\s*(?:k\b)?)\s*(points|miles|pts)?\s*$", re.IGNORECASE
)


def clean_benefit_value(value: Any) -> str | None:
    """Normalize a benefit 'value' token: '$120,' → '$120'; junk → None."""
    text = clean_text(value).rstrip(",.;:")
    if not text:
        return None
    m = _MONEY_VALUE_RE.match(text)
    if m:
        return f"${m.group(1)}"
    m = _POINTS_VALUE_RE.match(text)
    if m and m.group(2):
        return f"{m.group(1)} {m.group(2).lower()}"
    # "10,000 miles" style already matched; anything else that still contains
    # a dollar amount keeps just that amount.
    m = re.search(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?", text)
    if m:
        return m.group(0).replace(" ", "")
    return None


# --- structural junk detection -------------------------------------------

# Brand words that legitimately contain internal capitals — masked before the
# nav-concatenation check so "DoorDash" never reads as glued navigation text.
_CAMEL_BRANDS = (
    "DoorDash", "DashPass", "PreCheck", "ThankYou", "JetBlue", "OpenTable",
    "PayPal", "LifeMiles", "AAdvantage", "SkyMiles", "MileagePlus", "TrueBlue",
    "IHG", "McDonald", "iPhone", "eGift", "StubHub", "YouTube", "WiFi",
    "GrubHub", "Grubhub", "SoFi", "TSA", "NEXUS", "InCircle", "OneWorld",
    "ShopRunner", "GoPuff", "HelloFresh", "WSJ", "SiriusXM",
)

_DATELINE_RE = re.compile(r"^[A-Z][A-Z .]+,\s*[A-Z]{2}\b")  # "SEATTLE, WA —"
_NAV_JOIN_RE = re.compile(r"[a-z][A-Z]")

_MARKETING_PHRASES = (
    "apply today",
    "apply now",
    "learn more",
    "view all",
    "believes that",
    "present a new",
    "presents a new",
    "our popular",
    "long-time credit card",
    "is also getting",
    "award winning mobile app",
    "award-winning mobile app",
    "navigate membership",
    "financial education center",
    "credit intel",
    "press release",
    "transform lives",
    "helping you leverage",
    "designed for global",
    "enhanced, combined",
)

_DISCLOSURE_SENTENCE_STARTS = (
    "one credit will",
    "one statement credit will",
    "the credit will",
    "the statement credit will",
    "the actual amount",
    "credit will be posted",
    "redeem anytime",
    "individuals whose",
    "you must use",
    "to receive a statement credit",
    "the same low",
    "with atmos rewards",
    "seattle, wa",
)


def looks_like_scrape_junk(text: Any) -> bool:
    """True when text is navigation cruft, marketing copy, a press dateline,
    or a terms/disclosure fragment rather than a benefit fact."""
    raw = clean_text(text)
    if not raw:
        return True
    low = raw.lower()
    if "|" in raw:
        return True
    if _DATELINE_RE.match(raw):
        return True
    if any(phrase in low for phrase in _MARKETING_PHRASES):
        return True
    if any(low.startswith(start) for start in _DISCLOSURE_SENTENCE_STARTS):
        return True
    masked = raw
    for brand in _CAMEL_BRANDS:
        masked = masked.replace(brand, "X")
    if len(_NAV_JOIN_RE.findall(masked)) >= 2:
        return True  # "CenterBusiness Credit CardsView All…" glued navigation
    return False
