import datetime as dt
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.logic import catalog, scoring

ELIGIBLE = {"eligible": True, "block_type": "none"}
CPP_2 = {"chase ultimate rewards": 2.0}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _product(**overrides) -> models.CardProduct:
    data = {
        "issuer": "Chase",
        "product_name": "Chase Sapphire Preferred Card",
        "currency": "Chase Ultimate Rewards",
        "annual_fee": 0,
    }
    data.update(overrides)
    return models.CardProduct(**data)


class ScoringValueTests(unittest.TestCase):
    """Value math for S1 (WAIT floor), S2 (cash-only), S3 (bundle valuation)."""

    # --- S1: high-value offers below WAIT_THRESHOLD are WAIT, not dropped ---

    def test_high_value_below_wait_threshold_is_wait(self):
        product = _product(
            current_offer_points=90000, peak_offer_points=200000, annual_fee=95
        )

        score = scoring.compute_score(product, CPP_2, ELIGIBLE)

        self.assertEqual(score.peak_score, 45)
        self.assertEqual(score.status, scoring.WAIT)
        self.assertAlmostEqual(score.offer_value, 1705.0)
        self.assertFalse(score.cash_only)

    def test_low_value_below_wait_threshold_stays_low_priority(self):
        product = _product(current_offer_points=10000, peak_offer_points=100000)

        score = scoring.compute_score(product, CPP_2, ELIGIBLE)

        self.assertEqual(score.peak_score, 10)
        self.assertLess(score.offer_value, config.MIN_WATCH_VALUE)
        self.assertEqual(score.status, scoring.LOW_PRIORITY)

    # --- S2: cash-only offers — honest valuation + points-first filter ---

    def test_cash_only_scores_against_points_peak_in_dollars(self):
        product = _product(current_offer_cash=750, peak_offer_points=90000)

        with patch.object(config, "POINTS_FIRST", True):
            score = scoring.compute_score(product, CPP_2, ELIGIBLE)

        # $750 vs 90,000 pts * 2.0cpp = $1,800 peak -> 42% of peak, not 0.
        self.assertEqual(score.peak_score, 42)
        self.assertAlmostEqual(score.offer_value, 750.0)
        self.assertTrue(score.cash_only)
        self.assertFalse(score.needs_data)
        # Points-first: honest data kept, but demoted out of the queue.
        self.assertEqual(score.status, scoring.LOW_PRIORITY)

    def test_cash_only_honest_status_when_points_first_disabled(self):
        product = _product(current_offer_cash=750, peak_offer_points=90000)

        with patch.object(config, "POINTS_FIRST", False):
            score = scoring.compute_score(product, CPP_2, ELIGIBLE)

        # peak_score 42 < WAIT_THRESHOLD, value clears MIN_WATCH_VALUE -> WAIT.
        self.assertEqual(score.status, scoring.WAIT)
        self.assertTrue(score.cash_only)
        self.assertAlmostEqual(score.offer_value, 750.0)

    def test_cash_only_unknown_peak_follows_unknown_peak_branch(self):
        product = _product(currency=None, current_offer_cash=750)

        with patch.object(config, "POINTS_FIRST", False):
            score = scoring.compute_score(product, {}, ELIGIBLE)

        self.assertEqual(score.peak_score, 0)
        self.assertFalse(score.needs_data)
        self.assertEqual(score.status, scoring.WATCH)
        self.assertTrue(score.cash_only)

    def test_cash_only_unvaluable_points_peak_treated_as_unknown_not_zero(self):
        # Points peak exists but no cpp for the currency: no usable dollar
        # comparison, so route through the unknown-peak branch, not peak_score 0.
        product = _product(
            currency="Mystery Points", current_offer_cash=750, peak_offer_points=90000
        )

        with patch.object(config, "POINTS_FIRST", False):
            score = scoring.compute_score(product, {}, ELIGIBLE)

        self.assertEqual(score.peak_score, 0)
        self.assertEqual(score.status, scoring.WATCH)
        self.assertTrue(score.cash_only)

    # --- S3: public cash and targeted points are alternatives, not a stack ---

    def test_targeted_points_not_stacked_with_public_cash(self):
        product = _product(current_offer_cash=200, peak_offer_points=60000)

        score = scoring.compute_score(
            product, CPP_2, ELIGIBLE, my_targeted_offer_points=60000
        )

        # Best bundle is the targeted 60k (= $1,200), not $1,200 + $200.
        self.assertAlmostEqual(score.offer_value, 1200.0)
        self.assertTrue(score.targeted_beats_public)
        self.assertEqual(score.effective_points, 60000)
        self.assertFalse(score.cash_only)

    def test_public_points_plus_cash_bundle_sums(self):
        product = _product(
            current_offer_points=60000, current_offer_cash=200, peak_offer_points=60000
        )

        score = scoring.compute_score(product, CPP_2, ELIGIBLE)

        # One public bundle: points and cash are components, so they sum.
        self.assertAlmostEqual(score.offer_value, 1400.0)
        self.assertFalse(score.targeted_beats_public)
        self.assertFalse(score.cash_only)
        self.assertEqual(score.peak_score, 100)

    def test_public_cash_bundle_can_beat_smaller_targeted_points(self):
        product = _product(current_offer_cash=500, peak_offer_points=60000)

        score = scoring.compute_score(
            product, CPP_2, ELIGIBLE, my_targeted_offer_points=10000
        )

        # $500 public cash beats 10k targeted (= $200); winner drives the value.
        self.assertAlmostEqual(score.offer_value, 500.0)
        self.assertFalse(score.targeted_beats_public)
        self.assertEqual(score.effective_points, 0)
        # Targeted points exist, so the card is not cash-only.
        self.assertFalse(score.cash_only)


class ScoredCatalogCashOnlyTests(unittest.TestCase):
    """cash_only flows through the catalog entry dict for the UI/pipeline."""

    def test_entries_carry_cash_only_flag(self):
        db = self._session()
        points_card = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            currency="Chase Ultimate Rewards",
            annual_fee=95,
            current_offer_points=80000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            peak_offer_points=100000,
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=_now(),
        )
        cash_card = models.CardProduct(
            issuer="Test Bank",
            product_name="Cashline Card",
            currency="Test Cash Points",
            annual_fee=0,
            current_offer_cash=750,
            peak_offer_points=90000,
            source_url="https://testbank.example/cashline-card",
            last_verified=_now(),
        )
        db.add_all(
            [
                points_card,
                cash_card,
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=2.0),
                models.Valuation(currency="Test Cash Points", cpp_scraped=2.0),
            ]
        )
        db.commit()

        with patch.object(config, "POINTS_FIRST", True):
            entries = catalog.scored_catalog(db, "User A")
        by_name = {e["product_name"]: e for e in entries}

        points_entry = by_name["Chase Sapphire Preferred Card"]
        self.assertFalse(points_entry["cash_only"])
        self.assertEqual(points_entry["status"], scoring.APPLY_NOW)

        cash_entry = by_name["Cashline Card"]
        self.assertTrue(cash_entry["cash_only"])
        self.assertEqual(cash_entry["status"], scoring.LOW_PRIORITY)
        # Honest data stays intact for the low-priority surface.
        self.assertEqual(cash_entry["peak_score"], 42)
        self.assertAlmostEqual(cash_entry["offer_value"], 750.0)

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
