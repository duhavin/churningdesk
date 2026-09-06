import datetime as dt
import json
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.ingestion import extract, validate
from backend.logic import catalog, pipeline, scoring
from backend.tests.offer_fixtures import add_current_offer_evidence


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _complete_product(**overrides) -> models.CardProduct:
    data = {
        "issuer": "Chase",
        "product_name": "Chase Sapphire Preferred Card",
        "currency": "Chase Ultimate Rewards",
        "annual_fee": 95,
        "current_offer_points": 80000,
        "current_offer_min_spend": 5000,
        "current_offer_window_months": 3,
        "peak_offer_points": 100000,
        "peak_offer_min_spend": 5000,
        "source_url": "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
        "last_verified": _now(),
        "earn_multipliers": {"dining": 3},
        "best_category_uses": {"dining": "3x"},
        "card_benefits": ["$50 annual Chase Travel hotel credit"],
    }
    data.update(overrides)
    return models.CardProduct(**data)


class DecisionDataQualityTests(unittest.TestCase):
    def test_unverified_complete_offer_is_needs_data_not_apply_now(self):
        db = self._session()
        product = _complete_product(source_url=None, last_verified=None)
        db.add_all([product, models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0)])
        db.commit()

        entries = catalog.scored_catalog(db, "User A")
        entry = entries[0]

        self.assertEqual(entry["status"], scoring.NEEDS_DATA)
        self.assertFalse(entry["decision_ready"])
        self.assertIn("missing_source", entry["data_quality_issues"])
        self.assertIn("never_verified", entry["data_quality_issues"])

    def test_stale_verified_offer_is_excluded_from_pipeline(self):
        db = self._session()
        product = _complete_product(last_verified=_now() - dt.timedelta(days=45))
        db.add_all([product, models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0)])
        db.commit()

        result = pipeline.build_pipeline(db, "User A")

        self.assertEqual(result["next_cards"], [])
        self.assertEqual(len(result["needs_data"]), 1)
        self.assertIn("stale_verified_data", result["needs_data"][0]["data_quality_issues"])

    def test_pending_offer_change_blocks_apply_until_approved(self):
        db = self._session()
        product = _complete_product()
        db.add_all([product, models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0)])
        db.commit()
        add_current_offer_evidence(db, [product])
        db.commit()
        db.refresh(product)
        change = models.ProposedChange(
            target_table="card_product",
            target_id=product.id,
            field="current_offer_points",
            old_value="80000",
            new_value="100000",
            source_url=product.source_url,
            confidence=0.92,
            status="pending",
        )
        db.add(change)
        db.commit()

        # Approval still requires exact corroboration of the proposed value;
        # the original row certifies only the prior 80,000-point offer.
        db.add(
            models.IngestionEvidence(
                product_id=product.id,
                field="current_offer_points",
                value_json=json.dumps(100000),
                source_url=product.source_url,
                fetched_at=_now().isoformat(),
                content_hash=f"fixture-{product.id}-current_offer_points-new",
                confidence=0.95,
                evidence_snippets={"current_offer_points": ["Chase Sapphire Preferred Card 100,000 points fixture."]},
                offer_status="public",
            )
        )
        db.commit()

        before = catalog.scored_catalog(db, "User A")[0]
        self.assertEqual(before["status"], scoring.NEEDS_DATA)
        self.assertIn("pending_verified_update", before["data_quality_issues"])

        validate.approve_change(db, change)
        add_current_offer_evidence(db, [product])
        db.commit()
        after = catalog.scored_catalog(db, "User A")[0]

        self.assertNotIn("pending_verified_update", after["data_quality_issues"])
        self.assertTrue(after["decision_ready"])
        self.assertEqual(after["status"], scoring.APPLY_NOW)

    def test_missing_public_peak_with_targeted_peak_is_watch_not_apply_now(self):
        db = self._session()
        product = _complete_product(
            peak_offer_points=None,
            targeted_peak_offer_points=150000,
            current_offer_points=90000,
        )
        db.add_all([product, models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0)])
        db.commit()
        add_current_offer_evidence(db, [product])
        db.commit()

        entry = catalog.scored_catalog(db, "User A")[0]

        self.assertEqual(entry["peak_score"], 60)
        self.assertEqual(entry["status"], scoring.WATCH)
        self.assertTrue(entry["decision_ready"])
        self.assertFalse(entry["needs_data"])
        self.assertIn("missing_public_peak", entry["data_quality_issues"])

    def test_current_offer_does_not_fabricate_first_sight_public_peak(self):
        db = self._session()
        product = _complete_product(peak_offer_points=None, peak_offer_min_spend=None)
        db.add_all([product, models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0)])
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.92,
            issuer="Chase",
            card_name="Chase Sapphire Preferred Card",
            currency="Chase Ultimate Rewards",
            current_offer_points=90000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            offer_status="public",
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            evidence_snippets={
                "current_offer_points": ["Earn 90,000 bonus points."],
                "current_offer_min_spend": ["after you spend $5,000 in the first 3 months"],
                "current_offer_window_months": ["after you spend $5,000 in the first 3 months"],
            },
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)
        # The extraction is intentionally about peak completeness; the
        # persisted current values remain certified by a matching public row.
        add_current_offer_evidence(db, [product])
        db.commit()
        entry = catalog.scored_catalog(db, "User A")[0]

        self.assertIn("current_offer_points", result["committed"])
        self.assertIsNone(product.peak_offer_points)
        self.assertIn("missing_public_peak", entry["data_quality_issues"])
        self.assertEqual(entry["status"], scoring.WATCH)
        self.assertTrue(entry["decision_ready"])

    def test_official_large_delta_without_field_evidence_queues_review(self):
        db = self._session()
        product = _complete_product(
            product_name="Chase Sapphire Reserve for Business Credit Card",
            annual_fee=795,
            current_offer_points=100000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            peak_offer_points=100000,
            source_url="https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            issuer="Chase",
            card_name="Chase Sapphire Reserve for Business Credit Card",
            current_offer_points=200000,
            current_offer_min_spend=30000,
            current_offer_window_months=6,
            offer_status="public",
            source_url="https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
            evidence_snippets={},
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertIn("current_offer_points", result["proposed"])
        self.assertEqual(product.current_offer_points, 100000)
        self.assertEqual(product.peak_offer_points, 100000)

    def test_raw_benefit_offer_fragments_are_rejected(self):
        product = _complete_product()
        review = validate.classify_proposed_change(
            product,
            "card_benefits",
            old_value=[],
            new_value=[
                {
                    "name": "[text] Sapphire ReserveThe Sapphire Reserve for BusinessSM card Opens offer details overlay.",
                    "description": "OUR BEST OFFER RETURNS Earn 150,000 strikethrough 200,000 points after you spend $30,000.",
                    "frequency": "unknown",
                },
                {
                    "name": "Ink Business Preferred Credit Card New Cardmember Offer Complimentary access to DashPass.",
                    "description": "IF YOU ALREADY HAVE ANY CHASE FOR BUSINESS CREDIT CARD!",
                    "frequency": "unknown",
                },
            ],
            confidence=0.95,
        )

        self.assertEqual(review.action, "reject")
        self.assertIn(review.reason_code, {"raw_benefit_fragments", "low_benefit_quality"})

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
