import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.logic import eligibility, household
from backend.tests.offer_fixtures import add_current_offer_evidence


class HouseholdOrderingTests(unittest.TestCase):
    def setUp(self):
        self._old_users = config.USERS
        self._old_max_apps = config.MAX_APPS_PER_QUARTER
        config.USERS = ["User A", "User B"]
        config.MAX_APPS_PER_QUARTER = 4

    def tearDown(self):
        config.USERS = self._old_users
        config.MAX_APPS_PER_QUARTER = self._old_max_apps

    def test_household_moves_preserve_pipeline_rotation_before_raw_value(self):
        db = self._session()
        chase = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred",
            currency="Chase Ultimate Rewards",
            annual_fee=95,
            current_offer_points=80_000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            peak_offer_points=100_000,
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        amex = models.CardProduct(
            issuer="American Express",
            product_name="Platinum Card",
            currency="Amex Membership Rewards",
            annual_fee=0,
            current_offer_points=100_000,
            current_offer_min_spend=8000,
            current_offer_window_months=6,
            peak_offer_points=125_000,
            source_url="https://www.americanexpress.com/us/credit-cards/card/platinum/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
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
        add_current_offer_evidence(db, [chase, amex])
        db.commit()

        result = household.build_household(db)
        moves = result["moves"]

        self.assertGreater(moves[2]["household_value"], moves[0]["household_value"])
        self.assertEqual(moves[0]["issuer"], "Chase")
        self.assertEqual(moves[0]["pipeline_rank"], 1)
        self.assertEqual(moves[1]["issuer"], "Chase")
        self.assertEqual(moves[2]["issuer"], "American Express")

    def test_capital_one_venture_family_rule_and_household_referral_route(self):
        db = self._session()
        today = dt.date.today()
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=95,
            current_offer_points=75000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=75000,
            source_url="https://www.capitalone.com/credit-cards/venture/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        venture_x = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=395,
            current_offer_points=75000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=90000,
            referral_bonus_points=25000,
            source_url="https://www.capitalone.com/credit-cards/venture-x/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                venture,
                venture_x,
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.0),
            ]
        )
        db.commit()
        add_current_offer_evidence(db, [venture, venture_x])
        db.commit()
        db.refresh(venture_x)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Capital One Venture X Rewards Credit Card",
                product_id=venture_x.id,
                date_opened=today - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                bonus_earned_date=today - dt.timedelta(days=365),
                status="Active",
            )
        )
        db.commit()

        same_user = eligibility.eligibility(
            db,
            "User A",
            "Capital One",
            "Capital One Venture Rewards Credit Card",
        )
        plan = household.build_household(db)
        venture_referral = next(
            row
            for row in plan["referrals"]
            if row["to_user"] == "User B" and row["product_name"] == "Capital One Venture Rewards Credit Card"
        )
        family_referrals = [
            row
            for row in plan["referrals"]
            if row["to_user"] == "User B"
            and row["referral_match"] == "family"
            and row.get("referral_family_key") == "capital_one:capital_one_venture"
        ]

        self.assertFalse(same_user.eligible)
        self.assertEqual(same_user.block_type, "temporary")
        self.assertTrue(any("48-month" in reason for reason in same_user.reasons))
        self.assertEqual(venture_referral["from_user"], "User A")
        self.assertEqual(venture_referral["referral_match"], "family")
        self.assertIn("Refer via User A", venture_referral["route"])
        self.assertEqual(
            [row["product_name"] for row in family_referrals],
            ["Capital One Venture Rewards Credit Card"],
        )

    def test_exact_referral_match_is_not_labeled_as_family_route(self):
        db = self._session()
        venture_x = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=395,
            current_offer_points=75000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=90000,
            referral_bonus_points=25000,
            source_url="https://www.capitalone.com/credit-cards/venture-x/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                venture_x,
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.0),
            ]
        )
        db.commit()
        add_current_offer_evidence(db, [venture_x])
        db.commit()
        db.refresh(venture_x)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Venture X",
                product_id=venture_x.id,
                date_opened=dt.date.today() - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                bonus_earned_date=dt.date.today() - dt.timedelta(days=365),
                status="Active",
            )
        )
        db.commit()

        plan = household.build_household(db)
        referral = next(
            row
            for row in plan["referrals"]
            if row["to_user"] == "User B"
            and row["product_name"] == "Capital One Venture X Rewards Credit Card"
        )

        self.assertEqual(referral["referral_match"], "exact")
        self.assertEqual(referral["route"], "Refer via User A")
        self.assertEqual(referral["referral_family_key"], "capital_one:capital_one_venture")

    def test_super_family_ink_referral_is_family_route_naming_held_card(self):
        db = self._session()
        ink_preferred = models.CardProduct(
            issuer="Chase",
            product_name="Ink Business Preferred Credit Card",
            currency="Chase Ultimate Rewards",
            ownership="Business",
            annual_fee=95,
            current_offer_points=90_000,
            current_offer_min_spend=8000,
            current_offer_window_months=3,
            peak_offer_points=90_000,
            source_url="https://creditcards.chase.com/business-credit-cards/ink/business-preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                ink_preferred,
                models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.0),
            ]
        )
        db.commit()
        add_current_offer_evidence(db, [ink_preferred])
        db.commit()
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Chase",
                product_name="Ink Business Cash Credit Card",
                ownership="Business",
                date_opened=dt.date.today() - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                bonus_earned_date=dt.date.today() - dt.timedelta(days=365),
                status="Active",
            )
        )
        db.commit()

        plan = household.build_household(db)
        referral = next(
            row
            for row in plan["referrals"]
            if row["to_user"] == "User B"
            and row["product_name"] == "Ink Business Preferred Credit Card"
        )
        move = next(
            m
            for m in plan["moves"]
            if m["user"] == "User B"
            and m["product_name"] == "Ink Business Preferred Credit Card"
        )

        self.assertEqual(referral["from_user"], "User A")
        # A cross-variant (super-family) match is a family route, never "exact" —
        # User A holds Ink Cash, not Ink Preferred.
        self.assertEqual(referral["referral_match"], "family")
        self.assertIn("Refer via User A", referral["route"])
        self.assertIn("chase_ink", referral["route"])
        self.assertIn("Ink Cash", referral["reason"])
        self.assertIn("Ink Preferred", referral["reason"])
        self.assertNotIn("already holds Ink Preferred", referral["reason"])
        self.assertEqual(move["referral_match"], "family")
        self.assertIn("chase_ink", move["route"])

    def test_super_family_amex_gold_cross_referral_is_family_route(self):
        db = self._session()
        business_gold = models.CardProduct(
            issuer="American Express",
            product_name="American Express Business Gold Card",
            currency="Amex Membership Rewards",
            ownership="Business",
            annual_fee=0,
            current_offer_points=70_000,
            current_offer_min_spend=10_000,
            current_offer_window_months=3,
            peak_offer_points=70_000,
            source_url="https://www.americanexpress.com/us/credit-cards/business/business-gold-card/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                business_gold,
                models.Valuation(currency="Amex Membership Rewards", cpp_scraped=1.0),
            ]
        )
        db.commit()
        add_current_offer_evidence(db, [business_gold])
        db.commit()
        db.add(
            models.HeldCard(
                user="User A",
                issuer="American Express",
                product_name="American Express Gold Card",
                date_opened=dt.date.today() - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                bonus_earned_date=dt.date.today() - dt.timedelta(days=365),
                status="Active",
            )
        )
        db.commit()

        plan = household.build_household(db)
        referral = next(
            row
            for row in plan["referrals"]
            if row["to_user"] == "User B"
            and row["product_name"] == "American Express Business Gold Card"
        )

        self.assertEqual(referral["from_user"], "User A")
        self.assertEqual(referral["referral_match"], "family")
        self.assertIn("Refer via User A", referral["route"])
        self.assertIn("amex_mr_gold", referral["route"])
        self.assertIn("Amex Gold", referral["reason"])
        self.assertIn("Amex Business Gold", referral["reason"])
        self.assertNotIn("already holds", referral["reason"])

    def test_referral_value_lifts_move_above_better_pipeline_rank(self):
        db = self._session()
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=95,
            current_offer_points=75_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=75_000,
            referral_bonus_points=25_000,
            source_url="https://www.capitalone.com/credit-cards/venture/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        rival = models.CardProduct(
            issuer="Citi",
            product_name="Citi Strata Premier Card",
            currency="Citi ThankYou Points",
            annual_fee=0,
            current_offer_points=75_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=75_000,
            source_url="https://www.citi.com/credit-cards/citi-strata-premier-credit-card",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                venture,
                rival,
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.0),
                models.Valuation(currency="Citi ThankYou Points", cpp_scraped=1.0),
            ]
        )
        db.commit()
        add_current_offer_evidence(db, [venture, rival])
        db.commit()
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Capital One Venture X Rewards Credit Card",
                date_opened=dt.date.today() - dt.timedelta(days=365),
                welcome_bonus_earned=True,
                bonus_earned_date=dt.date.today() - dt.timedelta(days=365),
                status="Active",
            )
        )
        db.commit()

        plan = household.build_household(db)
        user_b_moves = [m for m in plan["moves"] if m["user"] == "User B"]

        # Citi ranks first in User B's raw pipeline (higher offer_value), but the
        # referral bonus lifts Venture's household value above it.
        self.assertEqual(
            [m["product_name"] for m in user_b_moves],
            ["Capital One Venture Rewards Credit Card", "Citi Strata Premier Card"],
        )
        self.assertEqual(user_b_moves[0]["pipeline_rank"], 2)
        self.assertEqual(user_b_moves[1]["pipeline_rank"], 1)
        self.assertGreater(
            user_b_moves[0]["household_value"], user_b_moves[1]["household_value"]
        )
        self.assertEqual(user_b_moves[0]["referral_from"], "User A")

    def test_referral_table_orders_household_gain_before_pipeline_rank(self):
        db = self._session()
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=95,
            current_offer_points=75_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=75_000,
            referral_bonus_points=25_000,
            source_url="https://www.capitalone.com/credit-cards/venture/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        gold = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            currency="Amex Membership Rewards",
            annual_fee=0,
            current_offer_points=70_000,
            current_offer_min_spend=6000,
            current_offer_window_months=6,
            peak_offer_points=70_000,
            source_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                venture,
                gold,
                models.Valuation(currency="Capital One Miles", cpp_scraped=1.0),
                models.Valuation(currency="Amex Membership Rewards", cpp_scraped=1.0),
            ]
        )
        db.commit()
        add_current_offer_evidence(db, [venture, gold])
        db.commit()
        db.add_all(
            [
                models.HeldCard(
                    user="User A",
                    issuer="Capital One",
                    product_name="Capital One Venture X Rewards Credit Card",
                    date_opened=dt.date.today() - dt.timedelta(days=400),
                    welcome_bonus_earned=True,
                    bonus_earned_date=dt.date.today() - dt.timedelta(days=400),
                    status="Active",
                ),
                models.HeldCard(
                    user="User A",
                    issuer="American Express",
                    product_name="American Express Business Gold Card",
                    ownership="Business",
                    date_opened=dt.date.today() - dt.timedelta(days=365),
                    welcome_bonus_earned=True,
                    bonus_earned_date=dt.date.today() - dt.timedelta(days=365),
                    status="Active",
                ),
            ]
        )
        db.commit()

        plan = household.build_household(db)
        b_rows = [r for r in plan["referrals"] if r["to_user"] == "User B"]

        # Amex Gold has the better pipeline rank (rank 1, higher offer_value) but
        # Venture carries the referral bonus: higher household_gain wins.
        self.assertEqual(
            [r["product_name"] for r in b_rows],
            ["Capital One Venture Rewards Credit Card", "American Express Gold Card"],
        )
        self.assertGreater(b_rows[0]["household_gain"], b_rows[1]["household_gain"])
        self.assertEqual(b_rows[0]["pipeline_rank"], 2)
        self.assertEqual(b_rows[1]["pipeline_rank"], 1)

    def _band_status_products(self):
        """Chase WATCH ($505), Amex Green WATCH ($800), Citi APPLY NOW ($700)."""
        chase_watch = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred",
            currency="Chase Ultimate Rewards",
            annual_fee=95,
            current_offer_points=60_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=80_000,
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        amex_watch = models.CardProduct(
            issuer="American Express",
            product_name="Amex Green Card",
            currency="Amex Membership Rewards",
            annual_fee=0,
            current_offer_points=80_000,
            current_offer_min_spend=3000,
            current_offer_window_months=6,
            peak_offer_points=105_000,
            source_url="https://www.americanexpress.com/us/credit-cards/card/green/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        citi_apply = models.CardProduct(
            issuer="Citi",
            product_name="Citi Strata Premier Card",
            currency="Citi ThankYou Points",
            annual_fee=0,
            current_offer_points=70_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=70_000,
            source_url="https://www.citi.com/credit-cards/citi-strata-premier-credit-card",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        valuations = [
            models.Valuation(currency="Chase Ultimate Rewards", cpp_scraped=1.0),
            models.Valuation(currency="Amex Membership Rewards", cpp_scraped=1.0),
            models.Valuation(currency="Citi ThankYou Points", cpp_scraped=1.0),
        ]
        return [chase_watch, amex_watch, citi_apply, *valuations]

    def test_move_ordering_chase_band_beats_status_under_524(self):
        db = self._session()
        db.add_all(self._band_status_products())
        db.commit()
        products = db.query(models.CardProduct).all()
        add_current_offer_evidence(db, products)
        db.commit()

        plan = household.build_household(db)
        a_moves = [m for m in plan["moves"] if m["user"] == "User A"]

        # Under 5/24 the Chase band wins even though the Chase card is only WATCH
        # and worth less; among the rest, household value beats status — the same
        # precedence the Pipeline page uses.
        self.assertEqual(
            [m["issuer"] for m in a_moves],
            ["Chase", "American Express", "Citi"],
        )
        self.assertEqual(a_moves[0]["status"], "WATCH")
        self.assertTrue(a_moves[0]["chase_urgent"])
        self.assertEqual(a_moves[2]["status"], "APPLY NOW")
        self.assertGreater(a_moves[1]["household_value"], a_moves[2]["household_value"])

    def test_move_ordering_value_decides_over_524(self):
        db = self._session()
        db.add_all(self._band_status_products())
        db.commit()
        products = db.query(models.CardProduct).all()
        add_current_offer_evidence(db, products)
        db.commit()
        for user in ("User A", "User B"):
            for i in range(5):
                db.add(
                    models.HeldCard(
                        user=user,
                        issuer="Local Bank",
                        product_name=f"Local Card {i}",
                        date_opened=dt.date.today() - dt.timedelta(days=300),
                        status="Active",
                    )
                )
        db.commit()

        plan = household.build_household(db)
        a_moves = [m for m in plan["moves"] if m["user"] == "User A"]

        # Over 5/24 the Chase card is eligibility-gated out of the queue entirely,
        # no move carries the Chase band, and household value decides: the higher
        # -value WATCH card outranks the lower-value APPLY NOW card.
        self.assertFalse(any(m["issuer"] == "Chase" for m in plan["moves"]))
        self.assertFalse(any(m["chase_urgent"] for m in plan["moves"]))
        self.assertEqual([m["issuer"] for m in a_moves], ["American Express", "Citi"])
        self.assertEqual(a_moves[0]["status"], "WATCH")
        self.assertEqual(a_moves[1]["status"], "APPLY NOW")

    def test_pace_guardrail_flags_moves_at_quarter_cap(self):
        db = self._session()
        citi = models.CardProduct(
            issuer="Citi",
            product_name="Citi Strata Premier Card",
            currency="Citi ThankYou Points",
            annual_fee=0,
            current_offer_points=70_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=70_000,
            source_url="https://www.citi.com/credit-cards/citi-strata-premier-credit-card",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all([citi, models.Valuation(currency="Citi ThankYou Points", cpp_scraped=1.0)])
        db.commit()
        add_current_offer_evidence(db, [citi])
        db.commit()
        for user in ("User A", "User B"):
            for i in range(2):
                db.add(
                    models.HeldCard(
                        user=user,
                        issuer="Local Bank",
                        product_name=f"Local Card {i}",
                        date_opened=dt.date.today(),
                        status="Active",
                    )
                )
        db.commit()

        plan = household.build_household(db)

        self.assertEqual(plan["quarter_apps"], 4)
        self.assertTrue(plan["at_pace_cap"])
        self.assertTrue(plan["moves"])
        for move in plan["moves"]:
            self.assertIsNotNone(move["pace_warning"])
            self.assertIn("4/4", move["pace_warning"])

    def test_pace_guardrail_absent_under_quarter_cap(self):
        db = self._session()
        citi = models.CardProduct(
            issuer="Citi",
            product_name="Citi Strata Premier Card",
            currency="Citi ThankYou Points",
            annual_fee=0,
            current_offer_points=70_000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=70_000,
            source_url="https://www.citi.com/credit-cards/citi-strata-premier-credit-card",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all([citi, models.Valuation(currency="Citi ThankYou Points", cpp_scraped=1.0)])
        db.commit()
        add_current_offer_evidence(db, [citi])
        db.commit()
        for user in ("User A", "User B"):
            db.add(
                models.HeldCard(
                    user=user,
                    issuer="Local Bank",
                    product_name="Local Card 0",
                    date_opened=dt.date.today(),
                    status="Active",
                )
            )
        db.commit()

        plan = household.build_household(db)

        self.assertEqual(plan["quarter_apps"], 2)
        self.assertFalse(plan["at_pace_cap"])
        self.assertTrue(plan["moves"])
        for move in plan["moves"]:
            self.assertIsNone(move["pace_warning"])

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
