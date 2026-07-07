"""Transfer-bonus math, multi-option award search, bonus-research parsing
(2026-07-06). All PUBLIC-data logic; fixtures are fictional."""
from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.db import Base
from backend.logic.redemption.bonus_research import _parse_suggestions
from backend.logic.redemption.engine import (
    _bonus_state,
    _program_availability,
    _progress_for_target,
)
from backend.logic.redemption.providers import BenchmarkProvider


def _partner(**kw) -> models.TransferPartner:
    defaults = dict(from_currency="Membership Rewards", to_program="Avios", ratio="1:1")
    defaults.update(kw)
    return models.TransferPartner(**defaults)


class BonusStateTests(unittest.TestCase):
    def test_active_bonus_multiplies_ratio(self):
        p = _partner(bonus_pct=30.0, bonus_end_date=dt.date.today() + dt.timedelta(days=10))
        ratio, active = _bonus_state(p)
        self.assertTrue(active)
        self.assertAlmostEqual(ratio, 1.3)

    def test_expired_bonus_ignored(self):
        p = _partner(bonus_pct=30.0, bonus_end_date=dt.date.today() - dt.timedelta(days=1))
        ratio, active = _bonus_state(p)
        self.assertFalse(active)
        self.assertAlmostEqual(ratio, 1.0)

    def test_no_end_date_counts_as_active(self):
        p = _partner(bonus_pct=25.0, bonus_end_date=None)
        ratio, active = _bonus_state(p)
        self.assertTrue(active)
        self.assertAlmostEqual(ratio, 1.25)

    def test_bonus_on_non_unit_ratio(self):
        p = _partner(ratio="1:1.5", bonus_pct=20.0,
                     bonus_end_date=dt.date.today() + dt.timedelta(days=5))
        ratio, active = _bonus_state(p)
        self.assertTrue(active)
        self.assertAlmostEqual(ratio, 1.8)


class ProgramAvailabilityTests(unittest.TestCase):
    def test_bonused_transfer_converts_more_and_sorts_first(self):
        partners = [
            _partner(from_currency="Ultimate Rewards", to_program="Avios", ratio="1:1"),
            _partner(from_currency="Membership Rewards", to_program="Avios", ratio="1:1",
                     bonus_pct=30.0, bonus_end_date=dt.date.today() + dt.timedelta(days=14)),
        ]
        balances = {"Membership Rewards": 100000.0, "Ultimate Rewards": 100000.0}
        out = _program_availability("Avios", balances, partners)
        self.assertEqual(out["available"], 230000.0)
        first = out["transfers"][0]
        self.assertTrue(first["bonus_active"])
        self.assertEqual(first["converted_points"], 130000)
        self.assertEqual(first["bonus_pct"], 30.0)


class AwardOptionsTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_multiple_benchmark_options_surface_with_coverage(self):
        db = self._session()
        db.add_all([
            models.AwardBenchmark(route="LAX-Tokyo", cabin_or_tier="Business",
                                  program="Avios", est_cost_points=75000),
            models.AwardBenchmark(route="LAX-Tokyo", cabin_or_tier="Business",
                                  program="Virgin Points", est_cost_points=90000),
            models.AwardBenchmark(route="LAX-Tokyo", cabin_or_tier="Economy",
                                  program="Avios", est_cost_points=39000),
        ])
        db.add(_partner(from_currency="Membership Rewards", to_program="Avios",
                        ratio="1:1", bonus_pct=30.0,
                        bonus_end_date=dt.date.today() + dt.timedelta(days=14)))
        db.add(models.TargetRedemption(
            user="Household", name="Tokyo trip", origin="LAX", destination="Tokyo",
            preferred_programs="Avios",
        ))
        db.commit()
        target = db.query(models.TargetRedemption).first()

        row = _progress_for_target(
            db, target, {"Membership Rewards": 60000.0}, list(db.query(models.TransferPartner)),
            BenchmarkProvider(),
        )
        options = row["award_options"]
        self.assertGreaterEqual(len(options), 3)
        # Sorted by points cost; the 39k economy option leads.
        self.assertEqual(options[0]["points_cost"], 39000)
        # 60k MR × 1.3 bonus = 78k Avios: covers 39k and 75k, not 90k Virgin.
        by_cost = {o["points_cost"]: o for o in options}
        self.assertTrue(by_cost[39000]["covered_by_household"])
        self.assertTrue(by_cost[75000]["covered_by_household"])
        self.assertFalse(by_cost[90000]["covered_by_household"])
        # The transfer option itself carries the bonus.
        transfers = row["transfer_options"]
        self.assertTrue(transfers and transfers[0]["bonus_active"])


