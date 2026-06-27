import datetime as dt
import json
from pathlib import Path
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.ingestion import extract, schedule, validate
from backend.logic import catalog, eligibility, pipeline, scoring
from backend.product_identity import canonical_product_key, product_display_name, product_reference, product_variant_key
from backend.routers.ingestion import _proposed_change_to_dict


class IngestionGuardTests(unittest.TestCase):
    def test_ingestion_modules_do_not_read_private_models(self):
        private_model_tokens = (
            "models.HeldCard",
            "models.UserProfile",
            "models.BenefitUsage",
            "models.ManualTargetedOffer",
            "models.TargetRedemption",
        )
        root = Path("backend/ingestion")
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in private_model_tokens:
                if token in text:
                    offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)

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

    def test_proposed_change_review_payload_summarizes_raw_values(self):
        product = models.CardProduct(id=1, issuer="Capital One", product_name="Venture X Rewards Credit Card")
        change = models.ProposedChange(
            id=1,
            target_table="card_product",
            target_id=1,
            field="card_benefits",
            old_value=json.dumps(["$300 annual Capital One Travel credit"]),
            new_value=json.dumps(
                [
                    "[json-ld] {\"@context\":\"https://schema.org\"}",
                    "[title] Capital One Venture X review",
                    "Access to Capital One Lounges and Priority Pass lounges worldwide.",
                ]
            ),
            source_url="https://thepointsguy.com/credit-cards/reviews/capital-one-venture-x-card-review",
            confidence=0.92,
            status="pending",
        )

        payload = _proposed_change_to_dict(change, product)

        self.assertEqual(payload["field_label"], "Benefits")
        self.assertIn("3 items", payload["new_preview"])
        self.assertIn("Priority Pass", payload["new_preview"])
        self.assertNotIn("json-ld", payload["new_preview"].lower())
        self.assertIn("raw source fragment", payload["review_note"])
        self.assertEqual(payload["source_domain"], "thepointsguy.com")

    def test_proposed_change_review_payload_flags_generic_currency(self):
        product = models.CardProduct(id=1, issuer="Chase", product_name="Marriott Bonvoy Boundless")
        change = models.ProposedChange(
            id=2,
            target_table="card_product",
            target_id=1,
            field="currency",
            old_value=json.dumps("Marriott Bonvoy"),
            new_value=json.dumps("points"),
            source_url="https://creditcards.chase.com/travel-credit-cards/marriott-bonvoy/boundless",
            confidence=0.9,
            status="pending",
        )

        payload = _proposed_change_to_dict(change, product)

        self.assertEqual(payload["old_preview"], "Marriott Bonvoy")
        self.assertEqual(payload["new_preview"], "points")
        self.assertIn("Generic currency", payload["review_note"])

    def test_generic_currency_extraction_normalizes_without_review_churn(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Marriott Bonvoy Boundless Credit Card",
            currency="Marriott Bonvoy",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            currency="points",
            offer_status="public",
            source_url="https://creditcards.chase.com/travel-credit-cards/marriott-bonvoy/boundless",
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertEqual([], result["rejected"])
        self.assertEqual([], result["committed"])
        self.assertEqual([], result["proposed"])
        self.assertEqual(product.currency, "Marriott Bonvoy")
        self.assertEqual([], db.scalars(select(models.ProposedChange)).all())

    def test_category_map_regression_is_rejected_before_commit(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Amazon Prime Visa",
            currency="cash back",
            earn_multipliers={"amazon": 5, "dining": 2, "gas": 2},
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            offer_status="public",
            source_url="https://creditcards.chase.com/cash-back-credit-cards/amazon",
        )
        ext.earn_multipliers = [extract.EarnMultiplier(category="dining", multiplier=5)]
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertIn("earn_multipliers", result["rejected"])
        self.assertEqual(product.earn_multipliers, {"amazon": 5, "dining": 2, "gas": 2})
        self.assertEqual([], db.scalars(select(models.ProposedChange)).all())

    def test_cleanup_rejects_existing_bad_proposals(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture Business",
            currency="Capital One Miles",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        change = models.ProposedChange(
            target_table="card_product",
            target_id=product.id,
            field="currency",
            old_value=json.dumps("Capital One Miles"),
            new_value=json.dumps("cash back"),
            source_url="https://www.capitalone.com/small-business/credit-cards/venture-business/",
            confidence=0.9,
            status="pending",
        )
        db.add(change)
        db.commit()

        result = validate.cleanup_bad_pending_changes(db)
        db.refresh(change)

        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(change.status, "rejected")
        self.assertEqual(change.reason_code, "generic_currency_degradation")

    def test_approval_refuses_bad_existing_proposal(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="Delta SkyMiles Gold American Express Card",
            currency="Delta SkyMiles",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        change = models.ProposedChange(
            target_table="card_product",
            target_id=product.id,
            field="currency",
            old_value=json.dumps("Delta SkyMiles"),
            new_value=json.dumps("miles"),
            source_url="https://www.americanexpress.com/us/credit-cards/card/delta-skymiles-gold-american-express-card/",
            confidence=0.9,
            status="pending",
        )
        db.add(change)
        db.commit()

        validate.approve_change(db, change)
        db.refresh(product)
        db.refresh(change)

        self.assertEqual(product.currency, "Delta SkyMiles")
        self.assertEqual(change.status, "rejected")
        self.assertEqual(change.reason_code, "generic_currency_degradation")

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

    def test_supplemental_facts_commit_without_bonus_line(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Useful Rewards Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer="Test Bank",
            card_name="Useful Rewards Card",
            source_url="https://example.test/useful-rewards",
            offer_status="unknown",
            confidence=0.86,
            found=True,
            annual_fee=95,
            earn_multipliers={"dining": 3, "travel": 2},
            best_category_uses={"dining": "3x", "travel": "2x"},
            card_benefits=["$100 annual travel credit"],
            evidence_snippets={
                "annual_fee": ["$95 annual fee."],
                "earn_multipliers": ["Earn 3x on dining and 2x on travel."],
                "best_category_uses": ["Earn 3x on dining and 2x on travel."],
                "card_benefits": ["$100 annual travel credit."],
            },
        )

        result = schedule._apply_scan_row(db, product, row)

        self.assertIn("earn_multipliers", result["committed"])
        self.assertIn("best_category_uses", result["committed"])
        self.assertIn("card_benefits", result["committed"])
        self.assertEqual(product.earn_multipliers, {"dining": 3.0, "travel": 2.0})
        self.assertEqual(product.best_category_uses, {"dining": "3x", "travel": "2x"})
        self.assertEqual(product.card_benefits, ["$100 annual travel credit"])
        self.assertIsNone(product.current_offer_points)

    def test_broad_roundup_cannot_write_card_specific_annual_fee(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="The Business Platinum Card from American Express",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer="American Express",
            card_name="The Business Platinum Card from American Express",
            source_url="https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
            offer_status="public",
            confidence=0.86,
            found=True,
            bonus_amount=200000,
            bonus_unit="points",
            annual_fee=95,
            evidence_snippets={
                "bonus_amount": ["The Business Platinum Card from American Express: 200,000 points after spend."],
                "annual_fee": ["Card has a $95 annual fee but this offer only requires $8,000 in spend."],
            },
        )

        result = schedule._apply_scan_row(db, product, row)

        self.assertIn("current_offer_points", result["committed"])
        self.assertNotIn("annual_fee", result["committed"])
        self.assertIsNone(product.annual_fee)
        self.assertEqual(product.current_offer_points, 200000)

    def test_official_product_page_large_current_offer_delta_auto_commits(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Reserve for Business Credit Card",
            currency="Chase Ultimate Rewards",
            current_offer_points=100000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            annual_fee=795,
            peak_offer_points=100000,
            source_url="https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
            last_verified=dt.datetime(2026, 6, 1),
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.92,
            issuer="Chase",
            card_name="Chase Sapphire Reserve for Business Credit Card",
            currency="Chase Ultimate Rewards",
            current_offer_points=200000,
            current_offer_min_spend=30000,
            current_offer_window_months=6,
            annual_fee=795,
            offer_status="public",
            source_url="https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
            evidence_snippets={
                "current_offer_points": ["Chase Sapphire Reserve for Business: earn 200,000 bonus points."],
                "current_offer_min_spend": ["after you spend $30,000 in the first 6 months"],
                "current_offer_window_months": ["after you spend $30,000 in the first 6 months"],
                "annual_fee": ["The annual fee is $795."],
            },
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertIn("current_offer_points", result["committed"])
        self.assertIn("current_offer_min_spend", result["committed"])
        self.assertIn("current_offer_window_months", result["committed"])
        self.assertEqual([], result["proposed"])
        self.assertEqual(product.current_offer_points, 200000)
        self.assertEqual(product.current_offer_min_spend, 30000)
        self.assertEqual(product.current_offer_window_months, 6)
        self.assertEqual(product.peak_offer_points, 200000)
        self.assertEqual([], db.scalars(select(models.ProposedChange)).all())

    def test_broad_roundup_large_current_offer_delta_still_queues_review(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Reserve for Business Credit Card",
            currency="Chase Ultimate Rewards",
            current_offer_points=100000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            annual_fee=795,
            peak_offer_points=100000,
            source_url="https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
            last_verified=dt.datetime(2026, 6, 1),
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer="Chase",
            card_name="Chase Sapphire Reserve for Business Credit Card",
            source_url="https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
            offer_status="public",
            confidence=0.92,
            found=True,
            bonus_amount=200000,
            bonus_unit="points",
            spend_requirement=30000,
            spend_window_months=6,
            evidence_snippets={
                "bonus_amount": ["Chase Sapphire Reserve for Business: 200,000 points."],
                "spend_requirement": ["Spend $30,000 in 6 months."],
            },
        )

        result = schedule._apply_scan_row(db, product, row)

        self.assertIn("current_offer_points", result["proposed"])
        self.assertEqual(product.current_offer_points, 100000)
        pending = db.scalars(select(models.ProposedChange)).all()
        self.assertTrue(any(change.field == "current_offer_points" for change in pending))

    def test_cross_product_source_cannot_write_or_replace_product_truth(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Business Gold Card",
            currency="Amex Membership Rewards",
            source_url="https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/american-express-business-gold-card/",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            issuer="American Express",
            card_name="American Express Business Gold Card",
            current_offer_points=75000,
            current_offer_min_spend=6000,
            annual_fee=150,
            offer_status="public",
            source_url="https://www.doctorofcredit.com/american-express-delta-gold-50000-miles-400-statement-credit",
            evidence_snippets={
                "current_offer_points": ["Delta Gold: 75,000 SkyMiles after spend."],
                "annual_fee": ["Delta Gold annual fee is $150."],
            },
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertNotIn("current_offer_points", result["committed"])
        self.assertNotIn("annual_fee", result["committed"])
        self.assertIsNone(product.current_offer_points)
        self.assertIsNone(product.annual_fee)
        self.assertEqual(
            product.source_url,
            "https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/american-express-business-gold-card/",
        )

    def test_targeted_trusted_source_still_does_not_write_public_current_offer(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            currency="Chase Ultimate Rewards",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            issuer="Chase",
            card_name="Chase Sapphire Preferred Card",
            current_offer_points=120000,
            current_offer_min_spend=5000,
            current_offer_window_months=3,
            is_targeted=True,
            offer_status="targeted",
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            evidence_snippets={
                "current_offer_points": ["Targeted offer: earn 120,000 points."],
            },
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertNotIn("current_offer_points", result["committed"])
        self.assertIn("targeted_peak_offer_points", result["committed"])
        self.assertIsNone(product.current_offer_points)
        self.assertEqual(product.targeted_peak_offer_points, 120000)

    def test_anniversary_bonus_snippet_cannot_write_current_welcome_offer(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            currency="Capital One Miles",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.92,
            current_offer_points=10000,
            source_url="https://thepointsguy.com/credit-cards/reviews/capital-one-venture-x-card-review",
            evidence_snippets={
                "bonus_amount": [
                    "Cardholders also receive 10,000 bonus miles every account anniversary."
                ]
            },
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertNotIn("current_offer_points", result["committed"])
        self.assertIsNone(product.current_offer_points)
        self.assertIsNone(product.peak_offer_points)

    def test_offer_scan_normalization_structures_benefits_and_use(self):
        row = extract.OfferScanRow(
            issuer="American Express",
            card_name="American Express Gold Card",
            source_url="https://example.test/amex-gold",
            offer_status="unknown",
            confidence=0.9,
            found=True,
            earn_multipliers={"restaurants": 4, "U.S. supermarkets": 4},
            best_category_uses={"dining": "4x-4x", "groceries": "4x at supermarkets"},
            card_benefits=[
                "Up to $10 monthly Uber Cash. Enrollment required.",
                {
                    "name": "Up to $50 in statement credits January through June and July through December at Resy restaurants.",
                    "value": "$50",
                    "frequency": "semiannual",
                    "category": "dining",
                    "evidence": "Card Members can earn up to $50 in statement credits semiannually at Resy restaurants.",
                },
                "[json-ld] {\"@context\":\"https://schema.org\"}",
            ],
        )

        normalized = extract.normalize_offer_scan_row(row)

        self.assertEqual(normalized.earn_multipliers, {"dining": 4.0, "groceries": 4.0})
        self.assertEqual(normalized.best_category_uses["dining"], "4x")
        self.assertEqual(len(normalized.card_benefits), 2)
        self.assertEqual(normalized.card_benefits[0]["name"], "Uber Cash")
        self.assertEqual(normalized.card_benefits[0]["frequency"], "monthly")
        self.assertEqual(normalized.card_benefits[1]["name"], "Resy credit")
        self.assertEqual(normalized.card_benefits[1]["frequency"], "semiannual")
        self.assertNotIn("json-ld", json.dumps(normalized.card_benefits).lower())

    def test_missing_supplemental_data_counts_as_incomplete(self):
        product = models.CardProduct(
            issuer="Test Bank",
            product_name="Complete Offer Card",
            current_offer_points=80000,
            current_offer_min_spend=4000,
            peak_offer_points=90000,
        )

        self.assertTrue(schedule._needs_public_data_backfill(product))

        product.earn_multipliers = {"dining": 3}
        product.best_category_uses = {"dining": "3x"}
        product.card_benefits = ["$100 annual travel credit"]

        self.assertFalse(schedule._needs_public_data_backfill(product))

    def test_missing_supplemental_data_only_forces_backfill_when_held(self):
        product = models.CardProduct(
            issuer="Test Bank",
            product_name="Complete Offer Card",
            current_offer_points=80000,
            current_offer_min_spend=4000,
            peak_offer_points=90000,
        )

        self.assertFalse(schedule._needs_public_data_backfill(product, held_for_benefit_backfill=False))
        self.assertTrue(schedule._needs_public_data_backfill(product, held_for_benefit_backfill=True))

    def test_official_closed_to_new_page_marks_product_ineligible_and_clears_current_offer(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Citi",
            product_name="Citi Custom Cash Card",
            currency="cash back",
            current_offer_cash=200,
            current_offer_min_spend=1500,
            current_offer_window_months=6,
            peak_offer_points=None,
            source_url="https://www.citi.com/credit-cards/citi-custom-cash-credit-card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer="Citi",
            card_name="Citi Custom Cash Card",
            source_url="https://www.citi.com/credit-cards/citi-custom-cash-credit-card",
            product_url="https://www.citi.com/credit-cards/citi-custom-cash-credit-card",
            offer_status="expired",
            confidence=0.92,
            found=True,
            eligibility_tags=["closed_to_new_applicants"],
            eligibility_language="Citi stopped accepting applications for this card on May 28, 2026.",
            evidence_snippets={
                "eligibility_language": [
                    "Citi stopped accepting applications for this card on May 28, 2026."
                ],
            },
        )

        result = schedule._apply_scan_row(db, product, row)
        eligibility_result = eligibility.eligibility(
            db,
            "User A",
            product.issuer,
            product.product_name,
            eligibility_tags=product.eligibility_tags,
        )

        self.assertIn("eligibility_tags", result["committed"])
        self.assertIn("closed_to_new_applicants", product.eligibility_tags)
        self.assertIsNone(product.current_offer_cash)
        self.assertIsNone(product.current_offer_min_spend)
        self.assertFalse(eligibility_result.eligible)
        self.assertEqual(eligibility_result.block_type, "permanent")

    def test_supplemental_search_has_separate_cooldown(self):
        old_days = config.SUPPLEMENTAL_SEARCH_COOLDOWN_DAYS
        config.SUPPLEMENTAL_SEARCH_COOLDOWN_DAYS = 14
        try:
            now = dt.datetime(2026, 6, 20)
            product = models.CardProduct(
                issuer="American Express",
                product_name="American Express Gold Card",
                last_supplemental_search_at=now - dt.timedelta(days=3),
            )
            self.assertFalse(schedule._supplemental_search_cooldown_open(product, now))
            product.last_supplemental_search_at = now - dt.timedelta(days=15)
            self.assertTrue(schedule._supplemental_search_cooldown_open(product, now))
        finally:
            config.SUPPLEMENTAL_SEARCH_COOLDOWN_DAYS = old_days

    def test_cobrand_currency_normalization_is_product_aware(self):
        cases = [
            (
                "American Express",
                "Delta SkyMiles Gold American Express Card",
                "Membership Rewards",
                "Delta SkyMiles",
            ),
            (
                "Chase",
                "Marriott Bonvoy Boundless Credit Card",
                "Ultimate Rewards",
                "Marriott Bonvoy",
            ),
            (
                "Capital One",
                "Capital One Venture Business",
                "cash back",
                "Capital One Miles",
            ),
            (
                "American Express",
                "American Express Gold Card",
                "Membership Rewards",
                "Amex Membership Rewards",
            ),
            (
                "Chase",
                "Chase Sapphire Preferred Card",
                "Ultimate Rewards",
                "Chase Ultimate Rewards",
            ),
        ]
        for issuer, name, extracted_currency, expected in cases:
            with self.subTest(name=name):
                db = self._session()
                product = models.CardProduct(issuer=issuer, product_name=name)
                db.add(product)
                db.commit()
                db.refresh(product)

                ext = extract.OfferExtraction(
                    found=True,
                    confidence=0.9,
                    currency=extracted_currency,
                    offer_status="public",
                    source_url="https://example.test/card",
                )
                result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

                self.assertIn("currency", result["committed"])
                self.assertEqual(product.currency, expected)

    def test_cobrand_variant_is_not_generic_issuer_ladder(self):
        delta_gold = product_variant_key(
            "American Express",
            "Delta SkyMiles Gold American Express Card",
        )
        delta_gold_alt = product_variant_key(
            "Delta",
            "Delta SkyMiles Gold Card from American Express",
        )
        amex_gold = product_variant_key("American Express", "American Express Gold Card")

        self.assertEqual(delta_gold, delta_gold_alt)
        self.assertNotEqual(delta_gold, amex_gold)

    def test_canonical_display_names_are_short_and_stable(self):
        cases = [
            ("American Express", "American Express Gold Card", "Amex Gold"),
            ("American Express", "American Express Business Gold Card", "Amex Business Gold"),
            ("American Express", "The Platinum Card from American Express", "Amex Platinum"),
            ("American Express", "The Business Platinum Card from American Express", "Amex Business Platinum"),
            ("Chase", "Chase Sapphire Preferred Card", "Sapphire Preferred"),
            ("Chase", "Chase Sapphire Reserve", "Sapphire Reserve"),
            ("Chase", "Chase Sapphire Reserve for Business Credit Card", "Sapphire Reserve Business"),
            ("Chase", "Chase Freedom Unlimited Credit Card", "Freedom Unlimited"),
            ("Capital One", "Capital One Venture X Rewards Credit Card", "Venture X"),
            ("Capital One", "Capital One Venture Rewards Credit Card", "Venture"),
            ("Capital One", "Capital One Venture X Business", "Venture X Business"),
            ("Bilt", "Bilt Blue Card", "Bilt Blue"),
            ("Bilt", "Bilt Obsidian Card", "Bilt Obsidian"),
            ("Bilt", "Bilt Palladium Card", "Bilt Palladium"),
            ("American Express", "Delta SkyMiles Gold American Express Card", "Delta Gold"),
            ("Chase", "Marriott Bonvoy Boundless Credit Card", "Marriott Boundless"),
        ]
        for issuer, name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(product_display_name(issuer, name), expected)

        ref = product_reference("American Express", "American Express Gold Card")
        self.assertEqual(ref["display_name"], "Amex Gold")
        self.assertEqual(canonical_product_key("American Express", "Gold"), "american_express:amex_gold")
        self.assertIn("American Express Gold Card", ref["search_terms"])

    def test_effective_catalog_collapses_same_cobrand_variant(self):
        db = self._session()
        weak_delta = models.CardProduct(
            issuer="American Express",
            product_name="Delta SkyMiles Gold American Express Card",
            currency="Delta SkyMiles",
        )
        rich_delta = models.CardProduct(
            issuer="Delta",
            product_name="Delta SkyMiles Gold Card from American Express",
            currency="Delta SkyMiles",
            current_offer_points=70000,
            source_url="https://example.test/delta-gold",
        )
        amex_gold = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            currency="Amex Membership Rewards",
        )
        db.add_all([weak_delta, rich_delta, amex_gold])
        db.commit()

        products = catalog.effective_catalog(db)
        names = {p.product_name for p in products}

        self.assertIn("Delta SkyMiles Gold Card from American Express", names)
        self.assertIn("American Express Gold Card", names)
        self.assertEqual(
            1,
            sum(1 for p in products if product_variant_key(p.issuer, p.product_name) == product_variant_key("Delta", "Delta SkyMiles Gold Card from American Express")),
        )

    def test_pipeline_suppresses_active_same_family_card(self):
        db = self._session()
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Venture Rewards Credit Card",
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
            product_name="Venture X Rewards Credit Card",
            currency="Capital One Miles",
            annual_fee=395,
            current_offer_points=75000,
            current_offer_min_spend=4000,
            current_offer_window_months=3,
            peak_offer_points=90000,
            source_url="https://www.capitalone.com/credit-cards/venture-x/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
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
                user="User A",
                issuer="Capital One",
                product_name="Venture X Rewards Credit Card",
                product_id=venture_x.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        result = pipeline.build_pipeline(db, "User A")

        self.assertNotIn("Venture Rewards Credit Card", {c["product_name"] for c in result["next_cards"]})
        self.assertIn("Venture Rewards Credit Card", {c["product_name"] for c in result["alternate_strategies"]})
        alternate = next(c for c in result["alternate_strategies"] if c["product_name"] == "Venture Rewards Credit Card")
        self.assertEqual(alternate["relationship"], "same_family_ladder")
        self.assertIn("Do not open as a duplicate", alternate["reason"])

    def test_pipeline_does_not_block_business_variant_for_personal_card(self):
        db = self._session()
        personal_gold = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            currency="Amex Membership Rewards",
            annual_fee=325,
            current_offer_points=60000,
            current_offer_min_spend=6000,
            current_offer_window_months=6,
            peak_offer_points=100000,
            source_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        business_gold = models.CardProduct(
            issuer="American Express",
            product_name="American Express Business Gold Card",
            ownership="Business",
            currency="Amex Membership Rewards",
            annual_fee=375,
            current_offer_points=100000,
            current_offer_min_spend=15000,
            current_offer_window_months=3,
            peak_offer_points=100000,
            source_url="https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/american-express-business-gold-card/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all(
            [
                personal_gold,
                business_gold,
                models.Valuation(currency="Amex Membership Rewards", cpp_override=1.5),
            ]
        )
        db.commit()
        db.refresh(personal_gold)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="American Express",
                product_name="American Express Gold Card",
                product_id=personal_gold.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        result = pipeline.build_pipeline(db, "User A")

        self.assertIn("American Express Business Gold Card", {c["product_name"] for c in result["next_cards"]})

    def test_five24_counts_closed_recent_personal_cards(self):
        db = self._session()
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Test Bank",
                product_name="Closed Personal Card",
                date_opened=dt.date.today() - dt.timedelta(days=30),
                reports_to_personal_credit=True,
                status="Closed",
            )
        )
        db.commit()

        result = eligibility.five24(db, "User A")

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
