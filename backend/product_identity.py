"""Shared public product identity helpers."""
from __future__ import annotations

import re


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def normalized_key(value: str | None) -> str:
    return _norm(value).replace(" ", "_")


ISSUER_ALIASES = {
    "amex": "american_express",
    "american_express": "american_express",
    "capital_one": "capital_one",
    "chase": "chase",
    "jp_morgan": "chase",
    "jpmorgan": "chase",
    "citi": "citi",
    "citibank": "citi",
    "bank_of_america": "bank_of_america",
    "bofa": "bank_of_america",
    "wells_fargo": "wells_fargo",
    "u_s_bank": "us_bank",
    "us_bank": "us_bank",
}

TRANSFERABLE_CURRENCIES = {
    "Amex Membership Rewards",
    "Bilt Rewards",
    "Capital One Miles",
    "Chase Ultimate Rewards",
    "Citi ThankYou Points",
    "Wells Fargo Rewards",
}

AIRLINE_CURRENCIES = {
    "Alaska Mileage Plan",
    "American Airlines AAdvantage",
    "Atmos Rewards",
    "Delta SkyMiles",
    "HawaiianMiles",
    "JetBlue TrueBlue",
    "Southwest Rapid Rewards",
    "United MileagePlus",
}

HOTEL_CURRENCIES = {
    "Choice Privileges",
    "Hilton Honors",
    "IHG One Rewards",
    "Marriott Bonvoy",
    "World of Hyatt",
    "Wyndham Rewards",
}

CARD_DISPLAY_NAMES = {
    ("american_express", "amex_gold"): "Amex Gold",
    ("american_express", "amex_business_gold"): "Amex Business Gold",
    ("american_express", "amex_platinum"): "Amex Platinum",
    ("american_express", "amex_business_platinum"): "Amex Business Platinum",
    ("american_express", "amex_green"): "Amex Green",
    ("american_express", "amex_blue_business_plus"): "Blue Business Plus",
    ("american_express", "amex_everyday"): "Amex EveryDay",
    ("american_express", "amex_everyday_preferred"): "Amex EveryDay Preferred",
    ("capital_one", "capital_one_venture_x_personal"): "Venture X",
    ("capital_one", "capital_one_venture_personal"): "Venture",
    ("capital_one", "capital_one_venture_x_business"): "Venture X Business",
    ("capital_one", "capital_one_venture_business"): "Venture Business",
    ("capital_one", "capital_one_savor"): "Savor",
    ("capital_one", "capital_one_quicksilver"): "Quicksilver",
    ("chase", "chase_sapphire_preferred"): "Sapphire Preferred",
    ("chase", "chase_sapphire_reserve"): "Sapphire Reserve",
    ("chase", "chase_sapphire_business"): "Sapphire Reserve Business",
    ("chase", "chase_freedom_unlimited"): "Freedom Unlimited",
    ("chase", "chase_freedom_flex"): "Freedom Flex",
    ("chase", "chase_ink_preferred"): "Ink Preferred",
    ("chase", "chase_ink_cash"): "Ink Cash",
    ("chase", "chase_ink_unlimited"): "Ink Unlimited",
    ("chase", "chase_ink_premier"): "Ink Premier",
    ("citi", "citi_strata_premier"): "Citi Strata Premier",
    ("citi", "citi_prestige"): "Citi Prestige",
    ("citi", "citi_custom_cash"): "Citi Custom Cash",
    ("citi", "citi_double_cash"): "Citi Double Cash",
    ("wells_fargo", "wells_fargo_autograph"): "Wells Fargo Autograph",
    ("wells_fargo", "wells_fargo_autograph_journey"): "Wells Fargo Autograph Journey",
    ("wells_fargo", "wells_fargo_active_cash"): "Wells Fargo Active Cash",
}


def _issuer_key(issuer: str | None) -> str:
    key = normalized_key(issuer)
    return ISSUER_ALIASES.get(key, key)


def _has_any(name: str, *terms: str) -> bool:
    return any(term in name for term in terms)


