import datetime as dt
import json
import unittest

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.ingestion import extract, validate
from backend.logic import catalog, household, pipeline
from backend.logic.decision_context import DecisionContext
from backend.routers.profiles import _profile_summary


AS_OF = dt.date(2026, 9, 5)
SOURCE = "https://example.test/fictional-rewards-card"


def _product(**overrides) -> models.CardProduct:
    data = {
        "issuer": "Fictional Bank",
        "product_name": "Fictional Rewards Card",
        "currency": "Fictional Points",
        "annual_fee": 95,
        "current_offer_points": 80000,
        "current_offer_min_spend": 4000,
        "current_offer_window_months": 3,
        "peak_offer_points": 90000,
        "source_url": SOURCE,
        "last_verified": dt.datetime.combine(AS_OF, dt.time(12, 0)),
    }
    data.update(overrides)
    return models.CardProduct(**data)


def _evidence(
    product: models.CardProduct,
    field: str,
    value,
    *,
    fetched_at: str = "2026-09-04T12:00:00",
    offer_status: str = "public",
    source_url: str = SOURCE,
    created_at: dt.datetime | None = None,
    confidence: float = 0.95,
    evidence_snippets=None,
) -> models.IngestionEvidence:
    return models.IngestionEvidence(
        product_id=product.id,
        field=field,
        value_json=json.dumps(value),
        source_url=source_url,
        fetched_at=fetched_at,
        content_hash=f"hash-{field}",
        confidence=confidence,
        evidence_snippets=(
            {field: [f"Fictional Rewards Card {field} evidence."]}
            if evidence_snippets is None
            else evidence_snippets
        ),
        offer_status=offer_status,
        created_at=created_at,
    )