class BonusResearchParseTests(unittest.TestCase):
    def test_valid_rows_pass_and_junk_drops(self):
        future = (dt.date.today() + dt.timedelta(days=20)).isoformat()
        past = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        text = f"""Here are the current bonuses:
[
  {{"from_currency": "Membership Rewards", "to_program": "Avios",
    "bonus_pct": 30, "end_date": "{future}", "source_url": "https://example.com/a"}},
  {{"from_currency": "Ultimate Rewards", "to_program": "Virgin Points",
    "bonus_pct": 900, "end_date": "{future}", "source_url": "https://example.com/b"}},
  {{"from_currency": "ThankYou Points", "to_program": "Choice",
    "bonus_pct": 25, "end_date": "{past}", "source_url": "https://example.com/c"}},
  {{"from_currency": "Bilt", "to_program": "Alaska",
    "bonus_pct": 50, "end_date": null, "source_url": "not-a-url"}}
]"""
        out = _parse_suggestions(text)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["from_currency"], "Membership Rewards")
        self.assertEqual(out[0]["bonus_pct"], 30.0)

    def test_no_array_returns_empty(self):
        self.assertEqual(_parse_suggestions("no deals today"), [])


class MinSpendRoutingTests(unittest.TestCase):
    """Min-spend windows override category routing (2026-07-06)."""

    def _card(self, **kw):
        import datetime as dt
        from backend import config
        defaults = dict(
            user=config.USERS[0], issuer="Chase", product_name="Sapphire Preferred Card",
            date_opened=dt.date.today() - dt.timedelta(days=30), status="Active",
            ownership="Personal",
        )
        defaults.update(kw)
        return models.HeldCard(**defaults)

    def test_window_math_and_urgency(self):
        from backend.logic.categories import _min_spend_windows
        today = dt.date.today()
        cards = [
            self._card(min_spend_requirement=4000, min_spend_progress=1000,
                       min_spend_deadline=today + dt.timedelta(days=10)),
            self._card(user=__import__("backend.config", fromlist=["USERS"]).USERS[-1], product_name="Venture X Rewards Credit Card",
                       issuer="Capital One",
                       min_spend_requirement=4000, min_spend_progress=3800,
                       min_spend_deadline=today + dt.timedelta(days=60)),
            self._card(product_name="Freedom Flex", min_spend_requirement=500,
                       min_spend_progress=500, min_spend_deadline=today + dt.timedelta(days=5)),
            self._card(product_name="Gold Card", issuer="American Express",
                       min_spend_requirement=6000, min_spend_progress=0,
                       welcome_bonus_earned=True),
        ]
        windows = _min_spend_windows(cards, today=today)
        # Completed ($500/$500) and bonus-earned cards never appear.
        self.assertEqual(len(windows), 2)
        # $3,000 in 10 days = $300/day → critical; it sorts first.
        first = windows[0]
        self.assertEqual(first["remaining"], 3000)
        self.assertEqual(first["days_left"], 10)
        self.assertEqual(first["daily_needed"], 300.0)
        self.assertEqual(first["urgency"], "critical")
        self.assertEqual(windows[1]["urgency"], "on_track")

    def test_guide_carries_override(self):
        from backend.logic.categories import build_category_guide
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        product = models.CardProduct(
            issuer="Chase", product_name="Sapphire Preferred Card",
            earn_multipliers={"dining": 3.0}, currency="Ultimate Rewards",
        )
        db.add(product)
        db.commit()
        db.add(self._card(product_id=product.id, min_spend_requirement=4000,
                          min_spend_progress=500,
                          min_spend_deadline=dt.date.today() + dt.timedelta(days=20)))
        db.commit()
        guide = build_category_guide(db)
        self.assertEqual(len(guide["min_spend_windows"]), 1)
        self.assertTrue(all("min_spend_override" in row for row in guide["categories"]))
        note = guide["categories"][0]["min_spend_override"]["note"]
        self.assertIn("Route ALL spend", note)


class ValueVerdictTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_verdict_grades_against_baseline(self):
        db = self._session()
        db.add(models.AwardBenchmark(route="LAX-Tokyo", cabin_or_tier="Business",
                                     program="Avios", est_cost_points=100000))
        db.add(_partner(from_currency="Membership Rewards", to_program="Avios", ratio="1:1"))
        db.add(models.TargetRedemption(
            user="Household", name="Tokyo", origin="LAX", destination="Tokyo",
            preferred_programs="Avios", target_value_cash=2500.0,
        ))
        db.commit()
        target = db.query(models.TargetRedemption).first()
        partners = list(db.query(models.TransferPartner))
        balances = {"Membership Rewards": 120000.0}

        # 2500/100000*100 = 2.5 cpp vs 1.7 baseline → excellent.
        row = _progress_for_target(db, target, balances, partners, BenchmarkProvider(),
                                   valuations={"membership rewards": 1.7})
        self.assertEqual(row["value_verdict"], "excellent")
        self.assertEqual(row["baseline_cpp"], 1.7)
        # Same trip against a 3.5 cpp baseline → poor (2.5/3.5 < 0.8).
        row = _progress_for_target(db, target, balances, partners, BenchmarkProvider(),
                                   valuations={"membership rewards": 3.5})
        self.assertEqual(row["value_verdict"], "poor")
        # No valuation → no verdict, never a crash.
        row = _progress_for_target(db, target, balances, partners, BenchmarkProvider())
        self.assertIsNone(row["value_verdict"])



if __name__ == "__main__":
    unittest.main()