def _business_suffix(name: str) -> str:
    return "business" if "business" in name else "personal"


def _tier(name: str, tiers: tuple[str, ...], default: str = "card") -> str:
    for tier in tiers:
        if tier in name:
            return tier.replace(" ", "_")
    return default


def _title_from_variant(prefix: str, variant: str) -> str | None:
    if not variant.startswith(prefix):
        return None
    suffix = variant.removeprefix(prefix).strip("_")
    if not suffix:
        return None
    parts = [part for part in suffix.split("_") if part != "personal"]
    business = bool(parts and parts[-1] == "business")
    if business:
        parts = parts[:-1]
    if not parts:
        return None
    title = " ".join(part.upper() if part in {"ihg"} else part.title() for part in parts)
    return f"{title} Business" if business else title


def _clean_display_name(product_name: str | None) -> str | None:
    if not product_name:
        return None
    value = product_name.translate({0x00AE: None, 0x2122: None, 0x2120: None})
    value = re.sub(r"\s+", " ", value).strip()
    cleanup_patterns = (
        r"\bcredit card\b",
        r"\brewards card\b",
        r"\breward card\b",
        r"\bcard\b",
        r"\s+from American Express\b",
    )
    for pattern in cleanup_patterns:
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -")
    return value or product_name


def _cobrand_variant(name: str) -> tuple[str, str] | None:
    """Exact product variants for co-brands, independent of scraped issuer."""
    suffix = _business_suffix(name)
    if _has_any(name, "delta", "skymiles"):
        tier = _tier(name, ("blue", "gold", "platinum", "reserve"))
        return ("delta_skymiles", f"delta_skymiles_{tier}_{suffix}")
    if _has_any(name, "marriott", "bonvoy", "ritz carlton"):
        tier = _tier(name, ("bold", "boundless", "brilliant", "bevy", "business", "bountiful", "ritz carlton"))
        return ("marriott_bonvoy", f"marriott_bonvoy_{tier}_{suffix}")
    if _has_any(name, "hyatt", "world of hyatt"):
        tier = "business" if "business" in name else "personal"
        return ("world_of_hyatt", f"world_of_hyatt_{tier}")
    if _has_any(name, "hilton", "honors", "surpass", "aspire"):
        tier = _tier(name, ("honors", "surpass", "aspire", "business"))
        return ("hilton_honors", f"hilton_honors_{tier}_{suffix}")
    if _has_any(name, "ihg", "one rewards"):
        tier = _tier(name, ("premier", "traveler", "select", "business"))
        return ("ihg_one_rewards", f"ihg_one_rewards_{tier}_{suffix}")
    if _has_any(name, "united", "mileageplus"):
        tier = _tier(name, ("gateway", "explorer", "quest", "club", "business"))
        return ("united_mileageplus", f"united_mileageplus_{tier}_{suffix}")
    if _has_any(name, "southwest", "rapid rewards"):
        tier = _tier(name, ("plus", "premier", "priority", "performance business", "business"))
        return ("southwest_rapid_rewards", f"southwest_rapid_rewards_{tier}_{suffix}")
    if _has_any(name, "american airlines", "aadvantage", "advantage"):
        tier = _tier(name, ("platinum select", "executive", "mileup", "business"))
        return ("american_airlines_aadvantage", f"american_airlines_aadvantage_{tier}_{suffix}")
    if _has_any(name, "alaska", "mileage plan"):
        return ("alaska_mileage_plan", f"alaska_mileage_plan_{suffix}")
    if _has_any(name, "jetblue", "trueblue"):
        tier = _tier(name, ("plus", "business"))
        return ("jetblue_trueblue", f"jetblue_trueblue_{tier}_{suffix}")
    if _has_any(name, "hawaiian", "hawaiianmiles"):
        return ("hawaiianmiles", f"hawaiianmiles_{suffix}")
    if _has_any(name, "atmos"):
        tier = _tier(name, ("ascent", "summit"))
        return ("atmos_rewards", f"atmos_rewards_{tier}_{suffix}")
    if _has_any(name, "wyndham"):
        tier = _tier(name, ("earner plus", "earner business", "earner"))
        return ("wyndham_rewards", f"wyndham_rewards_{tier}_{suffix}")
    if _has_any(name, "choice privileges", "choice"):
        tier = _tier(name, ("select", "business"))
        return ("choice_privileges", f"choice_privileges_{tier}_{suffix}")
    return None


