import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import config, models, source_quality
from backend.db import Base
from backend.ingestion import extract, schedule, static_parse, validate
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

    def test_rejected_identical_proposal_is_not_requeued(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Business Gold Card",
            current_offer_points=200000,
            source_url="https://www.americanexpress.com/us/credit-cards/business/business-credit-cards/american-express-business-gold-card/",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.ProposedChange(
                target_table="card_product",
                target_id=product.id,
                field="current_offer_points",
                old_value=json.dumps(200000),
                new_value=json.dumps(250000),
                source_url="https://www.uscreditcardguide.com/amex-business-gold-rewards-card",
                confidence=0.95,
                status="rejected",
            )
        )
        db.commit()

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            current_offer_points=250000,
            offer_status="public",
            source_url="https://www.uscreditcardguide.com/amex-business-gold-rewards-card",
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)
        pending = db.scalars(
            select(models.ProposedChange).where(models.ProposedChange.status == "pending")
        ).all()

        self.assertIn("current_offer_points", result["rejected"])
        self.assertEqual(product.current_offer_points, 200000)
        self.assertEqual([], pending)

    def test_points_back_rebate_cap_cannot_be_welcome_offer(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Business Gold Card",
            current_offer_points=200000,
            source_url="https://www.uscreditcardguide.com/amex-business-gold-rewards-card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            current_offer_points=250000,
            offer_status="public",
            source_url="https://www.uscreditcardguide.com/amex-business-gold-rewards-card",
            evidence_snippets={
                "bonus_amount": [
                    "25% AIRLINE BONUS: receive 25% of Membership Rewards points back "
                    "after you use Pay With Points for a flight booked with American Express Travel, "
                    "up to 250,000 points back per calendar year."
                ],
            },
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        pending = db.scalars(
            select(models.ProposedChange).where(models.ProposedChange.status == "pending")
        ).all()
        evidence = db.scalars(select(models.IngestionEvidence)).all()

        self.assertNotIn("current_offer_points", result["committed"])
        self.assertNotIn("current_offer_points", result["proposed"])
        self.assertEqual(product.current_offer_points, 200000)
        self.assertEqual([], pending)
        self.assertFalse(any(row.field == "current_offer_points" for row in evidence))

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

    def test_known_citi_generic_currency_is_corrected_on_refresh(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Citi",
            product_name="Citi Custom Cash Card",
            currency="points",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.95,
            current_offer_points=20000,
            current_offer_min_spend=750,
            current_offer_window_months=3,
            offer_status="public",
            source_url="https://www.citi.com/credit-cards/citi-custom-cash-credit-card",
            evidence_snippets={
                "bonus_amount": ["Earn 20,000 points."],
                "spend_requirement": ["after spending $750"],
                "spend_window_months": ["in the first 3 months"],
            },
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertIn("currency", result["committed"])
        self.assertEqual(product.currency, "Citi ThankYou Points")
        self.assertEqual(product.current_offer_points, 20000)

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

    def test_static_benefit_extraction_skips_welcome_and_disclosure_fragments(self):
        evidence: dict[str, list[str]] = {}
        valid = "$100 annual travel credit for eligible bookings."
        hits = static_parse._extract_benefits(
            [
                "OUR BEST OFFER RETURNS Earn 150,000 bonus points after you spend $30,000 on purchases.",
                "While we don't cover all available credit cards, our editorial team reviews cards.",
                valid,
            ],
            evidence,
        )

        self.assertEqual(hits, [valid])
        self.assertEqual(evidence["card_benefits"], [valid])

    def test_offer_batch_json_fallback_parser_validates_rows(self):
        rows = extract._parse_offer_scan_batch_json(
            """
            ```json
            {
              "rows": [
                {
                  "issuer": "Test Bank",
                  "card_name": "Useful Rewards Card",
                  "bonus_amount": 75000,
                  "bonus_unit": "points",
                  "offer_status": "public",
                  "confidence": 0.91,
                  "evidence_snippets": {"bonus_amount": "Earn 75,000 points."},
                  "changed_fields": null,
                  "found": true
                }
              ]
            }
            ```
            """
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].issuer, "Test Bank")
        self.assertEqual(rows[0].bonus_amount, 75000)
        self.assertEqual(rows[0].offer_status, "public")
        self.assertEqual(rows[0].evidence_snippets["bonus_amount"], ["Earn 75,000 points."])
        self.assertEqual(rows[0].changed_fields, [])

    def test_safe_source_check_marks_verified_without_field_changes(self):
        db = self._session()
        source_url = "https://www.capitalone.com/credit-cards/venture-x"
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            source_url=source_url,
            current_offer_points=75000,
            peak_offer_points=100000,
            currency="Capital One Miles",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer=product.issuer,
            card_name=product.product_name,
            source_url=source_url,
            product_url=source_url,
            offer_status="unknown",
            confidence=0.92,
            found=True,
            evidence_snippets={"source": ["Capital One Venture X Rewards Credit Card source check."]},
        )

        result = schedule._apply_scan_row(db, product, row)

        self.assertTrue(result["verified_source"])
        self.assertIsNotNone(product.last_verified)
        self.assertEqual(product.source_url, source_url)
        self.assertFalse(result["committed"])
        self.assertFalse(result["proposed"])

    def test_raw_benefit_fragments_do_not_fall_back_to_catalog_write(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Useful Rewards Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        raw_fragment = (
            "Benefits include lounge access, travel protection, purchase protection, rental car coverage, "
            "and other useful account features described in this comparison table with editorial notes "
            "that should not be written as one catalog benefit."
        )
        ext = extract.OfferExtraction(
            found=True,
            confidence=0.9,
            issuer="Test Bank",
            card_name="Useful Rewards Card",
            card_benefits=[raw_fragment],
            evidence_snippets={"card_benefits": [raw_fragment]},
            source_url="https://example.test/useful-rewards",
        )

        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertNotIn("card_benefits", result["committed"])
        self.assertNotIn("card_benefits", result["proposed"])
        self.assertNotIn("card_benefits", result["rejected"])
        self.assertIsNone(product.card_benefits)

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
                # Outside the Capital One 6-month velocity window so this test
                # exercises same-family suppression, not the spacing rule.
                date_opened=dt.date.today() - dt.timedelta(days=200),
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

    def test_backfill_valuations_dedupes_duplicate_extracted_currency(self):
        db = self._session()
        db.add(
            models.CardProduct(
                issuer="Bilt",
                product_name="Bilt Mastercard",
                currency="Bilt Rewards",
            )
        )
        db.commit()
        source_url = "https://thepointsguy.com/loyalty-programs/monthly-valuations"
        extracted = [
            extract.CurrencyValuation(currency="Bilt Rewards", cpp=2.2, source_url=source_url),
            extract.CurrencyValuation(currency="Bilt Rewards", cpp=2.1, source_url=source_url),
        ]
        old_key = config.ANTHROPIC_API_KEY
        old_web = config.WEB_SEARCH_ENABLED
        try:
            config.ANTHROPIC_API_KEY = "test-key"
            config.WEB_SEARCH_ENABLED = True
            with (
                patch.object(
                    extract,
                    "research_point_valuations",
                    return_value={"text": "Bilt Rewards 2.2 cpp", "sources": [{"url": source_url}]},
                ),
                patch.object(extract, "extract_valuations", return_value=extracted),
            ):
                added = schedule.backfill_valuations(db)
        finally:
            config.ANTHROPIC_API_KEY = old_key
            config.WEB_SEARCH_ENABLED = old_web

        valuations = db.scalars(select(models.Valuation)).all()
        self.assertEqual(added, 1)
        self.assertEqual(len(valuations), 1)
        self.assertEqual(valuations[0].currency, "Bilt Rewards")
        self.assertEqual(valuations[0].cpp_scraped, 2.2)

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


class AutoResolveTests(unittest.TestCase):
    """System self-review of the proposed-change queue (2026-07-06)."""

    def _session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.db import Base
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def _pending(self, db, product, field, value, **kw):
        import json as _json
        change = models.ProposedChange(
            target_table="card_product", target_id=product.id, field=field,
            old_value=_json.dumps(getattr(product, field, None)),
            new_value=_json.dumps(value), status="pending", **kw,
        )
        db.add(change)
        db.commit()
        return change

    def test_evidence_backed_offer_auto_approves(self):
        import datetime as dt, json as _json
        db = self._session()
        product = models.CardProduct(issuer="Chase", product_name="Sapphire Preferred Card",
                                     current_offer_points=60000)
        db.add(product); db.commit()
        db.add(models.IngestionEvidence(
            product_id=product.id, field="current_offer_points",
            value_json=_json.dumps(75000), source_url="https://www.chase.com/sapphire",
            confidence=0.9, fetched_at=dt.datetime.now().isoformat(),
        ))
        db.commit()
        change = self._pending(db, product, "current_offer_points", 75000,
                               source_url="https://www.chase.com/sapphire")
        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["approved_count"], 1)
        db.refresh(product)
        self.assertEqual(product.current_offer_points, 75000)
        self.assertEqual(change.status, "approved")

    def test_implausible_value_auto_rejects(self):
        db = self._session()
        product = models.CardProduct(issuer="Chase", product_name="Freedom Flex")
        db.add(product); db.commit()
        change = self._pending(db, product, "annual_fee", 99999)
        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(change.status, "rejected")

    def test_peak_below_current_auto_rejects(self):
        db = self._session()
        product = models.CardProduct(issuer="Amex", product_name="Platinum Card",
                                     current_offer_points=150000)
        db.add(product); db.commit()
        change = self._pending(db, product, "peak_offer_points", 80000)
        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["rejected_count"], 1)
        self.assertIn("below the current offer", change.review_note)

    def test_peak_without_trusted_evidence_stays_pending(self):
        db = self._session()
        product = models.CardProduct(issuer="Amex", product_name="Platinum Card",
                                     current_offer_points=80000)
        db.add(product); db.commit()
        change = self._pending(db, product, "peak_offer_points", 175000)
        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["pending_count"], 1)
        self.assertEqual(change.status, "pending")

    def test_peak_with_trusted_evidence_auto_approves(self):
        import datetime as dt, json as _json
        db = self._session()
        product = models.CardProduct(issuer="Amex", product_name="Platinum Card",
                                     current_offer_points=80000)
        db.add(product); db.commit()
        db.add(models.IngestionEvidence(
            product_id=product.id, field="peak_offer_points",
            value_json=_json.dumps(175000),
            source_url="https://www.doctorofcredit.com/amex-platinum-175k",
            confidence=0.85, fetched_at=dt.datetime.now().isoformat(),
        ))
        db.commit()
        change = self._pending(db, product, "peak_offer_points", 175000)
        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["approved_count"], 1)
        db.refresh(product)
        self.assertEqual(product.peak_offer_points, 175000)

    # --- Autonomous doctrine (2026-07-16): the queue never needs a human ----

    def _evidence(self, db, product, field, value, source_url, confidence=0.9, created_at=None):
        row = models.IngestionEvidence(
            product_id=product.id,
            field=field,
            value_json=json.dumps(value),
            source_url=source_url,
            confidence=confidence,
            fetched_at=dt.datetime.now().isoformat(),
        )
        db.add(row)
        db.commit()
        if created_at is not None:
            row.created_at = created_at
            db.commit()
        return row

    def test_eligibility_from_one_untrusted_source_auto_rejects_retryably(self):
        db = self._session()
        product = models.CardProduct(issuer="Citi", product_name="Citi Custom Cash Card")
        db.add(product); db.commit()
        tags = ["closed_to_new_applicants"]
        self._evidence(db, product, "eligibility_tags", tags, "https://randomblog.example/citi-post", confidence=0.9)
        change = self._pending(db, product, "eligibility_tags", tags,
                               source_url="https://randomblog.example/citi-post")

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(report["pending_count"], 0)
        self.assertEqual(change.status, "rejected")
        self.assertEqual(change.reason_code, "unverified_eligibility_auto_reject")
        self.assertIn("re-verify on future refreshes", change.review_note)
        db.refresh(product)
        self.assertIsNone(product.eligibility_tags)
        # Retryable: a later refresh may re-propose the identical value.
        self.assertFalse(
            validate._matching_rejected_change_exists(db, product, "eligibility_tags", json.dumps(tags))
        )

    def test_eligibility_from_official_issuer_host_auto_approves(self):
        db = self._session()
        product = models.CardProduct(issuer="Chase", product_name="Chase Sapphire Preferred Card")
        db.add(product); db.commit()
        tags = ["sapphire_48mo"]
        self._evidence(
            db, product, "eligibility_tags", tags,
            "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            confidence=0.7,
        )
        change = self._pending(db, product, "eligibility_tags", tags)

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["approved_count"], 1)
        self.assertEqual(change.status, "approved")
        db.refresh(product)
        self.assertEqual(product.eligibility_tags, tags)

    def test_eligibility_from_two_independent_hosts_auto_approves(self):
        db = self._session()
        product = models.CardProduct(issuer="Citi", product_name="Citi Custom Cash Card")
        db.add(product); db.commit()
        tags = ["closed_to_new_applicants"]
        self._evidence(db, product, "eligibility_tags", tags, "https://bloga.example/citi", confidence=0.85)
        self._evidence(db, product, "eligibility_tags", tags, "https://blogb.example/citi", confidence=0.85)
        change = self._pending(db, product, "eligibility_tags", tags)

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["approved_count"], 1)
        db.refresh(product)
        self.assertEqual(product.eligibility_tags, tags)

    def test_large_offer_delta_cannot_self_approve_in_same_refresh(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card",
                                     current_offer_points=60000)
        db.add(product); db.commit()
        ext = extract.OfferExtraction(
            found=True,
            confidence=0.9,
            current_offer_points=100000,
            offer_status="public",
            source_url="https://cardblog.example/test-rewards-card-bonus",
            evidence_snippets={"bonus_amount": ["Test Rewards Card: 100,000 points after spend."]},
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)
        self.assertIn("current_offer_points", result["proposed"])

        # Same run: auto_resolve sees only the proposing extraction's own
        # evidence row — that is not corroboration.
        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["approved_count"], 0)
        self.assertEqual(report["pending_count"], 1)
        self.assertEqual(report["pending"][0]["reason"], "awaiting_corroboration")
        db.refresh(product)
        self.assertEqual(product.current_offer_points, 60000)

    def test_second_host_evidence_corroborates_pending_offer_change(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card",
                                     current_offer_points=60000)
        db.add(product); db.commit()
        self._evidence(db, product, "current_offer_points", 100000, "https://cardblog.example/a", confidence=0.9)
        change = self._pending(db, product, "current_offer_points", 100000,
                               source_url="https://cardblog.example/a")
        self._evidence(db, product, "current_offer_points", 100000, "https://otherblog.example/b", confidence=0.9)

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["approved_count"], 1)
        self.assertEqual(report["approved"][0]["basis"], "independent_host")
        db.refresh(product)
        self.assertEqual(product.current_offer_points, 100000)

    def test_later_refresh_reextraction_corroborates_same_host(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card",
                                     current_offer_points=60000)
        db.add(product); db.commit()
        change = self._pending(db, product, "current_offer_points", 100000,
                               source_url="https://cardblog.example/a")
        self._evidence(
            db, product, "current_offer_points", 100000, "https://cardblog.example/a",
            confidence=0.9, created_at=change.created_at + dt.timedelta(days=1),
        )

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["approved_count"], 1)
        self.assertEqual(report["approved"][0]["basis"], "later_refresh_reconfirmed")

    def test_uncorroborated_offer_change_expires_after_window(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card",
                                     current_offer_points=60000)
        db.add(product); db.commit()
        change = self._pending(db, product, "current_offer_points", 100000,
                               source_url="https://cardblog.example/a")
        change.created_at = validate._utcnow() - dt.timedelta(days=15)
        db.commit()
        self._evidence(
            db, product, "current_offer_points", 100000, "https://cardblog.example/a",
            confidence=0.9, created_at=change.created_at,
        )

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(change.status, "rejected")
        self.assertEqual(change.reason_code, "offer_change_never_corroborated")
        self.assertIn("never corroborated", change.review_note)
        db.refresh(product)
        self.assertEqual(product.current_offer_points, 60000)
        self.assertFalse(
            validate._matching_rejected_change_exists(db, product, "current_offer_points", json.dumps(100000))
        )

    def test_peak_evidence_from_lookalike_host_is_not_trusted(self):
        db = self._session()
        product = models.CardProduct(issuer="Amex", product_name="Platinum Card",
                                     current_offer_points=80000)
        db.add(product); db.commit()
        self._evidence(
            db, product, "peak_offer_points", 175000,
            "https://notdoctorofcredit.com/amex-platinum-175k", confidence=0.85,
        )
        change = self._pending(db, product, "peak_offer_points", 175000)

        report = validate.auto_resolve_pending_changes(db)

        self.assertEqual(report["approved_count"], 0)
        self.assertEqual(change.status, "pending")

    def test_stale_evidence_beyond_age_window_does_not_back_value(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card")
        db.add(product); db.commit()
        self._evidence(
            db, product, "current_offer_points", 75000, "https://www.chase.com/x",
            confidence=0.9, created_at=validate._utcnow() - dt.timedelta(days=61),
        )

        self.assertFalse(
            validate._evidence_backs_value(db, product.id, "current_offer_points", json.dumps(75000))
        )


class StrictHostMatchingTests(unittest.TestCase):
    def test_host_matches_is_strict(self):
        self.assertTrue(source_quality.host_matches("chase.com", "chase.com"))
        self.assertTrue(source_quality.host_matches("sub.chase.com", "chase.com"))
        self.assertTrue(source_quality.host_matches("www.doctorofcredit.com", "doctorofcredit.com"))
        self.assertFalse(source_quality.host_matches("purchase.com", "chase.com"))
        self.assertFalse(source_quality.host_matches("notdoctorofcredit.com", "doctorofcredit.com"))
        self.assertFalse(source_quality.host_matches("chase.com.evil.example", "chase.com"))

    def test_trusted_peak_source_uses_strict_hosts(self):
        from backend.ingestion import peak_research

        self.assertTrue(peak_research._trusted_peak_source("https://www.doctorofcredit.com/x"))
        self.assertTrue(peak_research._trusted_peak_source("https://sub.chase.com/x"))
        self.assertFalse(peak_research._trusted_peak_source("https://notdoctorofcredit.com/x"))
        self.assertFalse(peak_research._trusted_peak_source("https://purchase.com/x"))


class CommitPathGuardTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_implausible_first_sight_value_routes_to_review_not_commit(self):
        db = self._session()
        product = models.CardProduct(issuer="Test Bank", product_name="Test Rewards Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.9,
            current_offer_points=900000,  # above the 500k plausible ceiling
            offer_status="public",
            source_url="https://example.test/test-rewards-card",
            evidence_snippets={"bonus_amount": ["Earn 900,000 points after spend."]},
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertNotIn("current_offer_points", result["committed"])
        self.assertIn("current_offer_points", result["proposed"])
        self.assertIsNone(product.current_offer_points)

        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["rejected_count"], 1)
        self.assertIsNone(product.current_offer_points)

    def test_unknown_bonus_unit_never_becomes_points(self):
        row = extract.OfferScanRow(
            issuer="Test Bank",
            card_name="Test Rewards Card",
            offer_status="public",
            confidence=0.9,
            found=True,
            bonus_amount=500,
            bonus_unit=None,
            referral_bonus_amount=500,
            referral_bonus_unit=None,
        )

        ext = schedule._row_to_extraction(row)

        self.assertIsNone(ext.current_offer_points)
        self.assertIsNone(ext.current_offer_cash)
        self.assertIsNone(ext.referral_bonus_points)
        self.assertIsNone(ext.referral_bonus_cash)
        self.assertIn("bonus_unit_unknown", ext.needs_review_reason or "")

    def test_known_units_still_map_to_points_and_cash(self):
        points_row = extract.OfferScanRow(
            issuer="Test Bank", card_name="Test Rewards Card",
            offer_status="public", confidence=0.9, found=True,
            bonus_amount=75000, bonus_unit="bonus miles",
        )
        cash_row = extract.OfferScanRow(
            issuer="Test Bank", card_name="Test Rewards Card",
            offer_status="public", confidence=0.9, found=True,
            bonus_amount=200, bonus_unit="cash back",
        )

        self.assertEqual(schedule._row_to_extraction(points_row).current_offer_points, 75000)
        self.assertEqual(schedule._row_to_extraction(cash_row).current_offer_cash, 200)

    def test_referral_scan_row_commits_points_with_evidence_end_to_end(self):
        db = self._session()
        product = models.CardProduct(issuer="Chase", product_name="Chase Sapphire Preferred Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        snippet = "Earn 20,000 bonus points for each friend approved for the Sapphire Preferred card."
        row = extract.OfferScanRow(
            issuer="Chase",
            card_name="Chase Sapphire Preferred Card",
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            offer_status="unknown",
            confidence=0.9,
            found=True,
            referral_bonus_amount=20000,
            referral_bonus_unit="points",
            evidence_snippets={"referral_bonus_amount": [snippet]},
        )

        result = schedule._apply_scan_row(db, product, row)
        db.commit()

        self.assertIn("referral_bonus_points", result["committed"])
        self.assertEqual(product.referral_bonus_points, 20000)
        evidence = [
            e for e in db.scalars(select(models.IngestionEvidence)).all()
            if e.field == "referral_bonus_points"
        ]
        self.assertTrue(evidence)
        self.assertIn(snippet, evidence[0].evidence_snippets)
        # backend/logic/household.py reads referral_bonus_effective — the
        # scraped value must be visible through that contract.
        self.assertEqual(product.referral_bonus_effective, 20000)

    def test_referral_commit_requires_evidence_snippet(self):
        db = self._session()
        product = models.CardProduct(issuer="Chase", product_name="Chase Sapphire Preferred Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        ext = extract.OfferExtraction(
            found=True,
            confidence=0.9,
            referral_bonus_points=20000,
            offer_status="public",
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
        )
        result = validate.apply_extraction(db, product, ext, ext.source_url or "", commit=True)

        self.assertNotIn("referral_bonus_points", result["committed"])
        self.assertIsNone(product.referral_bonus_points)


class ClosedToNewGuardTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_closed_from_non_official_source_is_proposed_not_direct_written(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Citi",
            product_name="Citi Custom Cash Card",
            currency="cash back",
            current_offer_cash=200,
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer="Citi",
            card_name="Citi Custom Cash Card",
            source_url="https://www.doctorofcredit.com/citi-custom-cash-closed",
            offer_status="expired",
            confidence=0.9,
            found=True,
            eligibility_tags=["closed_to_new_applicants"],
            eligibility_language="Citi stopped accepting applications for the Custom Cash card.",
            evidence_snippets={
                "eligibility_language": ["Citi stopped accepting applications for the Custom Cash card."],
            },
        )

        result = schedule._apply_scan_row(db, product, row)
        db.commit()

        self.assertEqual(result["proposed"], ["eligibility_tags"])
        self.assertEqual(result["committed"], [])
        self.assertIsNone(product.eligibility_tags)
        self.assertEqual(product.current_offer_cash, 200)  # offers NOT nulled
        pending = db.scalars(
            select(models.ProposedChange).where(models.ProposedChange.status == "pending")
        ).all()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].field, "eligibility_tags")

        # The autonomous review then rejects it retryably: one non-official
        # source is not verification, and nothing waits for a human.
        report = validate.auto_resolve_pending_changes(db)
        self.assertEqual(report["rejected_count"], 1)
        self.assertEqual(pending[0].reason_code, "unverified_eligibility_auto_reject")
        db.refresh(product)
        self.assertIsNone(product.eligibility_tags)

    def test_closed_phrase_in_unrelated_benefit_copy_is_not_closed(self):
        db = self._session()
        product = models.CardProduct(issuer="Citi", product_name="Citi Custom Cash Card")
        db.add(product)
        db.commit()
        db.refresh(product)

        row = extract.OfferScanRow(
            issuer="Citi",
            card_name="Citi Custom Cash Card",
            source_url="https://www.citi.com/credit-cards/citi-custom-cash-credit-card",
            offer_status="expired",
            confidence=0.9,
            found=True,
            evidence_snippets={
                "card_benefits": [
                    "The $100 annual lounge credit is no longer available at select airports."
                ],
            },
        )

        self.assertFalse(schedule._is_closed_to_new_row(row, product))
        self.assertIsNone(schedule._apply_closed_to_new_row(db, product, row))

    def test_closed_phrase_near_application_context_is_detected(self):
        product = models.CardProduct(issuer="Citi", product_name="Citi Custom Cash Card")
        text = "As of May 2026 this card is no longer available to new applicants."
        self.assertTrue(schedule._closed_phrase_in_application_context(text, product))


if __name__ == "__main__":
    unittest.main()
