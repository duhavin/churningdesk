import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.logic import eligibility


class EligibilityRuleTests(unittest.TestCase):
    def test_amex_lifetime_is_exact_product_not_personal_business_family(self):
        db = self._session()
        db.add(
            models.HeldCard(
                user="User A",
                issuer="American Express",
                product_name="American Express Gold Card",
                date_opened=dt.date.today() - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                status="Active",
            )
        )
        db.commit()

        personal = eligibility.eligibility(
            db,
            "User A",
            "American Express",
            "American Express Gold Card",
            ownership="Personal",
        )
        business = eligibility.eligibility(
            db,
            "User A",
            "American Express",
            "American Express Business Gold Card",
            ownership="Business",
        )

        self.assertFalse(personal.eligible)
        self.assertEqual(personal.block_type, "permanent")
        self.assertTrue(any("this profile has Amex Gold listed" in reason for reason in personal.reasons))
        self.assertTrue(business.eligible)
        self.assertEqual(business.block_type, "none")
        self.assertFalse(any("once-per-lifetime" in reason for reason in business.reasons))

    def test_amex_lifetime_reason_names_business_profile_row(self):
        db = self._session()
        db.add(
            models.HeldCard(
                user="User B",
                issuer="American Express",
                product_name="Amex Business Gold",
                date_opened=dt.date.today() - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                status="Active",
            )
        )
        db.commit()

        result = eligibility.eligibility(
            db,
            "User B",
            "American Express",
            "American Express Business Gold Card",
            ownership="Business",
        )

        self.assertFalse(result.eligible)
        self.assertTrue(any("this profile has Amex Business Gold listed" in reason for reason in result.reasons))
        self.assertTrue(any("correct the profile first" in reason for reason in result.reasons))

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


class VelocityRuleTests(unittest.TestCase):
    """Issuer velocity rules that prevent wasted applications (2026-07-06)."""

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def _held(self, db, issuer, name, days_ago, **kw):
        kw.setdefault("status", "Active")
        card = models.HeldCard(
            user="User A", issuer=issuer, product_name=name,
            date_opened=dt.date.today() - dt.timedelta(days=days_ago),
            **kw,
        )
        db.add(card)
        db.commit()
        return card

    def test_chase_2_30_blocks_third_application(self):
        db = self._session()
        self._held(db, "Chase", "Freedom Flex", 5)
        self._held(db, "Chase", "Freedom Unlimited Credit Card", 20)
        result = eligibility.eligibility(db, "User A", "Chase", "Sapphire Preferred Card", ownership="Personal")
        self.assertFalse(result.eligible)
        self.assertTrue(any("2/30" in r for r in result.reasons))
        # The older in-window card ages out first: drop = opened + 30 days.
        expected = (dt.date.today() - dt.timedelta(days=20)) + dt.timedelta(days=30)
        self.assertEqual(result.earliest_eligible_date, expected)

    def test_chase_2_30_allows_when_one_recent(self):
        db = self._session()
        self._held(db, "Chase", "Freedom Flex", 5)
        self._held(db, "Chase", "Freedom Unlimited Credit Card", 45)
        result = eligibility.eligibility(db, "User A", "Chase", "Sapphire Preferred Card", ownership="Personal")
        self.assertFalse(any("2/30" in r for r in result.reasons))

    def test_capital_one_six_month_spacing(self):
        db = self._session()
        self._held(db, "Capital One", "Savor Cash Rewards Credit Card", 90)
        result = eligibility.eligibility(db, "User A", "Capital One", "Venture X Rewards Credit Card", ownership="Personal")
        self.assertFalse(result.eligible)
        self.assertTrue(any("6 months" in r for r in result.reasons))
        expected = (dt.date.today() - dt.timedelta(days=90)) + dt.timedelta(days=182)
        self.assertEqual(result.earliest_eligible_date, expected)

    def test_capital_one_clear_after_window(self):
        db = self._session()
        self._held(db, "Capital One", "Savor Cash Rewards Credit Card", 200)
        result = eligibility.eligibility(db, "User A", "Capital One", "Venture X Rewards Credit Card", ownership="Personal")
        self.assertFalse(any("6 months" in r for r in result.reasons))

    def test_chase_2_30_counts_closed_cards(self):
        db = self._session()
        self._held(db, "Chase", "Freedom Flex", 5)
        self._held(db, "Chase", "Freedom Unlimited Credit Card", 10, status="Closed")
        result = eligibility.eligibility(db, "User A", "Chase", "Sapphire Preferred Card", ownership="Personal")
        self.assertFalse(result.eligible)
        self.assertTrue(any("2/30" in r for r in result.reasons))

    def test_amex_five_card_ceiling_blocks_business_target(self):
        db = self._session()
        names = [
            "The Platinum Card",
            "American Express Gold Card",
            "Delta SkyMiles Gold American Express Card",
            "Hilton Honors American Express Surpass Card",
            "Amex EveryDay Credit Card",
        ]
        for i, name in enumerate(names):
            self._held(db, "American Express", name, 400 + i * 100)
        result = eligibility.eligibility(
            db,
            "User A",
            "American Express",
            "American Express Business Gold Card",
            ownership="Business",
        )
        self.assertFalse(result.eligible)
        self.assertTrue(any("5-credit-card limit" in r for r in result.reasons))