def reward_tag_for_currency(currency: str | None) -> str | None:
    if currency in TRANSFERABLE_CURRENCIES:
        return "transferable"
    if currency in AIRLINE_CURRENCIES:
        return "airline_cobrand"
    if currency in HOTEL_CURRENCIES:
        return "hotel_cobrand"
    return None


def reward_currency_for_product(
    issuer: str | None,
    product_name: str | None,
    observed_value: str | None = None,
) -> str | None:
    """Return the canonical rewards currency for a public card product.

    Product/program identity wins over broad issuer identity. A Delta Amex card
    earns Delta SkyMiles, not Amex Membership Rewards; a Marriott Chase card
    earns Marriott Bonvoy, not Chase Ultimate Rewards.
    """
    issuer_key = _issuer_key(issuer)
    name = _norm(product_name)
    observed = _norm(observed_value)

    product_rules = (
        (("delta", "skymiles"), "Delta SkyMiles"),
        (("marriott", "bonvoy", "ritz carlton"), "Marriott Bonvoy"),
        (("hyatt", "world of hyatt"), "World of Hyatt"),
        (("hilton", "honors", "surpass", "aspire"), "Hilton Honors"),
        (("ihg", "one rewards"), "IHG One Rewards"),
        (("united", "mileageplus"), "United MileagePlus"),
        (("southwest", "rapid rewards"), "Southwest Rapid Rewards"),
        (("american airlines", "aadvantage", "advantage"), "American Airlines AAdvantage"),
        (("alaska", "mileage plan"), "Alaska Mileage Plan"),
        (("jetblue", "trueblue"), "JetBlue TrueBlue"),
        (("hawaiian", "hawaiianmiles"), "HawaiianMiles"),
        (("atmos",), "Atmos Rewards"),
        (("wyndham",), "Wyndham Rewards"),
        (("choice privileges", "choice"), "Choice Privileges"),
    )
    for terms, currency in product_rules:
        if _has_any(name, *terms):
            return currency

    if issuer_key == "capital_one":
        if _has_any(name, "venture", "spark miles"):
            return "Capital One Miles"
        if _has_any(name, "savor", "quicksilver", "spark cash"):
            return "cash back"
    if issuer_key == "chase" and _has_any(name, "sapphire", "ink", "freedom"):
        return "Chase Ultimate Rewards"
    if issuer_key == "american_express" and _has_any(
        name,
        "platinum",
        "gold",
        "green",
        "everyday",
        "blue business plus",
        "business plus",
    ):
        return "Amex Membership Rewards"
    if issuer_key == "citi" and _has_any(name, "strata", "premier", "prestige"):
        return "Citi ThankYou Points"
    if issuer_key == "bilt":
        return "Bilt Rewards"
    if issuer_key == "wells_fargo" and _has_any(name, "autograph"):
        return "Wells Fargo Rewards"

    observed_rules = (
        (("delta", "skymiles"), "Delta SkyMiles"),
        (("marriott", "bonvoy"), "Marriott Bonvoy"),
        (("hyatt", "world of hyatt"), "World of Hyatt"),
        (("hilton", "honors"), "Hilton Honors"),
        (("ihg", "one rewards"), "IHG One Rewards"),
        (("ultimate reward",), "Chase Ultimate Rewards"),
        (("membership reward",), "Amex Membership Rewards"),
        (("thankyou", "thank you"), "Citi ThankYou Points"),
        (("capital one",), "Capital One Miles"),
        (("bilt",), "Bilt Rewards"),
        (("wells fargo",), "Wells Fargo Rewards"),
    )
    for terms, currency in observed_rules:
        if _has_any(observed, *terms):
            return currency
    if _has_any(observed, "cash", "statement credit", "dollar"):
        return "cash back"
    return observed_value.strip() if isinstance(observed_value, str) and observed_value.strip() else None


