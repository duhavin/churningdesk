import datetime as dt
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend import models
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

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