class ReBonusWindowTests(unittest.TestCase):
    """Issuer re-bonus windows gate the apply path — including closed cards."""

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def _closed_with_bonus(self, db, issuer, name, bonus_months_ago, **kw):
        today = dt.date.today()
        card = models.HeldCard(
            user="User A",
            issuer=issuer,
            product_name=name,
            date_opened=today - dt.timedelta(days=1600),
            welcome_bonus_earned=True,
            bonus_earned_date=eligibility.add_months(today, -bonus_months_ago),
            status="Closed",
            **kw,
        )
        db.add(card)
        db.commit()
        return card

    def _eligibility(self, db, issuer, name, **kw):
        return eligibility.eligibility(db, "User A", issuer, name, **kw)

    def test_citi_closed_card_bonus_90_days_ago_blocks_reapply(self):
        db = self._session()
        today = dt.date.today()
        card = models.HeldCard(
            user="User A",
            issuer="Citi",
            product_name="Citi Strata Premier Card",
            date_opened=today - dt.timedelta(days=1600),
            welcome_bonus_earned=True,
            bonus_earned_date=today - dt.timedelta(days=90),
            status="Closed",
        )
        db.add(card)
        db.commit()

        result = self._eligibility(db, "Citi", "Citi Strata Premier Card")

        expected = eligibility.add_months(today - dt.timedelta(days=90), 24)
        self.assertFalse(result.eligible)
        self.assertEqual(result.block_type, "temporary")
        self.assertTrue(any(expected.isoformat() in r for r in result.reasons))
        self.assertEqual(result.earliest_eligible_date, expected)

    def test_citi_closed_card_eligible_after_24_months(self):
        db = self._session()
        self._closed_with_bonus(db, "Citi", "Citi Strata Premier Card", 25)
        result = self._eligibility(db, "Citi", "Citi Strata Premier Card")
        self.assertTrue(result.eligible)

    def test_citi_unknown_bonus_date_is_conservatively_blocked(self):
        db = self._session()
        today = dt.date.today()
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Citi",
                product_name="Citi Strata Premier Card",
                date_opened=today - dt.timedelta(days=1600),
                welcome_bonus_earned=True,
                bonus_earned_date=None,
                status="Closed",
            )
        )
        db.commit()
        result = self._eligibility(db, "Citi", "Citi Strata Premier Card")
        self.assertFalse(result.eligible)
        self.assertTrue(any("bonus date is unknown" in r for r in result.reasons))
        self.assertIsNone(result.earliest_eligible_date)

    def test_barclays_24_month_window(self):
        db = self._session()
        self._closed_with_bonus(db, "Barclays", "JetBlue Plus Card", 23)
        blocked = self._eligibility(db, "Barclays", "JetBlue Plus Card")
        self.assertFalse(blocked.eligible)
        self.assertTrue(any("24-month rule" in r for r in blocked.reasons))

        db2 = self._session()
        self._closed_with_bonus(db2, "Barclays", "JetBlue Plus Card", 25)
        clear = self._eligibility(db2, "Barclays", "JetBlue Plus Card")
        self.assertTrue(clear.eligible)

    def test_airline_cobrand_24_month_window(self):
        db = self._session()
        self._closed_with_bonus(db, "Chase", "United Explorer Card", 23)
        blocked = self._eligibility(db, "Chase", "United Explorer Card")
        self.assertFalse(blocked.eligible)
        self.assertTrue(any("24-month rule" in r for r in blocked.reasons))

        db2 = self._session()
        self._closed_with_bonus(db2, "Chase", "United Explorer Card", 25)
        clear = self._eligibility(db2, "Chase", "United Explorer Card")
        self.assertTrue(clear.eligible)

    def test_airline_cobrand_fallback_issuer_24_month_window(self):
        db = self._session()
        self._closed_with_bonus(db, "Bank of America", "Alaska Airlines Visa Signature", 23)
        blocked = self._eligibility(db, "Bank of America", "Alaska Airlines Visa Signature")
        self.assertFalse(blocked.eligible)
        self.assertTrue(any("Airline co-brand 24-month rule" in r for r in blocked.reasons))

        db2 = self._session()
        self._closed_with_bonus(db2, "Bank of America", "Alaska Airlines Visa Signature", 25)
        clear = self._eligibility(db2, "Bank of America", "Alaska Airlines Visa Signature")
        self.assertTrue(clear.eligible)

    def test_capone_venture_business_48_month_window(self):
        db = self._session()
        self._closed_with_bonus(db, "Capital One", "Capital One Venture X Business", 47)
        blocked = self._eligibility(
            db, "Capital One", "Capital One Venture X Business", ownership="Business"
        )
        self.assertFalse(blocked.eligible)
        self.assertTrue(any("48-month rule" in r for r in blocked.reasons))

        db2 = self._session()
        self._closed_with_bonus(db2, "Capital One", "Capital One Venture X Business", 49)
        clear = self._eligibility(
            db2, "Capital One", "Capital One Venture X Business", ownership="Business"
        )
        self.assertTrue(clear.eligible)

    def test_generic_chase_24_month_window_non_sapphire(self):
        db = self._session()
        self._closed_with_bonus(db, "Chase", "Freedom Unlimited Credit Card", 23)
        blocked = self._eligibility(db, "Chase", "Freedom Unlimited Credit Card")
        self.assertFalse(blocked.eligible)
        self.assertTrue(any("Chase 24-month rule" in r for r in blocked.reasons))

        db2 = self._session()
        self._closed_with_bonus(db2, "Chase", "Freedom Unlimited Credit Card", 25)
        clear = self._eligibility(db2, "Chase", "Freedom Unlimited Credit Card")
        self.assertTrue(clear.eligible)

    def test_sapphire_window_stays_48_months(self):
        db = self._session()
        self._closed_with_bonus(db, "Chase", "Sapphire Preferred Card", 47)
        blocked = self._eligibility(db, "Chase", "Sapphire Preferred Card")
        self.assertFalse(blocked.eligible)
        self.assertTrue(any("Chase Sapphire 48-month rule" in r for r in blocked.reasons))

        db2 = self._session()
        self._closed_with_bonus(db2, "Chase", "Sapphire Preferred Card", 49)
        clear = self._eligibility(db2, "Chase", "Sapphire Preferred Card")
        self.assertTrue(clear.eligible)

    def test_amex_stays_lifetime(self):
        db = self._session()
        self._closed_with_bonus(db, "American Express", "American Express Gold Card", 60)
        result = self._eligibility(db, "American Express", "American Express Gold Card")
        self.assertFalse(result.eligible)
        self.assertEqual(result.block_type, "permanent")

    def test_personal_venture_requeues_after_48_months(self):
        today = dt.date.today()

        def _venture(months_ago):
            return models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Capital One Venture Rewards Credit Card",
                date_opened=today - dt.timedelta(days=1600),
                welcome_bonus_earned=True,
                bonus_earned_date=eligibility.add_months(today, -months_ago),
                status="Active",
            )

        ok_50, again_50 = eligibility.bonus_eligible_again(_venture(50), as_of=today)
        ok_40, again_40 = eligibility.bonus_eligible_again(_venture(40), as_of=today)

        self.assertTrue(ok_50)
        self.assertIsNotNone(again_50)
        self.assertFalse(ok_40)
        self.assertEqual(again_40, eligibility.add_months(eligibility.add_months(today, -40), 48))


if __name__ == "__main__":
    unittest.main()
