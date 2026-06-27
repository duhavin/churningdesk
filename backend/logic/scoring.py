"""Apply scoring + ranking (§6) — scaled, not binary.

peak_score = closeness of the effective offer to its all-time peak (0-100).
offer_value = dollar value of the offer net of annual fee.
status      = derived from peak_score + eligibility (FUTURE/SKIP/WAIT/...).
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import config, models

# Status constants
APPLY_NOW = "APPLY NOW"
WATCH = "WATCH"
WAIT = "WAIT"
LOW_PRIORITY = "LOW PRIORITY"
SKIP = "SKIP"
FUTURE = "FUTURE"
NEEDS_DATA = "NEEDS DATA"


@dataclass
class Score:
    peak_score: int
    offer_value: float
    effective_points: int
    targeted_beats_public: bool
    status: str
    rank_value: float  # used for ranking; offer_value for APPLY NOW / WATCH
    needs_data: bool = False
    value_known: bool = True
    peak_is_targeted: bool = False
    is_exceptional: bool = False


def _cpp(valuations: dict[str, float], currency: str | None) -> float:
    if not currency:
        return 0.0
    return valuations.get(currency.strip().lower(), 0.0)


def compute_score(
    product: models.CardProduct,
    valuations: dict[str, float],
    eligibility: dict,
    my_targeted_offer_points: int | None = None,
) -> Score:
    """valuations: {currency_lower: cpp}. eligibility: dict from eligibility engine."""
    current_public = (product.current_offer_effective or product.current_offer_points) or 0
    targeted = my_targeted_offer_points or 0
    effective_points = max(current_public, targeted)
    targeted_beats_public = targeted > current_public

    public_peak = product.peak_offer_points or 0
    targeted_peak = product.targeted_peak_offer_points or 0
    # Public peak is the scoring denominator when known. Targeted/incognito
    # peaks are informational unless there is no public peak yet.
    peak = public_peak or targeted_peak
    peak_is_targeted = peak > 0 and public_peak == 0
    peak_score = round(min(effective_points / peak, 1.0) * 100) if peak > 0 else 0
    is_exceptional = _is_exceptional_offer(effective_points, peak, peak_score)

    has_bonus_offer = bool(effective_points or (product.current_offer_cash or 0))

    # cpp is cents-per-point; divide by 100 so offer_value is in dollars.
    cpp = _cpp(valuations, product.currency)
    # Don't show a misleading value when we can't actually value the offer.
    value_known = bool(
        (product.current_offer_cash or 0)
        or (product.first_year_credit_value or 0)
        or (effective_points and cpp > 0)
    )
    offer_value = (
        (
            effective_points * cpp / 100.0
            + (product.current_offer_cash or 0.0)
            + (product.first_year_credit_value or 0.0)
            - (product.annual_fee or 0.0)
        )
        if (has_bonus_offer and value_known)
        else 0.0
    )

    points_need_valuation = bool(effective_points and cpp <= 0)
    needs_data = not has_bonus_offer or points_need_valuation
    status = _status(
        product,
        peak_score,
        offer_value,
        effective_points,
        eligibility,
        needs_data,
        value_known=value_known,
        public_peak_known=public_peak > 0,
    )

    return Score(
        peak_score=peak_score,
        offer_value=round(offer_value, 2),
        effective_points=effective_points,
        targeted_beats_public=targeted_beats_public,
        status=status,
        rank_value=round(offer_value, 2),
        needs_data=needs_data,
        value_known=value_known,
        peak_is_targeted=peak_is_targeted,
        is_exceptional=is_exceptional,
    )


def _is_exceptional_offer(effective_points: int, peak: int, peak_score: int) -> bool:
    if effective_points >= config.EXCEPTIONAL_PEAK_POINTS:
        return True
    return bool(
        peak >= config.EXCEPTIONAL_PEAK_POINTS
        and effective_points > 0
        and peak_score >= config.APPLY_NOW_THRESHOLD
    )


def _status(
    product: models.CardProduct,
    peak_score: int,
    offer_value: float,
    effective_points: int,
    eligibility: dict,
    needs_data: bool = False,
    value_known: bool = True,
    public_peak_known: bool = True,
) -> str:
    # 1. future_trip tagged, not yet time
    if product.tag == "future_trip":
        return FUTURE

    # 2. not eligible
    if not eligibility.get("eligible", True):
        return SKIP if eligibility.get("block_type") == "permanent" else WAIT

    # 3. eligible but we can't trust the current offer/value yet.
    if needs_data:
        return NEEDS_DATA

    # Missing public peak means timing/hotness is unknown, not that a sourced
    # current offer is unusable. Keep it below APPLY NOW until history is known.
    if not public_peak_known:
        if offer_value < 0:
            return LOW_PRIORITY
        if value_known and (offer_value >= config.MIN_WATCH_VALUE or effective_points >= config.MIN_APPLY_POINTS):
            return WATCH
        return LOW_PRIORITY

    # 4-7. eligible — by peak_score
    if offer_value < 0:
        return LOW_PRIORITY
    if peak_score >= config.APPLY_NOW_THRESHOLD:
        if value_known and (
            offer_value >= config.MIN_APPLY_VALUE or effective_points >= config.MIN_APPLY_POINTS
        ):
            return APPLY_NOW
        return WATCH if offer_value >= config.MIN_WATCH_VALUE else LOW_PRIORITY
    if peak_score >= config.WATCH_THRESHOLD:
        return WATCH if offer_value >= config.MIN_WATCH_VALUE else LOW_PRIORITY
    if peak_score >= config.WAIT_THRESHOLD:
        return WAIT
    return LOW_PRIORITY
