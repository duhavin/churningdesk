"""Reusable per-request decision data."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import config, models
from ..crypto import MissingKeyError
from ..product_identity import product_variant_key
from .catalog import dedupe_products_by_variant
from .eligibility import add_months


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
    organic_monthly_capacity_by_user: dict[str, float | None]
    benefit_usage_by_held: dict[int, list]
    offer_evidence_by_product: dict[int, list[models.IngestionEvidence]]

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
        organic_monthly_capacity_by_user: dict[str, float | None] = {}
        for user in config.USERS:
            try:
                # The encrypted profile columns are decrypted during row
                # materialization.  Keep the lookup inside the existing
                # MissingKeyError boundary so an unavailable key leaves only
                # private capacity/balances unknown for this projection.
                profile = db.get(models.UserProfile, user)
                point_balances_by_user[user] = dict((profile.point_balances if profile else None) or {})
                organic_monthly_capacity_by_user[user] = (
                    float(profile.organic_monthly_capacity)
                    if profile and profile.organic_monthly_capacity is not None
                    else None
                )
            except MissingKeyError:
                point_balances_by_user[user] = {}
                organic_monthly_capacity_by_user[user] = None

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

        product_ids = [p.id for p in products if p.id]
        offer_evidence_by_product: dict[int, list[models.IngestionEvidence]] = {}
        if product_ids:
            evidence_rows = list(
                db.scalars(
                    select(models.IngestionEvidence).where(
                        models.IngestionEvidence.product_id.in_(product_ids)
                    )
                ).all()
            )
            for row in evidence_rows:
                offer_evidence_by_product.setdefault(row.product_id, []).append(row)

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
            organic_monthly_capacity_by_user=organic_monthly_capacity_by_user,
            benefit_usage_by_held=benefit_usage_by_held,
            offer_evidence_by_product=offer_evidence_by_product,
        )

    def clone_for_hypothetical_application(
        self,
        user: str,
        candidate: dict,
        as_of: dt.date | None = None,
    ) -> "DecisionContext":
        """Return a read-only projection snapshot with one synthetic open card.

        The synthetic card is never added to the SQLAlchemy session.  Existing
        ORM rows remain shared read-only objects while the per-user collections
        are copied before the hypothetical is appended, so successor planning
        cannot mutate the live context or write private state.
        """
        opened_on = as_of or dt.date.today()
        try:
            window_months = int(candidate.get("current_offer_window_months"))
        except (TypeError, ValueError):
            window_months = None
        hypothetical = models.HeldCard(
            user=user,
            product_id=candidate.get("id"),
            issuer=str(candidate.get("issuer") or ""),
            product_name=str(candidate.get("product_name") or ""),
            date_opened=opened_on,
            ownership=str(candidate.get("ownership") or "Personal"),
            account_type=str(candidate.get("account_type") or "Credit Card"),
            reports_to_personal_credit=bool(candidate.get("reports_to_personal_credit", True)),
            min_spend_requirement=candidate.get("current_offer_min_spend"),
            min_spend_deadline=(
                add_months(opened_on, window_months)
                if window_months and window_months > 0
                else None
            ),
            min_spend_progress=0.0,
            min_spend_completed=False,
            status="Active",
        )
        held_by_user = {name: list(cards) for name, cards in self.held_by_user.items()}
        held_by_user.setdefault(user, []).append(hypothetical)
        active_held_by_user = {
            name: list(cards) for name, cards in self.active_held_by_user.items()
        }
        active_held_by_user.setdefault(user, []).append(hypothetical)
        return replace(
            self,
            held_by_user=held_by_user,
            active_held_by_user=active_held_by_user,
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
