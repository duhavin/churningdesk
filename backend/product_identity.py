"""Shared public product identity helpers."""
from __future__ import annotations

import re


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def normalized_key(value: str | None) -> str:
    return _norm(value).replace(" ", "_")


def _issuer_key(issuer: str | None) -> str:
    return normalized_key(issuer)


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


def family_key(
    issuer: str | None,
    product_name: str | None,
    explicit_family: str | None = None,
) -> tuple[str, str] | None:
    issuer_key = normalized_key(issuer)
    family = normalized_key(explicit_family) or derive_product_family(issuer, product_name)
    if not issuer_key or not family:
        return None
    return (issuer_key, family)
