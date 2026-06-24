import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.logic import household


class HouseholdOrderingTests(unittest.TestCase):
    def test_household_moves_preserve_pipeline_rotation_before_raw_value(self):
        db = self._session()
        chase = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred",
            currency="Chase Ultimate Rewards",
            annual_fee=95,
            current_offer_points=80_000,
            peak_offer_points=100_000,
        )
        amex = models.CardProduct(
            issuer="American Express",
            product_name="Platinum Card",
            currency="Amex Membership Rewards",
            annual_fee=0,
            current_offer_points=100_000,
            peak_offer_points=125_000,
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

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
