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

    def test_min_spend_only_is_not_current_offer(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            currency="Chase Ultimate Rewards",
            current_offer_min_spend=5000,
            peak_offer_points=100000,
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                product,
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.8),
            ]
        )
        db.commit()

        health = catalog_health.build_catalog_health(db)
        row = health["products"][0]
        issue_codes = {issue["code"] for issue in row["issues"]}

        self.assertIn("missing_current_offer", issue_codes)
        self.assertEqual(row["status"], "needs_data")

    def test_peak_min_spend_only_is_not_public_peak(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            currency="Chase Ultimate Rewards",
            current_offer_points=80000,
            current_offer_min_spend=5000,
            peak_offer_min_spend=5000,
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                product,
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.8),
            ]
        )
        db.commit()

        health = catalog_health.build_catalog_health(db)
        row = health["products"][0]
        issue_codes = {issue["code"] for issue in row["issues"]}

        self.assertIn("missing_public_peak", issue_codes)
        self.assertEqual(row["status"], "needs_data")

    def test_unsafe_source_quality_issue_is_not_healthy(self):
        cases = [
            (
                "https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
                "broad_source_not_product_truth",
            ),
            (
                "https://www.example.com/cards/sapphire",
                "source_not_product_specific",
            ),
            (
                "https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
                "source_identity_conflict",
            ),
        ]
        for source_url, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                db = self._session()
                product = models.CardProduct(
                    issuer="Chase",
                    product_name="Chase Sapphire Preferred Card",
                    currency="Chase Ultimate Rewards",
                    current_offer_points=80000,
                    peak_offer_points=100000,
                    card_benefits=["$50 annual hotel credit"],
                    earn_multipliers={"dining": 3},
                    source_url=source_url,
                    last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
                )
                db.add_all(
                    [
                        product,
                        models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.8),
                    ]
                )
                db.commit()

                health = catalog_health.build_catalog_health(db)
                row = health["products"][0]
                issue_codes = {issue["code"] for issue in row["issues"]}

                self.assertIn(expected_issue, issue_codes)
                self.assertEqual(row["status"], "needs_data")

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