def derive_product_family(issuer: str | None, product_name: str | None) -> str | None:
    """Return a conservative product-family key for duplicate suppression.

    Families intentionally cover products a user generally should not open
    side-by-side without a deliberate close/reopen plan. Distinct Ink variants,
    airline cards, and hotel cards remain separate unless the name clearly
    describes the same ladder.
    """
    issuer_norm = _norm(issuer)
    name = _norm(product_name)
    if not issuer_norm or not name:
        return None

    if _cobrand_variant(name):
        return None

    if "capital one" in issuer_norm:
        if "venture" in name:
            return "capital_one_venture_business" if "business" in name else "capital_one_venture"
        if "savor" in name:
            return "capital_one_savor_business" if "business" in name else "capital_one_savor"
        if "quicksilver" in name:
            return "capital_one_quicksilver"

    if "chase" in issuer_norm:
        if "sapphire" in name:
            if "business" in name:
                return "chase_sapphire_business"
            return "chase_sapphire"
        if "freedom" in name:
            return "chase_freedom"
        if "ink" in name:
            if "preferred" in name:
                return "chase_ink_preferred"
            if "cash" in name:
                return "chase_ink_cash"
            if "unlimited" in name:
                return "chase_ink_unlimited"
            if "premier" in name:
                return "chase_ink_premier"

    if "american express" in issuer_norm or "amex" in issuer_norm:
        business = "business" in name
        prefix = "amex_business" if business else "amex"
        if "platinum" in name:
            return f"{prefix}_platinum"
        if "gold" in name:
            return f"{prefix}_gold"
        if "green" in name:
            return f"{prefix}_green"
        if "blue business plus" in name:
            return "amex_blue_business_plus"
        if "everyday preferred" in name:
            return "amex_everyday_preferred"
        if "everyday" in name:
            return "amex_everyday"

    if "citi" in issuer_norm:
        if "strata" in name or "premier" in name:
            return "citi_strata_premier"
        if "prestige" in name:
            return "citi_prestige"
        if "custom cash" in name:
            return "citi_custom_cash"
        if "double cash" in name:
            return "citi_double_cash"

    if "wells fargo" in issuer_norm:
        if "autograph journey" in name:
            return "wells_fargo_autograph_journey"
        if "autograph" in name:
            return "wells_fargo_autograph"
        if "active cash" in name:
            return "wells_fargo_active_cash"

    return None


def product_variant_key(
    issuer: str | None,
    product_name: str | None,
) -> tuple[str, str] | None:
    """Return a canonical key for the exact product variant.

    This is intentionally narrower than ``derive_product_family``. Families
    describe strategy ladders; variants identify the specific card the user
    already holds so name differences do not create duplicate recommendations.
    """
    issuer_norm = _norm(issuer)
    issuer_key = _issuer_key(issuer)
    name = _norm(product_name)
    if not issuer_key or not name:
        return None

    cobrand = _cobrand_variant(name)
    if cobrand:
        return cobrand

    if "chase" in issuer_norm:
        if "sapphire" in name:
            if "business" in name:
                return (issuer_key, "chase_sapphire_business")
            if "reserve" in name:
                return (issuer_key, "chase_sapphire_reserve")
            if "preferred" in name:
                return (issuer_key, "chase_sapphire_preferred")
            return (issuer_key, "chase_sapphire")
        if "freedom" in name:
            if "flex" in name:
                return (issuer_key, "chase_freedom_flex")
            if "unlimited" in name:
                return (issuer_key, "chase_freedom_unlimited")
            return (issuer_key, "chase_freedom")
        if "ink" in name:
            if "preferred" in name:
                return (issuer_key, "chase_ink_preferred")
            if "cash" in name:
                return (issuer_key, "chase_ink_cash")
            if "unlimited" in name:
                return (issuer_key, "chase_ink_unlimited")
            if "premier" in name:
                return (issuer_key, "chase_ink_premier")

    if "capital one" in issuer_norm:
        if "venture" in name:
            suffix = "business" if "business" in name else "personal"
            if "x" in name:
                return (issuer_key, f"capital_one_venture_x_{suffix}")
            return (issuer_key, f"capital_one_venture_{suffix}")
        if "savor" in name:
            return (issuer_key, "capital_one_savor")
        if "quicksilver" in name:
            return (issuer_key, "capital_one_quicksilver")

    if "american express" in issuer_norm or "amex" in issuer_norm:
        business = "business" in name
        prefix = "amex_business" if business else "amex"
        if "platinum" in name:
            return (issuer_key, f"{prefix}_platinum")
        if "gold" in name:
            return (issuer_key, f"{prefix}_gold")
        if "green" in name:
            return (issuer_key, f"{prefix}_green")
        if "blue business plus" in name:
            return (issuer_key, "amex_blue_business_plus")
        if "everyday preferred" in name:
            return (issuer_key, "amex_everyday_preferred")
        if "everyday" in name:
            return (issuer_key, "amex_everyday")

    return (issuer_key, normalized_key(product_name))


