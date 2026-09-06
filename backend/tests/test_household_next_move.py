import datetime as dt
import unittest

from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models, schemas
from backend.db import Base
from backend.logic import eligibility, household, pipeline
from backend.logic.decision_context import DecisionContext
from backend.routers.pipeline import get_pipeline
from backend.routers.profiles import upsert_profile
from backend.tests.offer_fixtures import add_current_offer_evidence


class HouseholdNextMoveTests(unittest.TestCase):
    def setUp(self):
        self._old_users = config.USERS
        self._old_key = config.FERNET_KEY
        config.USERS = ["User A", "User B"]
        config.FERNET_KEY = Fernet.generate_key().decode("utf-8")

    def tearDown(self):
        config.USERS = self._old_users
        config.FERNET_KEY = self._old_key

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    @staticmethod
    def _product(
        issuer="Chase",
        name="Chase Sapphire Fictional",
        *,
        min_spend=2000,
        window=3,
        reports=True,
        currency="Chase Ultimate Rewards",
        referral_bonus_points=None,
        current_points=100000,
        peak_points=100000,
    ):
        source_host = "chase.com" if "chase" in issuer.lower() else "americanexpress.com"
        return models.CardProduct(
            issuer=issuer,
            product_name=name,
            ownership="Personal",
            account_type="Credit Card",
            reports_to_personal_credit=reports,
            currency=currency,
            annual_fee=0,
            current_offer_points=current_points,
            current_offer_min_spend=min_spend,
            current_offer_window_months=window,
            peak_offer_points=peak_points,
            referral_bonus_points=referral_bonus_points,
            source_url=f"https://www.{source_host}/personal-credit-cards/fictional-card",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )

    def _seed_catalog(self, db, *products):
        db.add_all(
            [
                models.Valuation(currency=currency, cpp_scraped=1.0)
                for currency in {p.currency for p in products if p.currency}
            ]
        )
        db.add_all(products)
        db.commit()
        add_current_offer_evidence(db, products)
        db.commit()
        db.refresh(products[0])

    def test_connected_household_plan_has_one_primary_and_two_successors(self):
        db = self._session()
        product = self._product()
        self._seed_catalog(db, product)
        db.add_all(
            [
                models.UserProfile(user="User A", organic_monthly_capacity=1000),
                models.UserProfile(user="User B", organic_monthly_capacity=1000),
            ]
        )
        db.commit()

        before = db.query(models.HeldCard).count()
        result = household.build_household(db)
        primary = result["next_move"]["primary"]

        self.assertIsNotNone(primary)
        self.assertEqual((primary["user"], primary["id"]), (result["moves"][0]["user"], result["moves"][0]["id"]))
        self.assertLessEqual(len(result["next_move"]["successors"]), 2)
        self.assertEqual(before, db.query(models.HeldCard).count())
        self.assertNotIn(
            (primary["user"], primary["id"]),
            {(row["user"], row["id"]) for row in result["next_move"]["successors"]},
        )
        pipeline_payload = get_pipeline("User A", db)
        self.assertEqual(result["next_move"], pipeline_payload["household_next_move"])

    def test_hypothetical_application_reuses_reporting_and_offer_window(self):
        db = self._session()
        product = self._product()
        self._seed_catalog(db, product)
        context = DecisionContext.load(db)
        as_of = dt.date(2026, 9, 5)

        candidate = {
            "id": product.id,
            "issuer": product.issuer,
            "product_name": product.product_name,
            "ownership": "Business",
            "account_type": "Credit Card",
            "reports_to_personal_credit": False,
            "current_offer_min_spend": 2000,
            "current_offer_window_months": 3,
        }
        projected = context.clone_for_hypothetical_application("User A", candidate, as_of=as_of)
        synthetic = projected.held_by_user["User A"][-1]

        self.assertFalse(synthetic.reports_to_personal_credit)
        self.assertEqual(synthetic.min_spend_deadline, eligibility.add_months(as_of, 3))
        self.assertEqual(0, eligibility.five24(db, "User A", held=projected.held_by_user["User A"]).count)
        self.assertEqual([], context.held_by_user["User A"])

        candidate["reports_to_personal_credit"] = True
        personal = context.clone_for_hypothetical_application("User A", candidate, as_of=as_of)
        self.assertEqual(1, eligibility.five24(db, "User A", held=personal.held_by_user["User A"]).count)

    def test_connected_primary_recomputes_spend_524_and_referral_successors(self):
        db = self._session()
        chase = self._product(referral_bonus_points=10000)
        chase_alternate = self._product(
            name="Chase Freedom Fictional",
            min_spend=1500,
        )
        amex = self._product(
            issuer="American Express",
            name="American Express Fictional Gold",
            min_spend=2500,
            currency="Amex Membership Rewards",
        )
        self._seed_catalog(db, chase, chase_alternate, amex)
        as_of = dt.date.today()
        db.add_all(
            [
                models.UserProfile(user="User A", organic_monthly_capacity=1000),
                models.UserProfile(user="User B", organic_monthly_capacity=1000),
                models.HeldCard(
                    user="User B",
                    product_id=chase.id,
                    issuer=chase.issuer,
                    product_name=chase.product_name,
                    date_opened=as_of - dt.timedelta(days=400),
                    welcome_bonus_earned=True,
                    status="Active",
                ),
                models.HeldCard(
                    user="User B",
                    product_id=chase_alternate.id,
                    issuer=chase_alternate.issuer,
                    product_name=chase_alternate.product_name,
                    date_opened=as_of - dt.timedelta(days=400),
                    welcome_bonus_earned=True,
                    status="Active",
                ),
                models.HeldCard(
                    user="User B",
                    product_id=amex.id,
                    issuer=amex.issuer,
                    product_name=amex.product_name,
                    date_opened=as_of - dt.timedelta(days=400),
                    welcome_bonus_earned=True,
                    status="Active",
                ),
            ]
        )
        db.flush()
        for index in range(4):
            db.add(
                models.HeldCard(
                    user="User A",
                    issuer="Local Bank",
                    product_name=f"Recent Fictional Card {index}",
                    date_opened=as_of - dt.timedelta(days=30 + index),
                    status="Active",
                )
            )
        db.commit()

        plan = household.build_household(db)
        primary = plan["next_move"]["primary"]
        successors = plan["next_move"]["successors"]

        self.assertEqual("User A", primary["user"])
        self.assertEqual(chase.id, primary["id"])
        self.assertEqual(100.0, primary["referral_value"])
        self.assertIn("User B", primary["route"])
        self.assertLessEqual(len(successors), 2)
        self.assertTrue(all(row["conditional"] for row in successors), successors)
        self.assertTrue(
            all("hypothetical application" in row["condition_reason"] for row in successors),
            successors,
        )
        user_a_successors = [row for row in successors if row["user"] == "User A"]
        self.assertTrue(user_a_successors)
        self.assertTrue(
            all(row["conditional"] for row in user_a_successors),
            user_a_successors,
        )
        self.assertTrue(
            all(row["spend_capacity"]["active_min_spend_remaining"] == 2000 for row in user_a_successors),
            user_a_successors,
        )
        alternate = next(row for row in successors if row["id"] == chase_alternate.id)
        self.assertEqual("User A", alternate["user"])
        self.assertEqual("WAIT", alternate["status"])
        self.assertTrue(alternate["conditional"])
        context = DecisionContext.load(db)
        projected = context.clone_for_hypothetical_application("User A", primary)
        self.assertEqual(
            5,
            eligibility.five24(db, "User A", held=projected.held_by_user["User A"]).count,
        )

    def test_wait_can_rank_first_but_unresolved_timing_withholds_successors(self):
        db = self._session()
        wait_chase = self._product(
            name="Chase Sapphire Timing Fictional",
            current_points=60000,
            peak_points=100000,
        )
        apply_amex = self._product(
            issuer="American Express",
            name="American Express Immediate Fictional Gold",
            currency="Amex Membership Rewards",
            current_points=100000,
            peak_points=100000,
        )
        self._seed_catalog(db, wait_chase, apply_amex)

        plan = household.build_household(db)
        primary = plan["next_move"]["primary"]

        self.assertEqual("Chase", primary["issuer"])
        self.assertEqual("WAIT", primary["status"])
        self.assertEqual([], plan["next_move"]["successors"])
        self.assertIn("successors are withheld", plan["next_move"]["successor_reason"])

    def test_wait_with_recorded_eligibility_date_projects_at_that_date(self):
        db = self._session()
        wait_chase = self._product(
            name="Chase Sapphire Dated Fictional",
            current_points=100000,
            peak_points=100000,
        )
        amex = self._product(
            issuer="American Express",
            name="American Express Dated Fictional Gold",
            currency="Amex Membership Rewards",
        )
        self._seed_catalog(db, wait_chase, amex)
        as_of = dt.date.today()
        db.add(
            models.HeldCard(
                user="User B",
                product_id=wait_chase.id,
                issuer=wait_chase.issuer,
                product_name=wait_chase.product_name,
                date_opened=as_of - dt.timedelta(days=400),
                welcome_bonus_earned=True,
                status="Active",
            )
        )
        for index, days_ago in enumerate((2, 4, 10, 20)):
            db.add(
                models.HeldCard(
                    user="User A",
                    issuer="Chase" if index < 2 else "Local Bank",
                    product_name=f"Recent Dated Card {index}",
                    date_opened=as_of - dt.timedelta(days=days_ago),
                    status="Active",
                )
            )
        db.commit()

        expected_wait = as_of - dt.timedelta(days=4) + dt.timedelta(days=30)
        plan = household.build_household(db)
        primary = plan["next_move"]["primary"]

        self.assertEqual("User A", primary["user"])
        self.assertEqual(wait_chase.id, primary["id"])
        self.assertEqual("WAIT", primary["status"])
        self.assertEqual(expected_wait.isoformat(), primary["earliest_eligible_date"])
        self.assertEqual(expected_wait.isoformat(), plan["next_move"]["wait_until"])
        self.assertTrue(
            any(row["user"] == "User A" and row["id"] == wait_chase.id for row in plan["next_move"]["successors"]),
            plan["next_move"]["successors"],
        )

    def test_capacity_uses_candidate_window_once_for_multi_month_cash_flow(self):
        db = self._session()
        product = self._product(min_spend=2000, window=3)
        self._seed_catalog(db, product)
        as_of = dt.date(2026, 9, 5)
        db.add(
            models.UserProfile(user="User A", organic_monthly_capacity=1000)
        )
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Local Bank",
                product_name="Existing Fictional Card",
                date_opened=as_of - dt.timedelta(days=120),
                min_spend_requirement=1000,
                min_spend_progress=0,
                min_spend_deadline=eligibility.add_months(as_of, 1),
                status="Active",
            )
        )
        db.commit()
        context = DecisionContext.load(db)
        candidate = {
            "current_offer_min_spend": 2000,
            "current_offer_window_months": 3,
        }

        projection = pipeline.spend_capacity_projection(context, "User A", candidate, as_of=as_of)
        self.assertEqual(3000, projection["spend_capacity"]["horizon_budget"])
        self.assertEqual(1000, projection["spend_capacity"]["active_min_spend_remaining"])
        self.assertEqual(2000, projection["spend_capacity"]["available_after_commitments"])
        self.assertFalse(projection["conditional"])

        candidate["current_offer_min_spend"] = 2500
        insufficient = pipeline.spend_capacity_projection(context, "User A", candidate, as_of=as_of)
        self.assertTrue(insufficient["conditional"])
        self.assertEqual("insufficient_capacity", insufficient["condition_kind"])

    def test_unknown_timing_stays_visible_and_all_recorded_commitments_are_disclosed(self):
        db = self._session()
        self._seed_catalog(db, self._product())
        db.add(models.UserProfile(user="User A", organic_monthly_capacity=1000))
        as_of = dt.date(2026, 9, 5)
        db.add_all(
            [
                models.HeldCard(
                    user="User A",
                    issuer="Local Bank",
                    product_name="Unknown Deadline Card",
                    date_opened=as_of - dt.timedelta(days=120),
                    min_spend_requirement=1500,
                    min_spend_progress=0,
                    min_spend_deadline=None,
                    status="Active",
                ),
                models.HeldCard(
                    user="User A",
                    issuer="Local Bank",
                    product_name="Later Deadline Card",
                    date_opened=as_of - dt.timedelta(days=120),
                    min_spend_requirement=1500,
                    min_spend_progress=0,
                    min_spend_deadline=eligibility.add_months(as_of, 6),
                    status="Active",
                ),
            ]
        )
        db.commit()
        projection = pipeline.spend_capacity_projection(
            DecisionContext.load(db),
            "User A",
            {"current_offer_min_spend": 2000, "current_offer_window_months": 3},
            as_of=as_of,
        )

        self.assertTrue(projection["conditional"])
        self.assertEqual("unknown_commitment_timing", projection["condition_kind"])
        self.assertEqual(3000, projection["spend_capacity"]["active_min_spend_remaining"])
        self.assertTrue(projection["spend_capacity"]["unknown_commitment_timing"])

    def test_profile_capacity_omitted_preserves_and_explicit_null_clears(self):
        db = self._session()
        with self.assertRaises(ValidationError):
            schemas.ProfileUpsert(organic_monthly_capacity=float("inf"))

        db.add(models.UserProfile(user="User A", organic_monthly_capacity=750))
        db.commit()

        upsert_profile("User A", schemas.ProfileUpsert(notes="preserve capacity"), db)
        db.expire_all()
        self.assertEqual(750, db.get(models.UserProfile, "User A").organic_monthly_capacity)

        upsert_profile("User A", schemas.ProfileUpsert(organic_monthly_capacity=None), db)
        db.expire_all()
        self.assertIsNone(db.get(models.UserProfile, "User A").organic_monthly_capacity)


if __name__ == "__main__":
    unittest.main()
