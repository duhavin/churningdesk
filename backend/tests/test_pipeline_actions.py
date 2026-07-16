import datetime as dt
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.logic import eligibility, pipeline, scoring


class HeldActionRequeueTests(unittest.TestCase):
    """Renewal decisions must not swallow a pending bonus re-eligibility."""

    def setUp(self):
        self._old_users = config.USERS
        config.USERS = ["User A", "User B"]

    def tearDown(self):
        config.USERS = self._old_users

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def _re_eligible_citi(self, db, renewal_date=None):
        today = dt.date.today()
        card = models.HeldCard(
            user="User A",
            issuer="Citi",
            product_name="Citi Strata Premier Card",
            date_opened=today - dt.timedelta(days=1100),
            welcome_bonus_earned=True,
            bonus_earned_date=eligibility.add_months(today, -25),
            renewal_date=renewal_date,
            status="Active",
        )
        db.add(card)
        db.commit()
        return card

    def test_requeue_without_renewal_window(self):
        db = self._session()
        card = self._re_eligible_citi(db, renewal_date=None)
        action = pipeline._held_actions(db, [card])[0]
        self.assertEqual(action["action"], "requeue")
        self.assertTrue(action["bonus_eligible_again"])
        self.assertIsNotNone(action["eligible_again_date"])

    def test_renewal_window_keeps_re_eligibility_in_reason(self):
        db = self._session()
        today = dt.date.today()
        card = self._re_eligible_citi(db, renewal_date=today + dt.timedelta(days=30))
        action = pipeline._held_actions(db, [card])[0]

        # Renewal action wins, but the re-eligibility stays visible.
        self.assertIn(
            action["action"], {"renew_review", "downgrade_review", "retention_review"}
        )
        self.assertIn("bonus-re-eligible", action["reason"])
        self.assertIn("Renewal", action["reason"])
        # Payload fields survive the renewal branch.
        self.assertTrue(action["bonus_eligible_again"])
        self.assertEqual(
            action["eligible_again_date"],
            eligibility.add_months(eligibility.add_months(today, -25), 24).isoformat(),
        )


class BenefitUsageSignalTests(unittest.TestCase):
    """Benefit-usage renewal adjustment: visible driver + most-recent period."""

    def _card(self):
        return models.HeldCard(
            user="User A",
            issuer="American Express",
            product_name="American Express Gold Card",
            date_opened=dt.date(2024, 1, 1),
            status="Active",
        )

    @staticmethod
    def _usage(benefit_key, period_key, period_start, available, used):
        return SimpleNamespace(
            benefit_key=benefit_key,
            benefit_name=benefit_key,
            period_key=period_key,
            period_start=period_start,
            amount_available=available,
            amount_used=used,
        )

    def test_well_used_driver_and_score_boost(self):
        card = self._card()
        base_score, base_drivers = pipeline._held_value_signal(card, None)
        usages = [self._usage("dining_credit", "2026-07", dt.date(2026, 7, 1), 10.0, 10.0)]
        score, drivers = pipeline._held_value_signal(card, None, benefit_usages=usages)

        self.assertFalse(any("benefit credits" in d for d in base_drivers))
        self.assertTrue(any(d.startswith("benefit credits well-used") for d in drivers))
        self.assertEqual(score, base_score + 10)

    def test_unused_driver_and_score_penalty(self):
        card = self._card()
        base_score, _ = pipeline._held_value_signal(card, None)
        usages = [self._usage("dining_credit", "2026-07", dt.date(2026, 7, 1), 10.0, 0.0)]
        score, drivers = pipeline._held_value_signal(card, None, benefit_usages=usages)

        self.assertIn("benefit credits unused (1 trackable)", drivers)
        self.assertEqual(score, base_score - 8)

    def test_only_most_recent_period_per_benefit_counts(self):
        card = self._card()
        base_score, _ = pipeline._held_value_signal(card, None)
        usages = [
            # Old year fully unused; current year fully used. Only the most
            # recent period should count, so the card reads as well-used.
            self._usage("dining_credit", "2024", dt.date(2024, 1, 1), 120.0, 0.0),
            self._usage("dining_credit", "2026", dt.date(2026, 1, 1), 120.0, 120.0),
        ]
        score, drivers = pipeline._held_value_signal(card, None, benefit_usages=usages)

        self.assertTrue(any(d.startswith("benefit credits well-used") for d in drivers))
        self.assertFalse(any("unused" in d for d in drivers))
        self.assertEqual(score, base_score + 10)


class NeedsReviewReasonTests(unittest.TestCase):
    """LOW PRIORITY exclusions must state their honest binding reason."""

    def _entry(self, **overrides):
        entry = {
            "status": scoring.LOW_PRIORITY,
            "offer_value": 100.0,
            "peak_score": 50,
            "data_quality_issues": [],
        }
        entry.update(overrides)
        return entry

    def test_cash_only_reason(self):
        reason = pipeline._needs_review_reason(self._entry(cash_only=True, offer_value=250.0))
        self.assertIn("Cash-only offer", reason)
        self.assertIn("$250", reason)
        self.assertIn("points-first", reason)

    def test_high_value_below_peak_reason(self):
        reason = pipeline._needs_review_reason(
            self._entry(offer_value=config.MIN_WATCH_VALUE + 50)
        )
        self.assertIn("well below its known peak", reason)

    def test_value_floor_reason(self):
        reason = pipeline._needs_review_reason(self._entry(offer_value=50.0))
        self.assertIn("below the current value floor", reason)

    def test_missing_cash_only_key_is_safe(self):
        # Entries produced before the scoring change have no cash_only field.
        reason = pipeline._needs_review_reason(self._entry())
        self.assertIn("below the current value floor", reason)


if __name__ == "__main__":
    unittest.main()
