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


if __name__ == "__main__":
    unittest.main()
