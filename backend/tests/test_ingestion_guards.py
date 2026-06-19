import datetime as dt
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.ingestion import extract, schedule, validate
from backend.logic import eligibility, pipeline, scoring


class IngestionGuardTests(unittest.TestCase):
    def test_researched_rows_match_by_identity_not_position(self):
        sapphire = models.CardProduct(id=1, issuer="Chase", product_name="Sapphire Preferred Card")
        venture = models.CardProduct(id=2, issuer="Capital One", product_name="Venture X Rewards Credit Card")
        venture_row = extract.OfferScanRow(
            issuer="Capital One",
            card_name="Capital One Venture X Rewards",
            offer_status="public",
            confidence=0.9,
        )
        sapphire_row = extract.OfferScanRow(
            issuer="Chase",
            card_name="Chase Sapphire Preferred",
            offer_status="public",
            confidence=0.9,
        )

        matched = schedule._match_extracted_rows([sapphire, venture], [venture_row, sapphire_row])

        self.assertIs(matched[sapphire.id], sapphire_row)
        self.assertIs(matched[venture.id], venture_row)

    def test_researched_row_requires_matching_identity(self):
        sapphire = models.CardProduct(id=1, issuer="Chase", product_name="Sapphire Preferred Card")
        wrong_row = extract.OfferScanRow(
            issuer="American Express",
            card_name="Gold Card",
            offer_status="public",
            confidence=0.9,
        )

        self.assertFalse(schedule._row_matches_product(wrong_row, sapphire))
        self.assertEqual(schedule._match_extracted_rows([sapphire], [wrong_row]), {})

    def test_low_confidence_first_sight_queues_review(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.55,
            current_offer_points=75000,
            currency="points",
            offer_status="public",
            source_url="https://example.test/card",
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertIn("current_offer_points", result["proposed"])
        self.assertIsNone(product.current_offer_points)
        proposed = db.scalars(select(models.ProposedChange)).all()
        self.assertTrue(any(change.field == "current_offer_points" for change in proposed))

    def test_targeted_cash_offer_does_not_commit_public_spend_terms(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Cash Rewards Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.82,
            current_offer_cash=500,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            is_targeted=True,
            offer_status="targeted",
            source_url="https://example.test/targeted",
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertIn("targeted_peak_offer_cash", result["committed"])
        self.assertEqual(product.targeted_peak_offer_cash, 500)
        self.assertIsNone(product.current_offer_cash)
        self.assertIsNone(product.current_offer_min_spend)
        self.assertIsNone(product.current_offer_window_months)

    def test_pipeline_suppresses_active_same_family_card(self):
        db = self._session()
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Venture Rewards Credit Card",
            currency="Capital One Miles",
            current_offer_points=75000,
            peak_offer_points=75000,
        )
        venture_x = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            currency="Capital One Miles",
            current_offer_points=75000,
            peak_offer_points=90000,
        )
        db.add_all(
            [
                venture,
                venture_x,
                models.Valuation(currency="Capital One Miles", cpp_override=1.0),
            ]
        )
        db.commit()
        db.refresh(venture_x)
        db.add(
            models.HeldCard(
                user="Davin",
                issuer="Capital One",
                product_name="Venture X Rewards Credit Card",
                product_id=venture_x.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        result = pipeline.build_pipeline(db, "Davin")

        self.assertNotIn("Venture Rewards Credit Card", {c["product_name"] for c in result["next_cards"]})

    def test_five24_counts_closed_recent_personal_cards(self):
        db = self._session()
        db.add(
            models.HeldCard(
                user="Davin",
                issuer="Test Bank",
                product_name="Closed Personal Card",
                date_opened=dt.date.today() - dt.timedelta(days=30),
                reports_to_personal_credit=True,
                status="Closed",
            )
        )
        db.commit()

        result = eligibility.five24(db, "Davin")

        self.assertEqual(result.count, 1)

    def test_points_offer_without_cpp_needs_data(self):
        product = models.CardProduct(
            issuer="Test Bank",
            product_name="Unknown Points Card",
            currency="Unknown Points",
            current_offer_points=80000,
            peak_offer_points=100000,
        )
        result = scoring.compute_score(
            product,
            valuations={},
            eligibility={"eligible": True, "block_type": "none"},
        )

        self.assertEqual(result.status, scoring.NEEDS_DATA)

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