def canonical_product_key(issuer: str | None, product_name: str | None) -> str:
    variant = product_variant_key(issuer, product_name)
    if variant:
        return f"{variant[0]}:{variant[1]}"
    return f"{_issuer_key(issuer)}:{normalized_key(product_name)}"


def product_display_name(issuer: str | None, product_name: str | None) -> str:
    variant = product_variant_key(issuer, product_name)
    if variant and variant in CARD_DISPLAY_NAMES:
        return CARD_DISPLAY_NAMES[variant]
    if variant:
        namespace, key = variant
        dynamic_prefixes = {
            "delta_skymiles": ("delta_skymiles_", "Delta"),
            "marriott_bonvoy": ("marriott_bonvoy_", "Marriott"),
            "world_of_hyatt": ("world_of_hyatt_", "Hyatt"),
            "hilton_honors": ("hilton_honors_", "Hilton"),
            "ihg_one_rewards": ("ihg_one_rewards_", "IHG"),
            "united_mileageplus": ("united_mileageplus_", "United"),
            "southwest_rapid_rewards": ("southwest_rapid_rewards_", "Southwest"),
            "american_airlines_aadvantage": ("american_airlines_aadvantage_", "AA"),
            "alaska_mileage_plan": ("alaska_mileage_plan_", "Alaska"),
            "jetblue_trueblue": ("jetblue_trueblue_", "JetBlue"),
            "hawaiianmiles": ("hawaiianmiles_", "Hawaiian"),
            "atmos_rewards": ("atmos_rewards_", "Atmos"),
            "wyndham_rewards": ("wyndham_rewards_", "Wyndham"),
            "choice_privileges": ("choice_privileges_", "Choice"),
        }
        if namespace in dynamic_prefixes:
            prefix, label = dynamic_prefixes[namespace]
            title = _title_from_variant(prefix, key)
            if title:
                return f"{label} {title}"
    return _clean_display_name(product_name) or ""


def product_reference(issuer: str | None, product_name: str | None) -> dict:
    display_name = product_display_name(issuer, product_name)
    raw_name = (product_name or "").strip()
    search_terms = [display_name]
    if raw_name and raw_name != display_name:
        search_terms.append(raw_name)
    return {
        "canonical_key": canonical_product_key(issuer, product_name),
        "display_name": display_name,
        "search_terms": search_terms,
    }


def family_key(
    issuer: str | None,
    product_name: str | None,
    explicit_family: str | None = None,
) -> tuple[str, str] | None:
    issuer_key = _issuer_key(issuer)
    derived = derive_product_family(issuer, product_name)
    family = derived if _cobrand_variant(_norm(product_name)) else (normalized_key(explicit_family) or derived)
    if not issuer_key or not family:
        return None
    return (issuer_key, family)
