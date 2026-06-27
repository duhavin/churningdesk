import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.logic import eligibility, household


class HouseholdOrderingTests(unittest.TestCase):
    def setUp(self):
        self._old_users = config.USERS
        config.USERS = ["User A", "User B"]

    def tearDown(self):
        config.USERS = self._old_users

    def test_household_moves_preserve_pipeline_rotation_before_raw_value(self):
        db = self._session()
        chase = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred",
            currency="Chase Ultimate Rewards",
            annual_fee=95,
            current_offer_points=80_000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            peak_offer_points=100_000,
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        amex = models.CardProduct(
            issuer="American Express",
            product_name="Platinum Card",
            currency="Amex Membership Rewards",
            annual_fee=0,
            current_offer_points=100_000,
            current_offer_min_spend=8000,
            current_offer_window_months=6,
            peak_offer_points=125_000,
            source_url="https://www.americanexpress.com/us/credit-cards/card/platinum/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                chase,
                amex,
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0),
                models.Valuation(currency="Amex Membership Rewards", cpp_scraped=2.0),
            ]
        )
        db.commit()

        result = household.build_household(db)
        moves = result["moves"]

        self.assertGreater(moves[2]["household_value"], moves[0]["household_value"])
        self.assertEqual(moves[0]["issuer"], "Chase")
        self.assertEqual(moves[0]["pipeline_rank"], 1)
        self.assertEqual(moves[1]["issuer"], "Chase")
        self.assertEqual(moves[2]["issuer"], "American Express")

    def test_capital_one_venture_family_rule_and_household_referral_route(self):
        db = self._session()
        today = dt.date.today()
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=95,
            current_offer_points=75000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=75000,
            source_url="https://www.capitalone.com/credit-cards/venture/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        venture_x = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=395,
            current_offer_points=75000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=90000,
            referral_bonus_points=25000,
            source_url="https://www.capitalone.com/credit-cards/venture-x/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                venture,
                venture_x,
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.0),
            ]
        )
        db.commit()
        db.refresh(venture_x)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Capital One Venture X Rewards Credit Card",
                product_id=venture_x.id,
                date_opened=today - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                bonus_earned_date=today - dt.timedelta(days=365),
                status="Active",
            )
        )
        db.commit()

        same_user = eligibility.eligibility(
            db,
            "User A",
            "Capital One",
            "Capital One Venture Rewards Credit Card",
        )
        plan = household.build_household(db)
        venture_referral = next(
            row
            for row in plan["referrals"]
            if row["to_user"] == "User B" and row["product_name"] == "Capital One Venture Rewards Credit Card"
        )

        self.assertFalse(same_user.eligible)
        self.assertEqual(same_user.block_type, "temporary")
        self.assertTrue(any("48-month" in reason for reason in same_user.reasons))
        self.assertEqual(venture_referral["from_user"], "User A")
        self.assertEqual(venture_referral["referral_match"], "family")
        self.assertIn("Refer via User A", venture_referral["route"])

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
