import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.logic import catalog_health


class CatalogHealthTests(unittest.TestCase):
    def test_catalog_health_flags_held_missing_benefits_and_source(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred Card",
            currency="Chase Ultimate Rewards",
            current_offer_points=75000,
            peak_offer_points=100000,
            source_url="https://example.test/sapphire",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                product,
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.8),
            ]
        )
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer=product.issuer,
                product_name=product.product_name,
                product_id=product.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        health = catalog_health.build_catalog_health(db)
        row = health["products"][0]
        issue_codes = {issue["code"] for issue in row["issues"]}

        self.assertEqual(row["display_name"], "Sapphire Preferred")
        self.assertEqual(row["held_by"], ["User A"])
        self.assertIn("missing_benefits", issue_codes)
        self.assertIn("missing_multipliers", issue_codes)
        self.assertEqual(row["status"], "needs_data")
        self.assertEqual(health["summary"]["held_needs_data"], 1)

    def test_catalog_health_flags_pending_review_before_stale(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X",
            currency="Capital One Miles",
            current_offer_points=75000,
            peak_offer_points=90000,
            card_benefits=["$300 annual travel credit"],
            earn_multipliers={"everyday": 2},
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=45),
        )
        db.add_all(
            [
                product,
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.7),
            ]
        )
        db.commit()
        db.refresh(product)
        db.add(
            models.ProposedChange(
                target_table="card_product",
                target_id=product.id,
                field="current_offer_points",
                old_value="75000",
                new_value="90000",
                source_url="https://example.test/venture-x",
                confidence=0.9,
                status="pending",
            )
        )
        db.commit()

        health = catalog_health.build_catalog_health(db)
        row = next(item for item in health["products"] if item["product_id"] == product.id)
        issue_codes = {issue["code"] for issue in row["issues"]}

        self.assertEqual(row["status"], "needs_review")
        self.assertIn("pending_review", issue_codes)
        self.assertIn("source_stale", issue_codes)

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
