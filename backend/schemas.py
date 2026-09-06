"""Pydantic request/response schemas.

Computed-heavy responses (scored catalog, pipeline, eligibility) are returned as
plain dicts from the logic layer; these schemas mainly validate inputs and shape
the simpler outputs.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


# --- Held cards (PRIVATE) ---------------------------------------------------
class HeldCardBase(BaseModel):
    user: str
    issuer: str
    product_name: str
    product_id: int | None = None
    last4: str | None = None
    date_opened: dt.date
    ownership: str = "Personal"
    account_type: str = "Credit Card"
    reports_to_personal_credit: bool = True
    credit_limit: int | None = None
    annual_fee: float | None = None
    renewal_date: dt.date | None = None
    welcome_bonus_earned: bool = False
    bonus_points_earned: int | None = None
    bonus_currency: str | None = None
    bonus_earned_date: dt.date | None = None
    min_spend_requirement: float | None = None
    min_spend_deadline: dt.date | None = None
    min_spend_progress: float | None = None
    min_spend_completed: bool = False
    my_targeted_offer_points: int | None = None
    my_targeted_offer_notes: str | None = None
    status: str = "Active"
    notes: str | None = None


class HeldCardCreate(HeldCardBase):
    pass


class HeldCardUpdate(BaseModel):
    issuer: str | None = None
    product_name: str | None = None
    product_id: int | None = None
    last4: str | None = None
    date_opened: dt.date | None = None
    ownership: str | None = None
    account_type: str | None = None
    reports_to_personal_credit: bool | None = None
    credit_limit: int | None = None
    annual_fee: float | None = None
    renewal_date: dt.date | None = None
    welcome_bonus_earned: bool | None = None
    bonus_points_earned: int | None = None
    bonus_currency: str | None = None
    bonus_earned_date: dt.date | None = None
    min_spend_requirement: float | None = None
    min_spend_deadline: dt.date | None = None
    min_spend_progress: float | None = None
    min_spend_completed: bool | None = None
    my_targeted_offer_points: int | None = None
    my_targeted_offer_notes: str | None = None
    status: str | None = None
    notes: str | None = None


# --- Catalog (PUBLIC) -------------------------------------------------------
class CardProductCreate(BaseModel):
    issuer: str
    product_name: str
    product_family: str | None = None
    ownership: str = "Personal"
    account_type: str = "Credit Card"
    currency: str | None = None
    reports_to_personal_credit: bool = True
    annual_fee: float | None = None
    current_offer_points: int | None = None
    current_offer_override: int | None = None
    current_offer_cash: float | None = None
    current_offer_min_spend: float | None = None
    current_offer_window_months: int | None = None
    offer_expiration: str | None = None
    peak_offer_points: int | None = None
    peak_offer_min_spend: float | None = None
    peak_offer_source: str | None = None
    peak_offer_date: str | None = None
    targeted_peak_offer_points: int | None = None
    targeted_peak_offer_cash: float | None = None
    targeted_peak_offer_source: str | None = None
    targeted_peak_offer_date: str | None = None
    referral_bonus_points: int | None = None
    referral_bonus_override: int | None = None
    referral_bonus_cash: float | None = None
    first_year_credit_value: float | None = None
    earn_multipliers: dict | None = None
    best_category_uses: dict | None = None
    card_benefits: list | None = None
    downgrade_paths: list | None = None
    eligibility_tags: list | None = None
    tag: str | None = None
    added_by: str = "user_watchlist"
    source_url: str | None = None
    notes: str | None = None


class CardProductUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    issuer: str | None = None
    product_name: str | None = None
    product_family: str | None = None
    ownership: str | None = None
    account_type: str | None = None
    currency: str | None = None
    reports_to_personal_credit: bool | None = None
    annual_fee: float | None = None
    current_offer_points: int | None = None
    current_offer_override: int | None = None
    current_offer_cash: float | None = None
    current_offer_min_spend: float | None = None
    current_offer_window_months: int | None = None
    offer_expiration: str | None = None
    peak_offer_points: int | None = None
    peak_offer_min_spend: float | None = None
    peak_offer_source: str | None = None
    peak_offer_date: str | None = None
    targeted_peak_offer_points: int | None = None
    targeted_peak_offer_cash: float | None = None
    targeted_peak_offer_source: str | None = None
    targeted_peak_offer_date: str | None = None
    referral_bonus_points: int | None = None
    referral_bonus_override: int | None = None
    referral_bonus_cash: float | None = None
    first_year_credit_value: float | None = None
    earn_multipliers: dict | None = None
    best_category_uses: dict | None = None
    card_benefits: list | None = None
    downgrade_paths: list | None = None
    eligibility_tags: list | None = None
    tag: str | None = None
    discovery_reviewed: bool | None = None
    source_url: str | None = None
    notes: str | None = None


class CardReferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    aliases: list[str] | None = None
    search_terms: list[str] | None = None
    issuer_url: str | None = None
    offer_url: str | None = None
    history_url: str | None = None
    benefits_url: str | None = None
    trusted_source_urls: list[str] | None = None
    learned_source_urls: list[str] | None = None
    active: bool | None = None


# --- Watchlist / blacklist --------------------------------------------------
class WatchlistCreate(BaseModel):
    issuer: str
    product_name: str
    priority: bool = False
    notes: str | None = None


class BlacklistCreate(BaseModel):
    issuer: str
    product_name: str
    reason: str | None = None


# --- Valuations -------------------------------------------------------------
class ValuationUpsert(BaseModel):
    currency: str
    cpp_scraped: float | None = None
    cpp_override: float | None = None
    source_url: str | None = None


# --- Profiles ---------------------------------------------------------------
class ProfileUpsert(BaseModel):
    point_balances: dict[str, float] | None = None
    organic_monthly_capacity: FiniteFloat | None = Field(default=None, ge=0)
    notes: str | None = None


class BenefitUsageUpsert(BaseModel):
    held_card_id: int
    benefit_key: str
    benefit_name: str
    period_key: str
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    amount_available: float | None = None
    amount_used: float | None = None
    suppressed: bool | None = None
    suppress_all: bool = False
    notes: str | None = None


# --- Manual targeted offers (PRIVATE) ---------------------------------------
class ManualTargetedOfferUpsert(BaseModel):
    user: str
    issuer: str
    product_name: str
    product_id: int | None = None
    offer_points: int | None = None
    offer_cash: float | None = None
    expires_at: dt.date | None = None
    notes: str | None = None


class ManualTargetedOfferRead(ManualTargetedOfferUpsert):
    id: int
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


class SourceCreate(BaseModel):
    name: str
    url: str
    product_id: int | None = None
    kind: str = "offer"
    active: bool = True
    priority: int = Field(default=3, ge=1, le=5)


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    product_id: int | None = None
    kind: str | None = None
    active: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=5)


# --- Redemption -------------------------------------------------------------
class TargetRedemptionCreate(BaseModel):
    user: str = "Household"
    name: str
    origin: str | None = None
    destination: str | None = None
    region: str | None = None
    cabin_or_tier: str | None = None
    preferred_programs: str | None = None
    est_cost_points: int | None = None
    target_value_cash: float | None = None
    buy_points_cpp: float | None = None
    travel_start_date: dt.date | None = None
    travel_end_date: dt.date | None = None
    passenger_count: int | None = Field(default=None, ge=1, le=9)
    frequency: str | None = None
    priority: int | None = None
    notes: str | None = None


class TargetRedemptionUpdate(BaseModel):
    user: str | None = None
    name: str | None = None
    origin: str | None = None
    destination: str | None = None
    region: str | None = None
    cabin_or_tier: str | None = None
    preferred_programs: str | None = None
    est_cost_points: int | None = None
    target_value_cash: float | None = None
    buy_points_cpp: float | None = None
    travel_start_date: dt.date | None = None
    travel_end_date: dt.date | None = None
    passenger_count: int | None = Field(default=None, ge=1, le=9)
    frequency: str | None = None
    priority: int | None = None
    notes: str | None = None


class TransferPartnerCreate(BaseModel):
    from_currency: str
    to_program: str
    ratio: str | None = "1:1"
    bonus_pct: float | None = Field(default=None, ge=0, le=200)
    bonus_end_date: dt.date | None = None
    source_url: str | None = None


class TransferPartnerUpdate(BaseModel):
    from_currency: str | None = None
    to_program: str | None = None
    ratio: str | None = None
    bonus_pct: float | None = Field(default=None, ge=0, le=200)
    bonus_end_date: dt.date | None = None
    source_url: str | None = None


# --- Proposed changes -------------------------------------------------------
class ProposedDecision(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")


# --- Run actions ------------------------------------------------------------
class DiscoverRequest(BaseModel):
    issuers: list[str] | None = None  # narrow/widen scope; defaults to config


class RefreshRequest(BaseModel):
    issuer: str | None = None
    product_ids: list[int] | None = None
    source_urls: list[str] | None = None
    use_web_search: bool = True
    refresh_stale_days: int | None = Field(default=3, ge=0)
    force: bool = False
    batch_size: int | None = Field(default=None, ge=1, le=10)
    limit: int | None = Field(default=None, ge=1, le=100)
    web_fallback_limit: int | None = Field(default=None, ge=0, le=100)
    llm_fallback: bool = True
    include_incomplete: bool = True
    incomplete_only: bool = False
    peak_backfill: bool = False
    refresh_valuations: bool = False
    valuations_only: bool = False
    use_rendered_fallback: bool = True
    background: bool = False
    # Backward-compatible flag used by the existing frontend.
    only_stale: bool | None = True
