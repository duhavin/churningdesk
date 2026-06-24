"""Seeded and learned PUBLIC card reference registry.

References are identity/source hints, not offer data. They help discovery and
refresh start from known card names, aliases, currencies, and learned source
URLs without fabricating current offers, peaks, fees, or benefits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .product_identity import (
    canonical_product_key,
    derive_product_family,
    product_display_name,
    product_variant_key,
    reward_currency_for_product,
    reward_tag_for_currency,
)


@dataclass(frozen=True, slots=True)
class CardReferenceSeed:
    issuer: str
    product_name: str
    aliases: tuple[str, ...] = ()
    ownership: str = "Personal"
    account_type: str = "Credit Card"
    issuer_domain: str | None = None
    issuer_url: str | None = None
    offer_url: str | None = None
    history_url: str | None = None
    benefits_url: str | None = None
    reports_to_personal_credit: bool = True
    trusted_source_urls: tuple[str, ...] = ()


REFERENCE_SEEDS: tuple[CardReferenceSeed, ...] = (
    CardReferenceSeed(
        "American Express",
        "American Express Gold Card",
        ("Amex Gold", "Gold Card"),
        account_type="Charge Card",
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
    ),
    CardReferenceSeed(
        "American Express",
        "American Express Business Gold Card",
        ("Amex Business Gold", "Business Gold Card"),
        ownership="Business",
        account_type="Charge Card",
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/american-express-business-gold-card/",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "American Express",
        "The Platinum Card from American Express",
        ("Amex Platinum", "Platinum Card"),
        account_type="Charge Card",
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/card/platinum/",
    ),
    CardReferenceSeed(
        "American Express",
        "The Business Platinum Card from American Express",
        ("Amex Business Platinum", "Business Platinum Card"),
        ownership="Business",
        account_type="Charge Card",
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/american-express-business-platinum-card/",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "American Express",
        "American Express Green Card",
        ("Amex Green", "Green Card"),
        account_type="Charge Card",
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/card/green/",
    ),
    CardReferenceSeed(
        "American Express",
        "Blue Business Plus Credit Card from American Express",
        ("Blue Business Plus", "Amex Blue Business Plus", "BBP"),
        ownership="Business",
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/blue-business-plus-credit-card/",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Chase",
        "Chase Sapphire Preferred Card",
        ("Sapphire Preferred", "CSP"),
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
    ),
    CardReferenceSeed(
        "Chase",
        "Chase Sapphire Reserve",
        ("Sapphire Reserve", "CSR"),
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/reserve",
    ),
    CardReferenceSeed(
        "Chase",
        "Chase Freedom Unlimited Credit Card",
        ("Freedom Unlimited", "CFU"),
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/cash-back-credit-cards/freedom/unlimited",
    ),
    CardReferenceSeed(
        "Chase",
        "Chase Freedom Flex",
        ("Freedom Flex", "CFF"),
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/cash-back-credit-cards/freedom/flex",
    ),
    CardReferenceSeed(
        "Chase",
        "Ink Business Preferred Credit Card",
        ("Ink Preferred", "Chase Ink Preferred", "Ink Business Preferred"),
        ownership="Business",
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/business-credit-cards/ink/business-preferred",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Chase",
        "Ink Business Cash Credit Card",
        ("Ink Cash", "Chase Ink Cash", "Ink Business Cash"),
        ownership="Business",
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/business-credit-cards/ink/cash",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Chase",
        "Ink Business Unlimited Credit Card",
        ("Ink Unlimited", "Chase Ink Unlimited", "Ink Business Unlimited"),
        ownership="Business",
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/business-credit-cards/ink/unlimited",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Chase",
        "Ink Business Premier Credit Card",
        ("Ink Premier", "Chase Ink Premier", "Ink Business Premier"),
        ownership="Business",
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/business-credit-cards/ink/premier",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Capital One",
        "Capital One Venture X Rewards Credit Card",
        ("Venture X", "Capital One Venture X"),
        issuer_domain="capitalone.com",
        issuer_url="https://www.capitalone.com/credit-cards/venture-x/",
    ),
    CardReferenceSeed(
        "Capital One",
        "Capital One Venture Rewards Credit Card",
        ("Venture", "Capital One Venture"),
        issuer_domain="capitalone.com",
        issuer_url="https://www.capitalone.com/credit-cards/venture/",
    ),
    CardReferenceSeed(
        "Capital One",
        "Capital One Venture X Business",
        ("Venture X Business", "Capital One Venture X Business"),
        ownership="Business",
        issuer_domain="capitalone.com",
        issuer_url="https://www.capitalone.com/small-business/credit-cards/venture-x-business/",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Capital One",
        "Capital One Venture Business",
        ("Venture Business", "Capital One Venture Business"),
        ownership="Business",
        issuer_domain="capitalone.com",
        issuer_url="https://www.capitalone.com/small-business/credit-cards/venture-business/",
        reports_to_personal_credit=False,
    ),
    CardReferenceSeed(
        "Capital One",
        "Capital One Savor Cash Rewards Credit Card",
        ("Savor", "Capital One Savor"),
        issuer_domain="capitalone.com",
        issuer_url="https://www.capitalone.com/credit-cards/savor/",
    ),
    CardReferenceSeed(
        "Citi",
        "Citi Strata Premier Card",
        ("Citi Strata Premier", "Strata Premier"),
        issuer_domain="citi.com",
        issuer_url="https://www.citi.com/credit-cards/citi-strata-premier-credit-card",
    ),
    CardReferenceSeed(
        "Citi",
        "Citi Custom Cash Card",
        ("Citi Custom Cash", "Custom Cash"),
        issuer_domain="citi.com",
        issuer_url="https://www.citi.com/credit-cards/citi-custom-cash-credit-card",
    ),
    CardReferenceSeed(
        "Citi",
        "Citi Double Cash Card",
        ("Citi Double Cash", "Double Cash"),
        issuer_domain="citi.com",
        issuer_url="https://www.citi.com/credit-cards/citi-double-cash-credit-card",
    ),
    CardReferenceSeed(
        "Wells Fargo",
        "Wells Fargo Autograph Card",
        ("Wells Fargo Autograph", "Autograph"),
        issuer_domain="wellsfargo.com",
        issuer_url="https://creditcards.wellsfargo.com/autograph-visa-credit-card/",
    ),
    CardReferenceSeed(
        "Wells Fargo",
        "Wells Fargo Autograph Journey Card",
        ("Wells Fargo Autograph Journey", "Autograph Journey"),
        issuer_domain="wellsfargo.com",
        issuer_url="https://creditcards.wellsfargo.com/autograph-journey-visa-credit-card/",
    ),
    CardReferenceSeed(
        "American Express",
        "Delta SkyMiles Gold American Express Card",
        ("Delta Gold", "Delta SkyMiles Gold", "Delta Amex Gold"),
        issuer_domain="americanexpress.com",
        issuer_url="https://www.americanexpress.com/us/credit-cards/card/delta-skymiles-gold-american-express-card/",
    ),
    CardReferenceSeed(
        "Chase",
        "Marriott Bonvoy Boundless Credit Card",
        ("Marriott Boundless", "Bonvoy Boundless"),
        issuer_domain="chase.com",
        issuer_url="https://creditcards.chase.com/travel-credit-cards/marriott-bonvoy/boundless",
    ),
)


def _dedupe(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        text = value.strip()
        key = text.lower()
        if text and key not in seen:
            out.append(text)
            seen.add(key)
    return out


def _seed_dict(seed: CardReferenceSeed) -> dict:
    key = canonical_product_key(seed.issuer, seed.product_name)
    display_name = product_display_name(seed.issuer, seed.product_name)
    currency = reward_currency_for_product(seed.issuer, seed.product_name)
    search_terms = _dedupe([display_name, seed.product_name, *seed.aliases])
    return {
        "canonical_key": key,
        "issuer": seed.issuer,
        "product_name": seed.product_name,
        "display_name": display_name,
        "aliases": list(seed.aliases),
        "search_terms": search_terms,
        "currency": currency,
        "product_family": derive_product_family(seed.issuer, seed.product_name),
        "ownership": seed.ownership,
        "account_type": seed.account_type,
        "reports_to_personal_credit": seed.reports_to_personal_credit,
        "issuer_domain": seed.issuer_domain,
        "issuer_url": seed.issuer_url,
        "offer_url": seed.offer_url,
        "history_url": seed.history_url,
        "benefits_url": seed.benefits_url,
        "trusted_source_urls": list(seed.trusted_source_urls),
        "source": "seed",
    }


SEED_REFERENCES_BY_KEY = {
    item["canonical_key"]: item for item in (_seed_dict(seed) for seed in REFERENCE_SEEDS)
}


def seed_reference_for_product(issuer: str | None, product_name: str | None) -> dict | None:
    return SEED_REFERENCES_BY_KEY.get(canonical_product_key(issuer, product_name))


def reference_to_dict(reference: models.CardReference) -> dict:
    return {
        "id": reference.id,
        "canonical_key": reference.canonical_key,
        "issuer": reference.issuer,
        "product_name": reference.product_name,
        "display_name": reference.display_name,
        "aliases": reference.aliases or [],
        "search_terms": reference.search_terms or [],
        "currency": reference.currency,
        "product_family": reference.product_family,
        "ownership": reference.ownership,
        "account_type": reference.account_type,
        "reports_to_personal_credit": reference.reports_to_personal_credit,
        "issuer_domain": reference.issuer_domain,
        "issuer_url": reference.issuer_url,
        "offer_url": reference.offer_url,
        "history_url": reference.history_url,
        "benefits_url": reference.benefits_url,
        "trusted_source_urls": reference.trusted_source_urls or [],
        "learned_source_urls": reference.learned_source_urls or [],
        "active": reference.active,
        "source": reference.source,
        "updated_at": reference.updated_at.isoformat() if reference.updated_at else None,
    }


def get_reference(db: Session, issuer: str | None, product_name: str | None) -> models.CardReference | None:
    key = canonical_product_key(issuer, product_name)
    return db.scalar(
        select(models.CardReference).where(models.CardReference.canonical_key == key)
    )


def reference_for_product(db: Session | None, issuer: str | None, product_name: str | None) -> dict | None:
    if db is not None:
        row = get_reference(db, issuer, product_name)
        if row and row.active:
            return reference_to_dict(row)
    return seed_reference_for_product(issuer, product_name)


def reference_source_urls(db: Session, product: models.CardProduct) -> list[str]:
    ref = get_reference(db, product.issuer, product.product_name)
    if not ref or not ref.active:
        return []
    return _dedupe(
        [
            ref.issuer_url,
            ref.offer_url,
            ref.history_url,
            ref.benefits_url,
            *(ref.trusted_source_urls or []),
            *(ref.learned_source_urls or []),
        ]
    )


def promote_reference_url(db: Session, product: models.CardProduct, url: str | None) -> bool:
    if not url:
        return False
    ref = get_reference(db, product.issuer, product.product_name)
    if ref is None:
        seed = _seed_dict(
            CardReferenceSeed(
                issuer=product.issuer,
                product_name=product.product_name,
                ownership=product.ownership or "Personal",
                account_type=product.account_type or "Credit Card",
                reports_to_personal_credit=bool(product.reports_to_personal_credit),
            )
        )
        ref = models.CardReference(**seed)
        db.add(ref)
        db.flush()
    learned = _dedupe([*(ref.learned_source_urls or []), url])
    if learned == (ref.learned_source_urls or []):
        return False
    ref.learned_source_urls = learned
    return True


def _existing_variant_map(db: Session) -> dict[tuple[str, str], models.CardProduct]:
    out: dict[tuple[str, str], models.CardProduct] = {}
    for product in db.scalars(select(models.CardProduct)).all():
        variant = product_variant_key(product.issuer, product.product_name)
        if variant and variant not in out:
            out[variant] = product
    return out


def seed_card_references(db: Session, *, seed_products: bool = True) -> dict:
    existing_refs = {
        row.canonical_key: row
        for row in db.scalars(select(models.CardReference)).all()
    }
    added_refs = 0
    updated_refs = 0
    added_products = 0
    updated_products = 0

    for data in SEED_REFERENCES_BY_KEY.values():
        ref = existing_refs.get(data["canonical_key"])
        if ref is None:
            ref = models.CardReference(**data)
            db.add(ref)
            existing_refs[data["canonical_key"]] = ref
            added_refs += 1
        else:
            changed = False
            for field in (
                "issuer",
                "product_name",
                "display_name",
                "aliases",
                "search_terms",
                "currency",
                "product_family",
                "ownership",
                "account_type",
                "reports_to_personal_credit",
                "issuer_domain",
                "issuer_url",
                "offer_url",
                "history_url",
                "benefits_url",
                "trusted_source_urls",
            ):
                if getattr(ref, field) in (None, "", [], {}) and data.get(field) not in (None, "", [], {}):
                    setattr(ref, field, data[field])
                    changed = True
            if changed:
                updated_refs += 1

    if seed_products:
        db.flush()
        existing_variants = _existing_variant_map(db)
        for data in SEED_REFERENCES_BY_KEY.values():
            variant = product_variant_key(data["issuer"], data["product_name"])
            if not variant:
                continue
            product = existing_variants.get(variant)
            if product is None:
                product = models.CardProduct(
                    issuer=data["issuer"],
                    product_name=data["product_name"],
                    product_family=data["product_family"],
                    ownership=data["ownership"],
                    account_type=data["account_type"],
                    currency=data["currency"],
                    reports_to_personal_credit=data["reports_to_personal_credit"],
                    tag=reward_tag_for_currency(data["currency"]),
                    added_by="reference_seed",
                    discovery_reviewed=True,
                    notes="Seeded identity reference; offer fields require sourced refresh.",
                )
                db.add(product)
                existing_variants[variant] = product
                added_products += 1
            else:
                changed = False
                safe_updates = {
                    "product_family": data["product_family"],
                    "currency": data["currency"],
                    "ownership": data["ownership"],
                    "account_type": data["account_type"],
                    "reports_to_personal_credit": data["reports_to_personal_credit"],
                    "tag": reward_tag_for_currency(data["currency"]),
                }
                for field, value in safe_updates.items():
                    if getattr(product, field) in (None, "", [], {}) and value not in (None, "", [], {}):
                        setattr(product, field, value)
                        changed = True
                if changed:
                    updated_products += 1

    db.commit()
    return {
        "references_seeded": len(SEED_REFERENCES_BY_KEY),
        "references_added": added_refs,
        "references_updated": updated_refs,
        "products_added": added_products,
        "products_updated": updated_products,
    }
