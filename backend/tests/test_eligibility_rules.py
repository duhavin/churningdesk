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
        card = models.HeldCard(
            user="User A", issuer=issuer, product_name=name,
            date_opened=dt.date.today() - dt.timedelta(days=days_ago),
            status="Active", **kw,
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



if __name__ == "__main__":
    unittest.main()
