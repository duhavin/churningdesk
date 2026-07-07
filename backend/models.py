"""SQLAlchemy models.

The PRIVATE / PUBLIC boundary from §2 is reflected here:

  * PUBLIC tables  hold only public catalog/offer/rule data, keyed by
    issuer + product name. The ingestion engine may read/write these.
  * PRIVATE tables hold held-card details, balances, and identity. Sensitive
    fields are encrypted at rest (marked "🔒 encrypted"). The ingestion engine
    must NEVER touch these (enforced by it only importing PUBLIC models).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .crypto import EncryptedFloat, EncryptedInt, EncryptedJSON, EncryptedString
from .db import Base


def _now() -> dt.datetime:
    # Naive UTC (utcnow() is deprecated on 3.12+); kept naive to match existing
    # naive DateTime columns and comparisons elsewhere.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ===========================================================================
#  PUBLIC domain
# ===========================================================================
class CardWatchlist(Base):
    """Cards the user pins manually — the manual backstop for discovery (§4.3)."""

    __tablename__ = "card_watchlist"
    __table_args__ = (
        UniqueConstraint("issuer", "product_name", name="uq_watchlist_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer: Mapped[str] = mapped_column(String(120))
    product_name: Mapped[str] = mapped_column(String(200))
    priority: Mapped[bool] = mapped_column(Boolean, default=False)
    added_by: Mapped[str] = mapped_column(String(40), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CardBlacklist(Base):
    """Cards excluded everywhere — catalog, scoring, pipeline, discovery (§4.3)."""

    __tablename__ = "card_blacklist"
    __table_args__ = (
        UniqueConstraint("issuer", "product_name", name="uq_blacklist_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer: Mapped[str] = mapped_column(String(120))
    product_name: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class CardProduct(Base):
    """The public card catalog. No offer values are seeded — all are scraped."""

    __tablename__ = "card_product"
    __table_args__ = (
        UniqueConstraint("issuer", "product_name", name="uq_card_product_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer: Mapped[str] = mapped_column(String(120), index=True)
    product_name: Mapped[str] = mapped_column(String(200), index=True)
    product_family: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ownership: Mapped[str] = mapped_column(String(20), default="Personal")  # Personal|Business
    account_type: Mapped[str] = mapped_column(
        String(40), default="Credit Card"
    )  # Credit Card | Charge Card | Flexible Spending Credit Card
    currency: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reports_to_personal_credit: Mapped[bool] = mapped_column(Boolean, default=True)

    annual_fee: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Current public offer (scraped). `current_offer_override` is a manual entry
    # for a targeted offer valid right now; it wins until a refresh re-confirms the
    # public offer, at which point ingestion clears it (see validate.apply_extraction).
    current_offer_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_offer_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_offer_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_offer_min_spend: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_offer_window_months: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Peak / all-time-best offer (the "target/goal")
    peak_offer_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_offer_min_spend: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_offer_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    peak_offer_date: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Targeted / invite-only / incognito / phone high-water marks — kept SEPARATE
    # from the public peak so a one-off targeted offer never pollutes public data.
    targeted_peak_offer_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    targeted_peak_offer_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    targeted_peak_offer_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    targeted_peak_offer_date: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Points the *referrer* (an existing cardholder) earns for referring someone to
    # this card — powers household referral routing. `referral_bonus_points` is the
    # auto-scraped value (ingestion-managed, delta-gated); `referral_bonus_override`
    # is a manual entry that wins when we have a higher targeted referral offer.
    referral_bonus_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    referral_bonus_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    referral_bonus_cash: Mapped[float | None] = mapped_column(Float, nullable=True)

    first_year_credit_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    earn_multipliers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {category: "card to use"} — powers "what to use where" (travel/dining/etc.).
    best_category_uses: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # [{name, value, ...}] — the perks/credits ledger this card carries.
    card_benefits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Product-change targets for retention/downgrade decisions.
    downgrade_paths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    eligibility_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tag: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )  # transferable | hotel_cobrand | airline_cobrand | future_trip

    added_by: Mapped[str] = mapped_column(
        String(40), default="user_watchlist"
    )  # llm_discovery | user_watchlist
    discovery_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_web_search_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_supplemental_search_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    @property
    def peak_offer_effective(self) -> int | None:
        """Best target = higher of public peak and any targeted/seen-high peak."""
        public = self.peak_offer_points or 0
        targeted = self.targeted_peak_offer_points or 0
        best = max(public, targeted)
        return best or None

    @property
    def referral_bonus_effective(self) -> int | None:
        """Manual override wins over the scraped referral bonus (§referrals)."""
        return (
            self.referral_bonus_override
            if self.referral_bonus_override is not None
            else self.referral_bonus_points
        )

    @property
    def current_offer_effective(self) -> int | None:
        """Manual targeted override wins over the scraped current offer."""
        return (
            self.current_offer_override
            if self.current_offer_override is not None
            else self.current_offer_points
        )


class CardReference(Base):
    """Curated PUBLIC identity/source hints for cards the app cares about.

    This table intentionally stores stable metadata only. Offer amounts, peaks,
    fees, and benefits still come from sourced ingestion evidence.
    """

    __tablename__ = "card_reference"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_card_reference_canonical_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(180), index=True)
    issuer: Mapped[str] = mapped_column(String(120), index=True)
    product_name: Mapped[str] = mapped_column(String(200), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_terms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(80), nullable=True)
    product_family: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ownership: Mapped[str] = mapped_column(String(20), default="Personal")
    account_type: Mapped[str] = mapped_column(String(40), default="Credit Card")
    reports_to_personal_credit: Mapped[bool] = mapped_column(Boolean, default=True)
    issuer_domain: Mapped[str | None] = mapped_column(String(180), nullable=True)
    issuer_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    offer_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    benefits_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    trusted_source_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    learned_source_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(40), default="seed")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProposedChange(Base):
    """Human-in-the-loop review queue for LLM-extracted changes (§4.4)."""

    __tablename__ = "proposed_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_table: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(80))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected


class IngestionEvidence(Base):
    """Field-level provenance for PUBLIC offer extractions."""

    __tablename__ = "ingestion_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(80), index=True)
    value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_snippets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    offer_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Valuation(Base):
    """Point valuations (cents-per-point). effective = override ?? scraped (§5.5)."""

    __tablename__ = "valuation"

    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    cpp_scraped: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpp_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def cpp_effective(self) -> float | None:
        return self.cpp_override if self.cpp_override is not None else self.cpp_scraped


class SourceConfig(Base):
    """PUBLIC source registry used by discovery/refresh.

    The built-in config list is still a fallback, but the table lets the app add,
    disable, and prioritize sources without editing code.
    """

    __tablename__ = "source_config"
    __table_args__ = (
        UniqueConstraint("url", name="uq_source_config_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(Text)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="offer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TransferPartner(Base):
    """PUBLIC — transfer routes between owned currencies and award programs."""

    __tablename__ = "transfer_partner"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_currency: Mapped[str] = mapped_column(String(80))
    to_program: Mapped[str] = mapped_column(String(120))
    ratio: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. "1:1"
    # Time-limited transfer bonus (e.g. 30 for a "+30%" promo). The bonus is
    # applied on top of the base ratio while bonus_end_date has not passed;
    # an expired bonus is ignored automatically — no cleanup required.
    bonus_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    bonus_end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AwardBenchmark(Base):
    """PUBLIC — defined now, used in the later redemption phase (§5.9)."""

    __tablename__ = "award_benchmark"

    id: Mapped[int] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(120))
    cabin_or_tier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    program: Mapped[str | None] = mapped_column(String(120), nullable=True)
    est_cost_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


# ===========================================================================
#  PRIVATE domain  (🔒 = encrypted at rest)
# ===========================================================================
class HeldCard(Base):
    """A card a user actually holds. Timing here drives the eligibility engine."""

    __tablename__ = "held_card"

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(40), index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("card_product.id"), nullable=True
    )
    issuer: Mapped[str] = mapped_column(String(120))
    product_name: Mapped[str] = mapped_column(String(200))

    last4: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)  # 🔒
    date_opened: Mapped[dt.date] = mapped_column(Date)
    ownership: Mapped[str] = mapped_column(String(20), default="Personal")
    account_type: Mapped[str] = mapped_column(String(40), default="Credit Card")
    reports_to_personal_credit: Mapped[bool] = mapped_column(Boolean, default=True)

    credit_limit: Mapped[int | None] = mapped_column(EncryptedInt, nullable=True)  # 🔒
    annual_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    renewal_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    welcome_bonus_earned: Mapped[bool] = mapped_column(Boolean, default=False)
    bonus_points_earned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bonus_currency: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bonus_earned_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    # Min-spend tracking → deadline alerts before the bonus window closes.
    min_spend_requirement: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_spend_deadline: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    min_spend_progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_spend_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Manual, optional targeted/referral offer (PRIVATE — never scraped)
    my_targeted_offer_points: Mapped[int | None] = mapped_column(
        EncryptedInt, nullable=True
    )  # 🔒
    my_targeted_offer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(40), default="Active"
    )  # Active | Downgrade Pending | Cancel Pending | Closed
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class UserProfile(Base):
    """PRIVATE per-user state. five_24_count is computed (not stored)."""

    __tablename__ = "user_profile"

    user: Mapped[str] = mapped_column(String(40), primary_key=True)
    point_balances: Mapped[dict | None] = mapped_column(
        EncryptedJSON, nullable=True
    )  # 🔒 {currency: balance}
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BenefitUsage(Base):
    """PRIVATE per-user usage tracking for sourced card benefits/credits."""

    __tablename__ = "benefit_usage"
    __table_args__ = (
        UniqueConstraint("user", "held_card_id", "benefit_key", "period_key", name="uq_benefit_usage_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(40), index=True)
    held_card_id: Mapped[int] = mapped_column(ForeignKey("held_card.id"), index=True)
    benefit_key: Mapped[str] = mapped_column(String(180))
    benefit_name: Mapped[str] = mapped_column(String(240))
    period_key: Mapped[str] = mapped_column(String(40))
    period_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    amount_available: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_used: Mapped[float | None] = mapped_column(EncryptedFloat, nullable=True)  # 🔒
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)  # 🔒
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ManualTargetedOffer(Base):
    """PRIVATE per-user targeted offer for ANY product (held or not).

    Unlike the targeted offer stored on a HeldCard, this lets a user record a
    higher targeted/seen offer for a card they don't yet hold, so scoring &
    pipeline can factor it in. Figures are encrypted at rest.
    """

    __tablename__ = "manual_targeted_offer"
    __table_args__ = (
        UniqueConstraint("user", "issuer", "product_name", name="uq_targeted_offer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(40), index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("card_product.id"), nullable=True
    )
    issuer: Mapped[str] = mapped_column(String(120))
    product_name: Mapped[str] = mapped_column(String(200))
    offer_points: Mapped[int | None] = mapped_column(EncryptedInt, nullable=True)  # 🔒
    offer_cash: Mapped[float | None] = mapped_column(EncryptedFloat, nullable=True)  # 🔒
    expires_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TargetRedemption(Base):
    """PRIVATE — defined now, used in the later redemption phase (§5.8)."""

    __tablename__ = "target_redemption"

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(80), nullable=True)
    region: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cabin_or_tier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    preferred_programs: Mapped[str | None] = mapped_column(Text, nullable=True)
    est_cost_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_value_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_points_cpp: Mapped[float | None] = mapped_column(Float, nullable=True)
    travel_start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    travel_end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    passenger_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(60), nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
