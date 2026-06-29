"""Reusable per-request decision data."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import config, models
from ..crypto import MissingKeyError
from ..product_identity import product_variant_key
from .catalog import dedupe_products_by_variant


def key(issuer: str | None, product_name: str | None) -> tuple[str, str]:
    return ((issuer or "").strip().lower(), (product_name or "").strip().lower())


@dataclass(slots=True)
class DecisionContext:
    valuations: dict[str, float]
    products: list[models.CardProduct]
    products_by_id: dict[int, models.CardProduct]
    products_by_key: dict[tuple[str, str], models.CardProduct]
    products_by_variant: dict[tuple[str, str], models.CardProduct]
    held_by_user: dict[str, list[models.HeldCard]]
    active_held_by_user: dict[str, list[models.HeldCard]]
    manual_targeted_by_user: dict[str, list[models.ManualTargetedOffer]]
    point_balances_by_user: dict[str, dict[str, float]]
    benefit_usage_by_held: dict[int, list]

    @classmethod
    def load(cls, db: Session) -> "DecisionContext":
        valuations = {
            v.currency.strip().lower(): v.cpp_effective
            for v in db.scalars(select(models.Valuation)).all()
            if v.currency and v.cpp_effective is not None
        }
        blacklist = {
            key(b.issuer, b.product_name)
            for b in db.scalars(select(models.CardBlacklist)).all()
        }
        products = dedupe_products_by_variant([
            p
            for p in db.scalars(select(models.CardProduct)).all()
            if key(p.issuer, p.product_name) not in blacklist
        ])
        products_by_variant = {
            variant: p
            for p in products
            for variant in [product_variant_key(p.issuer, p.product_name)]
            if variant is not None
        }
        held_rows = list(
            db.scalars(
                select(models.HeldCard).where(models.HeldCard.user.in_(config.USERS))
            ).all()
        )
        held_by_user = {user: [] for user in config.USERS}
        active_held_by_user = {user: [] for user in config.USERS}
        for card in held_rows:
            held_by_user.setdefault(card.user, []).append(card)
            if card.status != "Closed":
                active_held_by_user.setdefault(card.user, []).append(card)

        today = dt.date.today()
        manual_rows = list(
            db.scalars(
                select(models.ManualTargetedOffer).where(
                    models.ManualTargetedOffer.user.in_(config.USERS),
                    or_(
                        models.ManualTargetedOffer.expires_at.is_(None),
                        models.ManualTargetedOffer.expires_at >= today,
                    ),
                )
            ).all()
        )
        manual_targeted_by_user = {user: [] for user in config.USERS}
        for offer in manual_rows:
            manual_targeted_by_user.setdefault(offer.user, []).append(offer)

        point_balances_by_user: dict[str, dict[str, float]] = {}
        for user in config.USERS:
            profile = db.get(models.UserProfile, user)
            try:
                point_balances_by_user[user] = dict((profile.point_balances if profile else None) or {})
            except MissingKeyError:
                point_balances_by_user[user] = {}

        all_held_ids = [card.id for cards in held_by_user.values() for card in cards if card.id]
        benefit_usage_by_held: dict[int, list] = {}
        if all_held_ids:
            usage_rows = list(
                db.scalars(
                    select(models.BenefitUsage)
                    .where(models.BenefitUsage.held_card_id.in_(all_held_ids))
                    .where(models.BenefitUsage.period_key != "__all__")
                ).all()
            )
            for u in usage_rows:
                benefit_usage_by_held.setdefault(u.held_card_id, []).append(u)

        return cls(
            valuations=valuations,
            products=products,
            products_by_id={p.id: p for p in products},
            products_by_key={key(p.issuer, p.product_name): p for p in products},
            products_by_variant=products_by_variant,
            held_by_user=held_by_user,
            active_held_by_user=active_held_by_user,
            manual_targeted_by_user=manual_targeted_by_user,
            point_balances_by_user=point_balances_by_user,
            benefit_usage_by_held=benefit_usage_by_held,
        )

    def product_for_card(self, card: models.HeldCard) -> models.CardProduct | None:
        if card.product_id and card.product_id in self.products_by_id:
            return self.products_by_id[card.product_id]
        exact = self.products_by_key.get(key(card.issuer, card.product_name))
        if exact:
            return exact
        variant = product_variant_key(card.issuer, card.product_name)
        if variant:
            return self.products_by_variant.get(variant)
        return None

    def active_product_refs(self) -> dict[str, list]:
        ids = sorted({
            card.product_id
            for cards in self.active_held_by_user.values()
            for card in cards
            if card.product_id
        })
        keys = sorted({
            key(card.issuer, card.product_name)
            for cards in self.active_held_by_user.values()
            for card in cards
        })
        variants = sorted({
            variant
            for cards in self.active_held_by_user.values()
            for card in cards
            for variant in [product_variant_key(card.issuer, card.product_name)]
            if variant is not None
        })
        return {
            "priority_product_ids": ids,
            "priority_product_keys": keys,
            "priority_product_variants": variants,
        }