class OfferFreshnessTests(unittest.TestCase):
    def setUp(self):
        self._old_users = config.USERS
        self._old_key = config.FERNET_KEY
        config.USERS = ["User A", "User B"]
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()
        config.USERS = self._old_users
        config.FERNET_KEY = self._old_key

    def _ready_product(self, **overrides) -> models.CardProduct:
        product = _product(**overrides)
        self.db.add(product)
        self.db.flush()
        rows = [
            _evidence(product, "current_offer_points", product.current_offer_effective),
            _evidence(product, "current_offer_min_spend", product.current_offer_min_spend),
            _evidence(product, "current_offer_window_months", product.current_offer_window_months),
        ]
        if product.offer_expiration:
            rows.append(_evidence(product, "offer_expiration", product.offer_expiration))
        self.db.add_all(rows)
        self.db.add(models.Valuation(currency=product.currency, cpp_scraped=1.0))
        self.db.commit()
        self.db.refresh(product)
        return product

    def test_fresh_terms_are_ready_and_no_expiration_is_explicitly_unknown(self):
        product = self._ready_product()
        quality = catalog.current_offer_quality(product, list(self.db.scalars(select(models.IngestionEvidence))), AS_OF)

        self.assertTrue(quality["ready"])
        self.assertEqual("fresh_unknown_expiration", quality["status"])
        entry = catalog.scored_catalog(self.db, "User A", as_of=AS_OF)[0]
        self.assertTrue(entry["decision_ready"])
        self.assertEqual(quality, entry["current_offer_quality"])

    def test_expiration_is_persisted_and_expired_projection_is_not_ready(self):
        product = self._ready_product(offer_expiration="2026-09-01")
        quality = catalog.current_offer_quality(
            product,
            list(self.db.scalars(select(models.IngestionEvidence))),
            AS_OF,
        )

        self.assertFalse(quality["ready"])
        self.assertEqual("expired", quality["status"])
        entry = catalog.scored_catalog(self.db, "User A", as_of=AS_OF)[0]
        self.assertFalse(entry["decision_ready"])
        self.assertIn("expired", entry["data_quality_issues"])

    def test_old_fetched_terms_stay_stale_even_when_evidence_was_created_today(self):
        product = _product()
        self.db.add(product)
        self.db.flush()
        self.db.add_all(
            [
                _evidence(
                    product,
                    "current_offer_points",
                    product.current_offer_effective,
                    fetched_at="2026-07-01T12:00:00",
                    created_at=dt.datetime(2026, 9, 5, 12, 0),
                ),
                _evidence(product, "current_offer_min_spend", product.current_offer_min_spend),
                _evidence(product, "current_offer_window_months", product.current_offer_window_months),
                _evidence(product, "annual_fee", product.annual_fee),
            ]
        )
        self.db.add(models.Valuation(currency=product.currency, cpp_scraped=1.0))
        self.db.commit()

        entry = catalog.scored_catalog(self.db, "User A", as_of=AS_OF)[0]
        self.assertFalse(entry["decision_ready"])
        self.assertEqual("current_offer_evidence_stale", entry["current_offer_status"])

    def test_fresh_mismatched_offer_cannot_certify_persisted_terms(self):
        product = _product()
        self.db.add(product)
        self.db.flush()
        self.db.add_all(
            [
                _evidence(product, "current_offer_points", 120000),
                _evidence(product, "current_offer_min_spend", product.current_offer_min_spend),
                _evidence(product, "current_offer_window_months", product.current_offer_window_months),
            ]
        )
        self.db.add(models.Valuation(currency=product.currency, cpp_scraped=1.0))
        self.db.commit()

        entry = catalog.scored_catalog(self.db, "User A", as_of=AS_OF)[0]
        self.assertFalse(entry["decision_ready"])
        self.assertEqual("current_offer_evidence_mismatch", entry["current_offer_status"])
        self.assertIn("current_offer_evidence_mismatch", entry["data_quality_issues"])

    def test_low_confidence_or_missing_snippets_cannot_refresh_current_terms(self):
        product = _product()
        self.db.add(product)
        self.db.flush()
        self.db.add_all(
            [
                _evidence(
                    product,
                    "current_offer_points",
                    product.current_offer_effective,
                    confidence=0.5,
                ),
                _evidence(
                    product,
                    "current_offer_min_spend",
                    product.current_offer_min_spend,
                    evidence_snippets={},
                ),
                _evidence(product, "current_offer_window_months", product.current_offer_window_months),
            ]
        )
        self.db.add(models.Valuation(currency=product.currency, cpp_scraped=1.0))
        self.db.commit()

        quality = catalog.current_offer_quality(
            product,
            list(self.db.scalars(select(models.IngestionEvidence))),
            AS_OF,
        )

        self.assertFalse(quality["ready"])
        self.assertEqual("current_offer_evidence_unknown", quality["status"])
        self.assertIn("current_offer_points: unknown", quality["reason"])
        self.assertIn("current_offer_min_spend: unknown", quality["reason"])

    def test_unknown_status_and_invalid_expiration_remain_visible_but_conditional(self):
        product = _product(offer_expiration="sometime soon")
        self.db.add(product)
        self.db.flush()
        self.db.add_all(
            [
                _evidence(product, "current_offer_points", product.current_offer_effective, offer_status="unknown"),
                _evidence(product, "current_offer_min_spend", product.current_offer_min_spend, offer_status="unknown"),
                _evidence(product, "current_offer_window_months", product.current_offer_window_months, offer_status="unknown"),
            ]
        )
        self.db.add(models.Valuation(currency=product.currency, cpp_scraped=1.0))
        self.db.commit()

        result = pipeline.build_pipeline(self.db, "User A", as_of=AS_OF)
        self.assertEqual([], result["next_cards"])
        self.assertEqual(1, len(result["needs_data"]))
        self.assertEqual("invalid_expiration", result["needs_data"][0]["current_offer_status"])
        self.assertFalse(result["needs_data"][0]["decision_ready"])

    def test_targeted_private_amount_does_not_certify_public_terms(self):
        product = self._ready_product()
        self.db.add(
            models.ManualTargetedOffer(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                offer_points=150000,
            )
        )
        self.db.commit()
        context = DecisionContext.load(self.db)
        entry = catalog.scored_catalog(self.db, "User A", context=context, as_of=AS_OF)[0]

        self.assertTrue(entry["decision_ready"])
        self.assertEqual(150000, entry["my_targeted_offer_points"])
        self.assertEqual(80000, entry["current_offer_effective"])
        self.assertEqual("fresh_unknown_expiration", entry["current_offer_status"])

    def test_selected_private_offer_requires_its_own_terms_across_shared_plan(self):
        config.FERNET_KEY = Fernet.generate_key().decode("utf-8")
        product = self._ready_product()
        self.db.add_all([
            models.UserProfile(user="User A", organic_monthly_capacity=5000),
            models.UserProfile(user="User B", organic_monthly_capacity=5000),
            models.ManualTargetedOffer(
                user="User A", product_id=product.id, issuer=product.issuer,
                product_name=product.product_name, offer_points=150000,
            ),
        ])
        self.db.commit()
        context = DecisionContext.load(self.db)
        planned = pipeline.build_pipeline(self.db, "User A", context=context, as_of=AS_OF)
        household_plan = household.build_household(self.db, context=context, as_of=AS_OF)["next_move"]
        candidate = planned["next_cards"][0]
        primary = household_plan["primary"]
        self.assertEqual(150000, candidate["effective_points"])
        self.assertEqual(150000, primary["welcome_points"])
        for selected in (candidate, primary):
            self.assertTrue(selected["conditional"])
            self.assertEqual("unverified_targeted_terms", selected["condition_kind"])
            self.assertIn("private offer", selected["condition_reason"])
            self.assertTrue(selected["current_offer_quality"]["ready"])
        self.assertEqual(candidate["condition_reason"], primary["condition_reason"])
        self.assertTrue(household_plan["successors"])
        for successor in household_plan["successors"]:
            self.assertTrue(successor["conditional"])
            self.assertIn("selected private offer", successor["condition_reason"])
        self.assertEqual(0, len(list(self.db.scalars(select(models.HeldCard)))))

    def test_current_utc_fetch_is_available_during_the_local_planning_day(self):
        product = self._ready_product()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        rows = list(self.db.scalars(select(models.IngestionEvidence)))
        for row in rows:
            row.fetched_at = now
        quality = catalog.current_offer_quality(product, rows, dt.date.today())
        self.assertTrue(quality["ready"], quality)

    def test_referral_carries_recipient_capacity_condition(self):
        config.FERNET_KEY = Fernet.generate_key().decode("utf-8")
        product = self._ready_product()
        product.referral_bonus_points = 10000
        self.db.add(models.HeldCard(
            user="User B", product_id=product.id, issuer=product.issuer,
            product_name=product.product_name, status="Active",
            date_opened=AS_OF - dt.timedelta(days=500),
        ))
        self.db.commit()
        plan = household.build_household(self.db, as_of=AS_OF)
        referral = plan["referrals"][0]
        primary = plan["next_move"]["primary"]
        self.assertEqual("User A", referral["to_user"])
        self.assertTrue(referral["conditional"])
        self.assertEqual(primary["condition_reason"], referral["condition_reason"])
        self.assertEqual(primary["spend_capacity"], referral["spend_capacity"])

    def test_ingestion_persists_public_expiration_evidence(self):
        product = _product(
            current_offer_points=None,
            current_offer_min_spend=None,
            current_offer_window_months=None,
            peak_offer_points=None,
            last_verified=None,
        )
        self.db.add(product)
        self.db.commit()
        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            currency=product.currency,
            current_offer_points=80000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=90000,
            offer_expiration="2026-12-31",
            offer_status="public",
            source_url=SOURCE,
            fetched_at="2026-09-04T12:00:00",
            content_hash="fixture-hash",
            evidence_snippets={
                "current_offer_points": ["Earn 80,000 points."],
                "current_offer_min_spend": ["after $4,000 in 3 months"],
                "current_offer_window_months": ["in 3 months"],
                "offer_expiration": ["Offer ends December 31, 2026."],
            },
        )

        result = validate.apply_extraction(self.db, product, ext, SOURCE, commit=True)
        self.db.refresh(product)
        self.assertIn("offer_expiration", result["committed"])
        self.assertEqual("2026-12-31", product.offer_expiration)
        self.assertTrue(
            self.db.scalar(
                select(models.IngestionEvidence).where(
                    models.IngestionEvidence.product_id == product.id,
                    models.IngestionEvidence.field == "offer_expiration",
                )
            )
        )

    def test_later_dated_commitment_participates_once_in_whole_horizon(self):
        product = self._ready_product(current_offer_min_spend=3000, current_offer_window_months=3)
        self.db.add(models.UserProfile(user="User A", organic_monthly_capacity=1000))
        self.db.add(
            models.HeldCard(
                user="User A",
                issuer="Other Bank",
                product_name="Existing Fictional Card",
                date_opened=AS_OF - dt.timedelta(days=60),
                min_spend_requirement=5000,
                min_spend_progress=0,
                min_spend_deadline=pipeline.elig.add_months(AS_OF, 6),
                status="Active",
            )
        )
        self.db.commit()
        projection = pipeline.spend_capacity_projection(
            DecisionContext.load(self.db),
            "User A",
            {"current_offer_min_spend": 3000, "current_offer_window_months": 3},
            as_of=AS_OF,
        )

        self.assertTrue(projection["conditional"])
        self.assertEqual("insufficient_capacity", projection["condition_kind"])
        self.assertEqual(5000, projection["spend_capacity"]["active_min_spend_remaining"])
        self.assertEqual(6, projection["spend_capacity"]["analysis_horizon_months"])

    def test_partial_month_deadline_does_not_receive_a_full_month_of_capacity(self):
        as_of = dt.date(2026, 9, 30)
        product = self._ready_product(current_offer_min_spend=100, current_offer_window_months=1)
        self.db.add(models.UserProfile(user="User A", organic_monthly_capacity=100))
        self.db.add(
            models.HeldCard(
                user="User A",
                issuer="Other Bank",
                product_name="Existing Fictional Card",
                date_opened=as_of - dt.timedelta(days=60),
                min_spend_requirement=50,
                min_spend_progress=0,
                min_spend_deadline=dt.date(2026, 10, 1),
                status="Active",
            )
        )
        self.db.commit()

        projection = pipeline.spend_capacity_projection(
            DecisionContext.load(self.db),
            "User A",
            {"current_offer_min_spend": 100, "current_offer_window_months": 1},
            as_of=as_of,
        )

        self.assertTrue(projection["conditional"])
        self.assertEqual("insufficient_capacity", projection["condition_kind"])
        self.assertEqual(1, projection["spend_capacity"]["analysis_horizon_months"])

    def test_connected_household_and_pipeline_share_fresh_offer_projection(self):
        product = self._ready_product(offer_expiration="2026-12-31")
        pipeline_payload = pipeline.build_pipeline(
            self.db,
            "User A",
            context=DecisionContext.load(self.db),
            as_of=AS_OF,
        )
        household_payload = household.build_household(
            self.db,
            context=DecisionContext.load(self.db),
            as_of=AS_OF,
        )

        card = pipeline_payload["next_cards"][0]
        move = household_payload["next_move"]["primary"]
        self.assertEqual(product.offer_expiration, card["offer_expiration"])
        self.assertEqual("fresh", card["current_offer_status"])
        self.assertEqual(card["current_offer_status"], move["current_offer_status"])
        self.assertEqual(card["current_offer_quality"], move["current_offer_quality"])

    def test_missing_profile_key_keeps_optional_capacity_unknown(self):
        config.FERNET_KEY = Fernet.generate_key().decode("utf-8")
        self.db.add(models.UserProfile(user="User A", organic_monthly_capacity=750))
        self.db.commit()
        self.db.expire_all()
        config.FERNET_KEY = ""

        context = DecisionContext.load(self.db)
        summary = _profile_summary(self.db, "User A", context=context)

        self.assertIsNone(context.organic_monthly_capacity_by_user["User A"])
        self.assertIsNone(summary["organic_monthly_capacity"])


if __name__ == "__main__":
    unittest.main()
