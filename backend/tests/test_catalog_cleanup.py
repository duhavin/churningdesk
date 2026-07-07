import datetime as dt
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend import card_references, models
from backend.db import Base
from backend.logic import catalog, catalog_cleanup
from backend.product_identity import product_variant_key


class CatalogCleanupTests(unittest.TestCase):
    def test_upsert_reuses_existing_canonical_variant(self):
        db = self._session()
        existing = models.CardProduct(
            issuer="Delta",
            product_name="Delta SkyMiles Gold Card from American Express",
            currency="Delta SkyMiles",
            current_offer_points=70000,
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)

        product, created = catalog.upsert_product_by_identity(
            db,
            {
                "issuer": "American Express",
                "product_name": "Delta SkyMiles Gold American Express Card",
                "currency": "Delta SkyMiles",
                "added_by": "user_watchlist",
            },
        )
        db.commit()

        self.assertFalse(created)
        self.assertEqual(product.id, existing.id)
        self.assertEqual(db.scalar(select(func.count()).select_from(models.CardProduct)), 1)

    def test_merge_duplicate_products_preserves_facts_and_repoints_dependents(self):
        db = self._session()
        weak = models.CardProduct(
            issuer="American Express",
            product_name="Delta SkyMiles Gold American Express Card",
            currency="Delta SkyMiles",
            peak_offer_points=90000,
            card_benefits=["First checked bag free"],
        )
        rich = models.CardProduct(
            issuer="Delta",
            product_name="Delta SkyMiles Gold Card from American Express",
            currency="Delta SkyMiles",
            current_offer_points=70000,
            current_offer_min_spend=3000,
            source_url="https://example.test/delta-gold",
            last_verified=dt.datetime(2026, 6, 1),
        )
        db.add_all([weak, rich])
        db.commit()
        db.refresh(weak)
        db.refresh(rich)
        db.add_all(
            [
                models.HeldCard(
                    user="User A",
                    issuer=weak.issuer,
                    product_name=weak.product_name,
                    product_id=weak.id,
                    date_opened=dt.date.today(),
                    status="Active",
                ),
                models.SourceConfig(
                    name="Delta Gold",
                    url="https://example.test/delta-gold",
                    product_id=weak.id,
                    kind="offer",
                ),
                models.IngestionEvidence(
                    product_id=weak.id,
                    field="peak_offer_points",
                    value_json="90000",
                    source_url="https://example.test/history",
                    confidence=0.9,
                ),
                models.ProposedChange(
                    target_table="card_product",
                    target_id=weak.id,
                    field="annual_fee",
                    old_value="0",
                    new_value="150",
                    status="pending",
                ),
            ]
        )
        db.commit()

        result = catalog_cleanup.merge_duplicate_products(db)
        canonical = db.get(models.CardProduct, rich.id)

        self.assertEqual(result["groups_merged"], 1)
        self.assertEqual(result["duplicates_deleted"], 1)
        self.assertIsNotNone(canonical)
        self.assertEqual(canonical.peak_offer_points, 90000)
        self.assertEqual(canonical.card_benefits, ["First checked bag free"])
        self.assertEqual(db.get(models.CardProduct, weak.id), None)
        self.assertEqual(db.scalar(select(models.HeldCard)).product_id, rich.id)
        self.assertEqual(db.scalar(select(models.SourceConfig)).product_id, rich.id)
        self.assertEqual(db.scalar(select(models.IngestionEvidence)).product_id, rich.id)
        self.assertEqual(db.scalar(select(models.ProposedChange)).target_id, rich.id)
        variants = [
            product_variant_key(product.issuer, product.product_name)
            for product in db.scalars(select(models.CardProduct)).all()
        ]
        self.assertEqual(len(variants), len(set(variants)))

    def test_repair_unsafe_product_sources_replaces_with_reference_and_resets_verification(self):
        db = self._session()
        card_references.seed_card_references(db)
        product = db.scalar(
            select(models.CardProduct).where(
                models.CardProduct.product_name == "Capital One Venture X Rewards Credit Card"
            )
        )
        product.current_offer_points = 75000
        product.current_offer_min_spend = 4000
        product.current_offer_window_months = 3
        product.source_url = "https://thepointsguy.com/credit-cards/bilt-credit-cards-current-offers"
        product.last_verified = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        product.last_web_search_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        product.last_supplemental_search_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        db.commit()

        result = catalog_cleanup.repair_unsafe_product_sources(db)

        self.assertEqual(result["repaired_count"], 1)
        self.assertEqual(result["replaced_count"], 1)
        self.assertEqual(product.source_url, "https://www.capitalone.com/credit-cards/venture-x")
        self.assertIsNone(product.last_verified)
        self.assertIsNone(product.last_web_search_at)
        self.assertIsNone(product.last_supplemental_search_at)

    def test_repair_unsafe_product_sources_clears_when_no_reference_exists(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Example Bank",
            product_name="Example Preferred Card",
            currency="Example Points",
            source_url="https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()

        result = catalog_cleanup.repair_unsafe_product_sources(db)

        self.assertEqual(result["repaired_count"], 1)
        self.assertEqual(result["cleared_count"], 1)
        self.assertIsNone(product.source_url)
        self.assertIsNone(product.last_verified)

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_sanitize_catalog_text_repairs_junk_without_identity_shift(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express American Express® Platinum Card�",
            notes="Terms and limitations apply.� Premium Global Assist™ Hotline",
            card_benefits=[
                {"name": "$600 hotel credit", "value": "$600", "frequency": "ongoing",
                 "category": "hotel",
                 "description": "Navigate Membership with the award winning mobile app $600 Hotel Credit"},
                {"name": "SEATTLE, WA — Alaska Airlines' enhanced, combined loy...",
                 "frequency": "unknown"},
                {"name": "One credit will be processed per account every 4 year...",
                 "frequency": "unknown"},
                {"name": "Global Entry/TSA PreCheck credit", "value": "$120,",
                 "frequency": "unknown", "category": "travel"},
                {"name": "Global Entry/TSA PreCheck credit", "value": "$120",
                 "frequency": "ongoing", "category": "travel"},
            ],
            best_category_uses={
                "dining": "4x restaurants worldwide",
                "travel": "Venture Rewards Travel Card — Apply Today| Capital One Earn unlimited",
            },
            source_url="https://www.americanexpress.com/platinum",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        before_key = product_variant_key(product.issuer, product.product_name)

        report = catalog_cleanup.sanitize_catalog_text(db)
        db.refresh(product)

        self.assertEqual(report["changed_count"], 1)
        # Mojibake + trademark stripped; duplicated issuer prefix removed;
        # identity (variant key) preserved.
        self.assertEqual(product.product_name, "Platinum Card")
        self.assertEqual(
            product_variant_key(product.issuer, product.product_name), before_key
        )
        self.assertNotIn("�", product.notes)
        self.assertNotIn("™", product.notes)

        names = [b["name"] for b in product.card_benefits]
        self.assertIn("$600 hotel credit", names)
        self.assertEqual(names.count("Global Entry/TSA PreCheck credit"), 1)
        ge = next(b for b in product.card_benefits if b["name"].startswith("Global Entry"))
        self.assertEqual(ge["value"], "$120")
        self.assertEqual(ge["frequency"], "ongoing")
        for junk in ("SEATTLE", "One credit will"):
            self.assertFalse(any(junk in n for n in names), junk)
        hotel = next(b for b in product.card_benefits if b["name"] == "$600 hotel credit")
        self.assertNotIn("description", hotel)  # marketing description dropped

        self.assertEqual(
            product.best_category_uses, {"dining": "4x restaurants worldwide"}
        )


if __name__ == "__main__":
    unittest.main()
