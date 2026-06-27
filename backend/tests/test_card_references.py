import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import card_references, models
from backend.db import Base
from backend.ingestion import schedule


class CardReferenceTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_seed_creates_reference_and_product_without_offer_data(self):
        db = self._session()

        result = card_references.seed_card_references(db)

        self.assertGreaterEqual(result["references_added"], 1)
        amex_gold = db.scalar(
            select(models.CardReference).where(
                models.CardReference.canonical_key == "american_express:amex_gold"
            )
        )
        self.assertIsNotNone(amex_gold)
        self.assertEqual(amex_gold.display_name, "Amex Gold")
        self.assertEqual(amex_gold.currency, "Amex Membership Rewards")
        self.assertEqual(
            amex_gold.issuer_url,
            "https://www.americanexpress.com/us/credit-cards/card/gold-card/",
        )

        product = db.scalar(
            select(models.CardProduct).where(
                models.CardProduct.product_name == "American Express Gold Card"
            )
        )
        self.assertIsNotNone(product)
        self.assertEqual(product.added_by, "reference_seed")
        self.assertIsNone(product.current_offer_points)
        self.assertIsNone(product.peak_offer_points)

        bilt = db.scalar(
            select(models.CardReference).where(
                models.CardReference.canonical_key == "bilt:bilt_blue"
            )
        )
        self.assertIsNotNone(bilt)
        self.assertEqual(bilt.display_name, "Bilt Blue")
        self.assertEqual(bilt.currency, "Bilt Rewards")
        self.assertIn("Bilt Mastercard", bilt.aliases)

        sapphire_business = db.scalar(
            select(models.CardReference).where(
                models.CardReference.canonical_key == "chase:chase_sapphire_business"
            )
        )
        self.assertIsNotNone(sapphire_business)
        self.assertEqual(sapphire_business.display_name, "Sapphire Reserve Business")
        self.assertEqual(sapphire_business.currency, "Chase Ultimate Rewards")
        self.assertEqual(sapphire_business.ownership, "Business")
        self.assertEqual(
            sapphire_business.issuer_url,
            "https://creditcards.chase.com/business-credit-cards/sapphire/reserve",
        )

        citi_custom = db.scalar(
            select(models.CardReference).where(
                models.CardReference.canonical_key == "citi:citi_custom_cash_card"
            )
        )
        self.assertIsNotNone(citi_custom)
        self.assertFalse(citi_custom.active)
        self.assertIsNone(
            db.scalar(
                select(models.CardProduct).where(
                    models.CardProduct.product_name == "Citi Custom Cash Card"
                )
            )
        )

    def test_learned_reference_url_is_used_as_product_source(self):
        db = self._session()
        card_references.seed_card_references(db)
        product = db.scalar(
            select(models.CardProduct).where(
                models.CardProduct.product_name == "Chase Sapphire Preferred Card"
            )
        )
        url = "https://example.test/sapphire-preferred-current-offer"

        promoted = card_references.promote_reference_url(db, product, url)
        db.commit()

        self.assertTrue(promoted)
        sources = schedule.product_source_urls(db, [product])
        self.assertIn(url, sources[product.id])
        self.assertIn(
            "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            sources[product.id],
        )


if __name__ == "__main__":
    unittest.main()
