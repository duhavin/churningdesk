import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.logic import categories


class CategoryGuideTests(unittest.TestCase):
    def test_everyday_card_covers_missing_category_without_repeating_multiplier(self):
        db = self._session()
        venture = self._product(
            db,
            issuer="Capital One",
            product_name="Venture X",
            currency="Capital One Miles",
            earn_multipliers={"everyday": 2},
        )
        self._held(db, "User A", venture)
        db.add(models.Valuation(currency="Capital One Miles", cpp_scraped=1.7))
        db.commit()

        guide = categories.build_category_guide(db)
        gas = next(row for row in guide["categories"] if row["category"] == "gas")

        self.assertEqual(gas["status"], "covered")
        self.assertEqual(gas["winner"]["display_name"], "Venture X")
        self.assertEqual(gas["winner"]["multiplier"], 2)
        self.assertTrue(gas["winner"]["is_fallback"])
        self.assertEqual(gas["winner"]["source_category"], "everyday")

        profile_rows = categories.build_user_category_coverage(db, "User A")
        profile_gas = next(row for row in profile_rows if row["category"] == "gas")
        self.assertTrue(profile_gas["covered"])
        self.assertEqual(profile_gas["multiplier"], 2)
        self.assertTrue(profile_gas["is_fallback"])

    def test_profile_and_household_use_same_effective_rate_rules(self):
        db = self._session()
        cash = self._product(
            db,
            issuer="Cash Bank",
            product_name="Cash Dining",
            currency="cash back",
            earn_multipliers={"dining": 3},
        )
        points = self._product(
            db,
            issuer="Chase",
            product_name="Sapphire Preferred",
            currency="Chase Ultimate Rewards",
            earn_multipliers={"dining": 2},
        )
        self._held(db, "User A", cash)
        self._held(db, "User A", points)
        db.add(models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0))
        db.commit()

        household = categories.build_category_guide(db)
        profile = categories.build_user_category_coverage(db, "User A")
        household_dining = next(row for row in household["categories"] if row["category"] == "dining")
        profile_dining = next(row for row in profile if row["category"] == "dining")

        self.assertEqual(household_dining["winner"]["display_name"], "Sapphire Preferred")
        self.assertEqual(profile_dining["display_name"], "Sapphire Preferred")
        self.assertEqual(household_dining["winner"]["effective_rate_cents"], profile_dining["effective_rate_cents"])

    def test_specific_category_bonus_beats_everyday_baseline(self):
        db = self._session()
        venture = self._product(
            db,
            issuer="Capital One",
            product_name="Venture X",
            currency="Capital One Miles",
            earn_multipliers={"everyday": 2},
        )
        freedom = self._product(
            db,
            issuer="Chase",
            product_name="Freedom Flex",
            currency="Chase Ultimate Rewards",
            earn_multipliers={"gas": 5, "everyday": 1},
        )
        self._held(db, "User A", venture)
        self._held(db, "User A", freedom)
        db.add_all(
            [
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.7),
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.8),
            ]
        )
        db.commit()

        guide = categories.build_category_guide(db)
        gas = next(row for row in guide["categories"] if row["category"] == "gas")

        self.assertEqual(gas["winner"]["display_name"], "Freedom Flex")
        self.assertEqual(gas["winner"]["multiplier"], 5)
        self.assertFalse(gas["winner"]["is_fallback"])

    def test_everyday_baseline_can_beat_weak_same_card_category_entry(self):
        db = self._session()
        venture = self._product(
            db,
            issuer="Capital One",
            product_name="Venture X",
            currency="Capital One Miles",
            earn_multipliers={"gas": 1, "everyday": 2},
        )
        self._held(db, "User A", venture)
        db.add(models.Valuation(currency="Capital One Miles", cpp_scraped=1.7))
        db.commit()

        guide = categories.build_category_guide(db)
        gas = next(row for row in guide["categories"] if row["category"] == "gas")

        self.assertEqual(gas["winner"]["display_name"], "Venture X")
        self.assertEqual(gas["winner"]["multiplier"], 2)
        self.assertTrue(gas["winner"]["is_fallback"])
        self.assertIsNone(gas["runner_up"])

    def test_other_travel_does_not_count_as_everyday_spend(self):
        db = self._session()
        venture = self._product(
            db,
            issuer="Capital One",
            product_name="Venture X",
            currency="Capital One Miles",
            earn_multipliers={"everyday": 2},
            best_category_uses={"everyday": "2x"},
        )
        sapphire = self._product(
            db,
            issuer="Chase",
            product_name="Sapphire Preferred",
            currency="Chase Ultimate Rewards",
            earn_multipliers={"other_travel": 2, "everyday": 1},
            best_category_uses={"travel": "2x other travel", "everyday": "1x"},
        )
        self._held(db, "User A", venture)
        self._held(db, "User A", sapphire)
        db.add_all(
            [
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.3),
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.5),
            ]
        )
        db.commit()

        guide = categories.build_category_guide(db)
        everyday = next(row for row in guide["categories"] if row["category"] == "everyday")

        self.assertEqual(everyday["winner"]["display_name"], "Venture X")
        self.assertEqual(everyday["winner"]["note"], "2x")

    def _product(self, db, *, issuer, product_name, currency, earn_multipliers, best_category_uses=None):
        product = models.CardProduct(
            issuer=issuer,
            product_name=product_name,
            currency=currency,
            earn_multipliers=earn_multipliers,
            best_category_uses=best_category_uses,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        return product

    def _held(self, db, user, product):
        card = models.HeldCard(
            user=user,
            issuer=product.issuer,
            product_name=product.product_name,
            product_id=product.id,
            date_opened=dt.date.today(),
            status="Active",
        )
        db.add(card)
        db.commit()
        db.refresh(card)
        return card

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
