"""Eligibility engine (§7) — computed from entered card dates. Timing-driven.

Every result is derived from ``HeldCard.date_opened`` /
``reports_to_personal_credit`` plus issuer rules. Rules are LLM-refreshable and
human-confirmed; see README §13 — verify 5/24, Amex velocity/lifetime, and the
Sapphire 48-month rule before relying on the pipeline.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..product_identity import product_display_name, product_variant_key

MONTHS_524 = 24
SAPPHIRE_MONTHS = 48
AMEX_VELOCITY_DAYS = 90
AMEX_CREDIT_LIMIT = 5
AMEX_CHARGE_LIMIT = 10
CITI_DAYS_BETWEEN = 8
CITI_TWO_WINDOW_DAYS = 65
CHASE_INK_DAYS = 90
BARCLAYS_REELIGIBILITY_MONTHS = 24
CITI_REELIGIBILITY_MONTHS = 24
AIRLINE_COBRAND_REELIGIBILITY_MONTHS = 24
CAPONE_VENTURE_BUSINESS_REELIGIBILITY_MONTHS = 48


def add_months(d: dt.date, months: int) -> dt.date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def _is(issuer: str, *names: str) -> bool:
    low = (issuer or "").lower()
    return any(n in low for n in names)


def _is_chase(issuer: str) -> bool:
    return _is(issuer, "chase")


def _is_amex(issuer: str) -> bool:
    return _is(issuer, "american express", "amex")


def _is_citi(issuer: str) -> bool:
    return _is(issuer, "citi")


def _is_capone(issuer: str) -> bool:
    return _is(issuer, "capital one", "capone")


def _is_barclays(issuer: str) -> bool:
    return "barclays" in (issuer or "").lower()


def _is_airline_cobrand(product_name: str) -> bool:
    low = (product_name or "").lower()
    return any(term in low for term in (
        "united", "southwest", "jetblue", "alaska", "hawaiian", "wyndham", "british airways",
    ))


def _same_product(held: models.HeldCard, issuer: str, product_name: str) -> bool:
    same_exact = (
        (held.issuer or "").strip().lower() == (issuer or "").strip().lower()
        and (held.product_name or "").strip().lower() == (product_name or "").strip().lower()
    )
    if same_exact:
        return True
    held_variant = product_variant_key(held.issuer, held.product_name)
    target_variant = product_variant_key(issuer, product_name)
    return bool(held_variant and target_variant and held_variant == target_variant)


def held_cards(db: Session, user: str) -> list[models.HeldCard]:
    return list(
        db.scalars(select(models.HeldCard).where(models.HeldCard.user == user)).all()
    )


# --- 5/24 -------------------------------------------------------------------
@dataclass
class Five24:
    count: int
    earliest_drop_date: dt.date | None
    contributing: list[dict] = field(default_factory=list)


def five24(
    db: Session,
    user: str,
    as_of: dt.date | None = None,
    held: list[models.HeldCard] | None = None,
) -> Five24:
    """Personal-credit-reporting openings (any issuer) in trailing 24 months.

    Pass ``held`` to reuse an already-loaded held-card list and avoid a query.
    """
    today = as_of or dt.date.today()
    window_start = add_months(today, -MONTHS_524)
    held = held if held is not None else held_cards(db, user)
    cards = [
        h
        for h in held
        if h.reports_to_personal_credit
        and h.date_opened
        and h.date_opened > window_start
    ]
    cards.sort(key=lambda h: h.date_opened, reverse=True)
    earliest = None
    if len(cards) >= 5:
        # The 5th-newest such card ages out of the 24-month window first.
        fifth_newest = cards[4]
        earliest = add_months(fifth_newest.date_opened, MONTHS_524)
    return Five24(
        count=len(cards),
        earliest_drop_date=earliest,
        contributing=[
            {
                "issuer": h.issuer,
                "product_name": h.product_name,
                "date_opened": h.date_opened.isoformat(),
            }
            for h in cards
        ],
    )


# --- Per-product eligibility ------------------------------------------------
@dataclass
class Eligibility:
    eligible: bool
    block_type: str  # none | permanent | temporary
    reasons: list[str]
    earliest_eligible_date: dt.date | None

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "block_type": self.block_type,
            "reasons": self.reasons,
            "earliest_eligible_date": self.earliest_eligible_date.isoformat()
            if self.earliest_eligible_date
            else None,
        }


def _max_date(a: dt.date | None, b: dt.date | None) -> dt.date | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def eligibility(
    db: Session,
    user: str,
    issuer: str,
    product_name: str,
    ownership: str = "Personal",
    as_of: dt.date | None = None,
    f24: Five24 | None = None,
    held: list[models.HeldCard] | None = None,
    eligibility_tags: list | None = None,
) -> Eligibility:
    today = as_of or dt.date.today()
    held = held if held is not None else held_cards(db, user)
    f24 = f24 or five24(db, user, as_of=today, held=held)

    reasons: list[str] = []
    permanent = False
    earliest: dt.date | None = None
    name_low = (product_name or "").lower()
    tags = {str(t).strip().lower() for t in (eligibility_tags or [])}

    # --- Card closed to new applicants (permanent) --------------------------
    if "closed_to_new_applicants" in tags or any("closed to new applicants" in t for t in tags):
        permanent = True
        reasons.append("Card is closed to new applicants — keep for history/retention only.")

    # --- Amex: once-per-lifetime (permanent) --------------------------------
    if _is_amex(issuer):
        earned = [
            h
            for h in held
            if _same_product(h, issuer, product_name) and h.welcome_bonus_earned
        ]
        if earned:
            permanent = True
            held_name = product_display_name(earned[0].issuer, earned[0].product_name) or earned[0].product_name
            reasons.append(
                "Amex once-per-lifetime: this profile has "
                f"{held_name} listed with a welcome bonus already earned. "
                "If that held-card row is wrong, correct the profile first."
            )

        # Velocity: 2 credit cards / 90 days
        amex_credit = sorted(
            [
                h
                for h in held
                if _is_amex(h.issuer)
                and h.account_type == "Credit Card"
                and h.date_opened
            ],
            key=lambda h: h.date_opened,
            reverse=True,
        )
        recent = [
            h
            for h in amex_credit
            if (today - h.date_opened).days < AMEX_VELOCITY_DAYS
        ]
        if len(recent) >= 2:
            # The older of the two in-window cards must age past 90 days first.
            drop = recent[1].date_opened + dt.timedelta(days=AMEX_VELOCITY_DAYS)
            earliest = _max_date(earliest, drop)
            reasons.append(
                "Amex velocity: 2 credit cards opened in the last 90 days "
                "(limit 2/90)."
            )
        # 5-credit-card limit
        open_amex_credit = [
            h
            for h in held
            if _is_amex(h.issuer)
            and h.account_type == "Credit Card"
            and h.status != "Closed"
        ]
        if ownership == "Personal" and len(open_amex_credit) >= AMEX_CREDIT_LIMIT:
            # Temporary block with no fixed date (clears when a card is closed).
            reasons.append(
                f"Amex 5-credit-card limit reached ({len(open_amex_credit)} held). "
                "Close one to add another."
            )

    # --- Chase 5/24 + Sapphire 48-month ------------------------------------
    if _is_chase(issuer):
        if f24.count >= 5:
            earliest = _max_date(earliest, f24.earliest_drop_date)
            reasons.append(
                f"Chase 5/24: {f24.count} personal-credit cards opened in the last "
                "24 months (must be under 5)."
            )
        if "sapphire" in name_low:
            target_variant = product_variant_key(issuer, product_name)
            target_is_personal = ownership == "Personal" and bool(
                target_variant and target_variant[1].startswith("chase_sapphire")
                and "business" not in target_variant[1]
            )
            active_personal_sapphire = [
                h
                for h in held
                for variant in [product_variant_key(h.issuer, h.product_name)]
                if h.status != "Closed"
                and variant
                and variant[1].startswith("chase_sapphire")
                and "business" not in variant[1]
            ]
            if target_is_personal and active_personal_sapphire:
                reasons.append(
                    "Chase one-Sapphire rule: an active personal Sapphire is already held; "
                    "review product-change or close/reapply strategy instead of a new application."
                )

            sapphire_held = [
                h
                for h in held
                if "sapphire" in (h.product_name or "").lower()
                and h.welcome_bonus_earned
                and h.bonus_earned_date
            ]
            for h in sapphire_held:
                again = add_months(h.bonus_earned_date, SAPPHIRE_MONTHS)
                if again > today:
                    earliest = _max_date(earliest, again)
                    reasons.append(
                        "Chase Sapphire 48-month rule: a Sapphire bonus was earned "
                        f"on {h.bonus_earned_date.isoformat()}; eligible again "
                        f"{again.isoformat()}."
                    )

        # Chase Ink ~90-day spacing between business-card approvals.
        if "chase_ink_90" in tags or "ink" in name_low:
            recent_ink = sorted(
                [
                    h
                    for h in held
                    if _is_chase(h.issuer)
                    and "ink" in (h.product_name or "").lower()
                    and h.date_opened
                    and (today - h.date_opened).days < CHASE_INK_DAYS
                ],
                key=lambda h: h.date_opened,
                reverse=True,
            )
            if recent_ink:
                drop = recent_ink[0].date_opened + dt.timedelta(days=CHASE_INK_DAYS)
                earliest = _max_date(earliest, drop)
                reasons.append(
                    "Chase Ink spacing: wait ~90 days after the most recent Ink approval."
                )

    # --- Citi: 8 days between apps, 65 between two --------------------------
    if _is_citi(issuer):
        citi = sorted(
            [h for h in held if _is_citi(h.issuer) and h.date_opened],
            key=lambda h: h.date_opened,
            reverse=True,
        )
        within8 = [h for h in citi if (today - h.date_opened).days < CITI_DAYS_BETWEEN]
        if within8:
            drop = within8[0].date_opened + dt.timedelta(days=CITI_DAYS_BETWEEN)
            earliest = _max_date(earliest, drop)
            reasons.append("Citi 8-day rule: a Citi card was opened in the last 8 days.")
        within65 = [
            h for h in citi if (today - h.date_opened).days < CITI_TWO_WINDOW_DAYS
        ]
        if len(within65) >= 2:
            drop = within65[1].date_opened + dt.timedelta(days=CITI_TWO_WINDOW_DAYS)
            earliest = _max_date(earliest, drop)
            reasons.append(
                "Citi 65-day rule: 2 Citi cards already opened in the last 65 days."
            )

    # --- Capital One: conservative re: recent inquiries (informational) ----
    if _is_capone(issuer):
        target_variant = product_variant_key(issuer, product_name)
        venture_bonus_variants = {
            ("capital_one", "capital_one_venture_personal"),
            ("capital_one", "capital_one_venture_x_personal"),
        }
        if target_variant in venture_bonus_variants:
            for h in held:
                held_variant = product_variant_key(h.issuer, h.product_name)
                if (
                    held_variant in venture_bonus_variants
                    and h.welcome_bonus_earned
                    and h.bonus_earned_date
                ):
                    again = add_months(h.bonus_earned_date, 48)
                    if again > today:
                        earliest = _max_date(earliest, again)
                        reasons.append(
                            "Capital One Venture-family 48-month rule: a Venture/Venture X bonus "
                            f"was earned on {h.bonus_earned_date.isoformat()}; eligible again "
                            f"{again.isoformat()}."
                        )

        recent_any = [
            h for h in held if h.date_opened and (today - h.date_opened).days < 90
        ]
        if len(recent_any) >= 3:
            reasons.append(
                "Capital One pulls all 3 bureaus and is sensitive to recent "
                f"inquiries ({len(recent_any)} new accounts in 90 days) — informational."
            )

    # --- Resolve ------------------------------------------------------------
    if permanent:
        return Eligibility(False, "permanent", reasons, None)

    has_temporary = any(
        kw in r.lower()
        for r in reasons
        for kw in (
            "5/24",
            "velocity",
            "48-month",
            "8-day",
            "65-day",
            "limit reached",
            "ink spacing",
            "one-sapphire",
        )
    )
    if has_temporary:
        return Eligibility(False, "temporary", reasons, earliest)

    return Eligibility(True, "none", reasons, None)


# --- Held-card re-eligibility ----------------------------------------------
def bonus_eligible_again(
    held: models.HeldCard, as_of: dt.date | None = None
) -> tuple[bool, dt.date | None]:
    """When (if ever) this held card's welcome bonus can be earned again."""
    today = as_of or dt.date.today()
    if not held.welcome_bonus_earned:
        return True, None

    if _is_amex(held.issuer):
        return False, None  # once per lifetime

    if _is_chase(held.issuer) and "sapphire" in (held.product_name or "").lower():
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, SAPPHIRE_MONTHS)
            return (again <= today), again

    if _is_barclays(held.issuer):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, BARCLAYS_REELIGIBILITY_MONTHS)
            return (again <= today), again

    if _is_citi(held.issuer):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, CITI_REELIGIBILITY_MONTHS)
            return (again <= today), again

    if (
        _is_capone(held.issuer)
        and "venture" in (held.product_name or "").lower()
        and "business" in (held.product_name or "").lower()
    ):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, CAPONE_VENTURE_BUSINESS_REELIGIBILITY_MONTHS)
            return (again <= today), again

    if _is_airline_cobrand(held.product_name):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, AIRLINE_COBRAND_REELIGIBILITY_MONTHS)
            return (again <= today), again

    # Generic: re-eligibility window unknown without a confirmed rule.
    return False, None
