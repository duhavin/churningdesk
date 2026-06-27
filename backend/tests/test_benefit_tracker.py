import datetime as dt
import unittest

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models, schemas
from backend.db import Base
from backend.logic import benefits, catalog as catalog_logic, pipeline


class BenefitTrackerTests(unittest.TestCase):
    def setUp(self):
        self._old_key = config.FERNET_KEY
        self._old_users = config.USERS
        config.FERNET_KEY = Fernet.generate_key().decode("utf-8")
        config.USERS = ["User A", "User B"]

    def tearDown(self):
        config.FERNET_KEY = self._old_key
        config.USERS = self._old_users

    def test_user_benefit_tracker_uses_verified_public_benefits_and_private_usage(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            card_benefits=[
                "$300 annual travel credit",
                "$10 monthly dining credit up to $120 per year",
            ],
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        card = models.HeldCard(
            user="User A",
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            product_id=product.id,
            date_opened=dt.date.today(),
            status="Active",
        )
        db.add(card)
        db.commit()
        db.refresh(card)

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        by_name = {row["benefit_name"]: row for row in tracker["benefits"]}

        self.assertEqual(by_name["$300 annual travel credit"]["amount_available"], 300)
        self.assertEqual(by_name["$300 annual travel credit"]["verified_status"], "verified")
        self.assertEqual(by_name["$10 monthly dining credit up to $120 per year"]["amount_available"], 10)
        self.assertEqual(by_name["$300 annual travel credit"]["amount_source"], "source")
        self.assertEqual(by_name["$300 annual travel credit"]["action_label"], "Use $300")
        self.assertIn(by_name["$300 annual travel credit"]["priority"], {"active", "attention"})
        self.assertGreaterEqual(tracker["summary"]["known_remaining_value"], 310)

        travel = by_name["$300 annual travel credit"]
        benefits.upsert_benefit_usage(
            db,
            "User A",
            schemas.BenefitUsageUpsert(
                held_card_id=card.id,
                benefit_key=travel["benefit_key"],
                benefit_name=travel["benefit_name"],
                period_key=travel["period_key"],
                period_start=dt.date.fromisoformat(travel["period_start"]),
                period_end=dt.date.fromisoformat(travel["period_end"]),
                amount_available=travel["amount_available"],
                amount_used=125,
                notes="Used on airfare.",
            ),
        )

        refreshed = benefits.build_user_benefit_tracker(db, "User A")
        travel_after = {row["benefit_name"]: row for row in refreshed["benefits"]}["$300 annual travel credit"]

        self.assertEqual(travel_after["status"], "partial")
        self.assertEqual(travel_after["amount_used"], 125)
        self.assertEqual(travel_after["amount_remaining"], 175)
        self.assertEqual(travel_after["notes"], "Used on airfare.")

    def test_manual_period_amount_fills_unparsed_benefit_amount(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Reserve",
            card_benefits=["Annual Instacart credit"],
            source_url="https://example.test/sapphire-reserve",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        card = models.HeldCard(
            user="User A",
            issuer="Chase",
            product_name="Sapphire Reserve",
            product_id=product.id,
            date_opened=dt.date.today(),
            status="Active",
        )
        db.add(card)
        db.commit()
        db.refresh(card)

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        row = tracker["benefits"][0]
        self.assertEqual(row["status"], "needs_amount")
        self.assertEqual(row["action_label"], "Add amount")

        benefits.upsert_benefit_usage(
            db,
            "User A",
            schemas.BenefitUsageUpsert(
                held_card_id=card.id,
                benefit_key=row["benefit_key"],
                benefit_name=row["benefit_name"],
                period_key=row["period_key"],
                period_start=dt.date.fromisoformat(row["period_start"]),
                period_end=dt.date.fromisoformat(row["period_end"]),
                amount_available=60,
                amount_used=25,
                notes="Manual amount from account benefit page.",
            ),
        )

        refreshed = benefits.build_user_benefit_tracker(db, "User A")
        after = refreshed["benefits"][0]
        self.assertEqual(after["amount_available"], 60)
        self.assertEqual(after["amount_source"], "manual_usage")
        self.assertEqual(after["status"], "partial")
        self.assertEqual(after["amount_remaining"], 35)
        self.assertEqual(after["tracking_kind"], "cash_credit")
        self.assertEqual(refreshed["summary"]["manual_amounts"], 1)

    def test_benefit_tracker_filters_scraped_noise_fragments(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            card_benefits=[
                '[json-ld] {"@context": "https://schema.org", "ratingCount": 10}',
                "[title] Capital One Venture X review [meta] A premium travel card.",
                "To access Capital One Lounges, eligible cardholders must present their physical card.",
                "The Venture Rewards doesn't include the $300 annual travel credit.",
                "$300 annual Capital One Travel credit",
                "10,000 anniversary bonus miles",
            ],
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Venture X Rewards Credit Card",
                product_id=product.id,
                date_opened=dt.date.today(),
                renewal_date=dt.date.today() + dt.timedelta(days=30),
                status="Active",
            )
        )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        names = [row["benefit_name"] for row in tracker["benefits"]]

        self.assertEqual(len(names), 2)
        self.assertIn("$300 annual Capital One Travel credit", names)
        self.assertIn("10,000 anniversary bonus miles", names)
        self.assertFalse(any("json-ld" in name.lower() for name in names))
        self.assertFalse(any("review" in name.lower() for name in names))

    def test_benefit_tracker_filters_editorial_disclosure_noise(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            card_benefits=[
                {
                    "name": "While we don\u2019t cover all available credit cards, our...",
                    "frequency": "unknown",
                    "description": "While we don\u2019t cover all available credit cards, our editorial team creates and maintains our analysis of cards.",
                    "evidence": "While we don\u2019t cover all available credit cards, our editorial team creates and maintains our analysis of cards.",
                },
                {
                    "name": "Editorial content is not influenced by nor subject to...",
                    "frequency": "unknown",
                    "description": "Editorial content is not influenced by nor subject to review by any credit card company, bank or partner.",
                    "evidence": "Editorial content is not influenced by nor subject to review by any credit card company, bank or partner.",
                },
                "Enrollment required. Earn up to $7 in monthly statement credits after you pay with the American Express Gold Card at U.S. Dunkin locations.",
            ],
            source_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer=product.issuer,
                product_name=product.product_name,
                product_id=product.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        labels = [row["benefit_label"] for row in tracker["benefits"]]
        text = " ".join(
            str(row.get(key) or "")
            for row in tracker["benefits"]
            for key in ("benefit_name", "description")
        ).lower()

        self.assertIn("Dunkin monthly credit", labels)
        self.assertNotIn("while we", text)
        self.assertNotIn("editorial content", text)

    def test_benefit_labels_are_specific_and_filter_payment_plan_noise(self):
        db = self._session()
        amex = models.CardProduct(
            issuer="American Express",
            product_name="Gold Card",
            card_benefits=[
                "That's up to $50 in statement credits from January through June and up to $50 in statement credits from July through December.",
                "Earn up to $7 in monthly statement credits after you pay with the American Express Gold Card at U.S. Dunkin locations.",
            ],
            source_url="https://example.test/amex-gold",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        chase = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred Card",
            card_benefits=[
                'At checkout: Chase Credit card members may have the option to create a payment plan at checkout on Amazon.com.',
                'Start a plan by selecting an eligible purchase with the "Pay Over Time" option next to the transaction amount.',
                "Airline Travel Partners Chase Sapphire Preferred Credit Card Earn more than ever, same low annual fee.",
            ],
            source_url="https://example.test/chase-sapphire",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        venture = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            card_benefits=[
                "$300 annual Capital One Travel credit",
                "Both cards come with a statement credit toward Global Entry or TSA PreCheck.",
                "10,000 anniversary bonus miles",
            ],
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add_all([amex, chase, venture])
        db.commit()
        for product in (amex, chase, venture):
            db.refresh(product)
            db.add(
                models.HeldCard(
                    user="User A",
                    issuer=product.issuer,
                    product_name=product.product_name,
                    product_id=product.id,
                    date_opened=dt.date.today(),
                    renewal_date=dt.date.today() + dt.timedelta(days=30),
                    status="Active",
                )
            )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        labels = {row["benefit_label"] for row in tracker["benefits"]}
        names = [row["benefit_name"] for row in tracker["benefits"]]

        self.assertIn("Resy semiannual credit", labels)
        self.assertIn("Dunkin monthly credit", labels)
        self.assertIn("Capital One Travel credit", labels)
        self.assertIn("Global Entry/TSA credit", labels)
        self.assertIn("10k anniversary miles", labels)
        self.assertFalse(any("Pay Over Time" in name or "payment plan" in name for name in names))
        self.assertFalse(any("Airline Travel Partners" in name for name in names))

    def test_duplicate_raw_benefit_snippets_collapse_by_label(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="Gold Card",
            card_benefits=[
                "Enrollment required. Earn up to $7 in monthly statement credits after you pay with the American Express Gold Card at U.S. Dunkin locations.",
                "Earn up to $7 in monthly statement credits after you pay with the American Express Gold Card at US Dunkin' locations.",
            ],
            source_url="https://example.test/amex-gold",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer=product.issuer,
                product_name=product.product_name,
                product_id=product.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        labels = [row["benefit_label"] for row in tracker["benefits"]]

        self.assertEqual(labels.count("Dunkin monthly credit"), 1)

    def test_amex_gold_official_source_normalizes_current_credit_benefits(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            card_benefits=[
                "That’s up to $50 in statement credits from January through June and up to $50 in statement credits from July through December.",
                "Enrollment required. Earn up to $7 in monthly statement credits after you pay with the American Express Gold Card at U.S. Dunkin locations.",
                "Get $10 in Uber Cash each month when you add your Gold Card to your Uber account.",
                "Terms Apply, json-ld, name American Express Gold Card raw page fragment",
            ],
            source_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="American Express",
                product_name="AMEX Gold",
                product_id=product.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        labels = [row["benefit_label"] for row in tracker["benefits"]]
        by_label = {row["benefit_label"]: row for row in tracker["benefits"]}

        self.assertIn("Dining credit", labels)
        self.assertIn("Uber Cash", labels)
        self.assertIn("Resy semiannual credit", labels)
        self.assertIn("Dunkin monthly credit", labels)
        self.assertEqual(labels.count("Resy semiannual credit"), 1)
        self.assertEqual(by_label["Dining credit"]["amount_available"], 10)
        self.assertEqual(by_label["Uber Cash"]["amount_available"], 10)
        self.assertEqual(by_label["Resy semiannual credit"]["amount_available"], 50)
        self.assertEqual(by_label["Dunkin monthly credit"]["amount_available"], 7)
        self.assertEqual(by_label["Resy semiannual credit"]["period_key"], f"{dt.date.today().year}-H{1 if dt.date.today().month <= 6 else 2}")
        self.assertNotIn(f"{dt.date.today().year}-H{2 if dt.date.today().month <= 6 else 1}", by_label["Resy semiannual credit"]["row_key"])
        self.assertFalse(any("json-ld" in row["benefit_name"].lower() for row in tracker["benefits"]))
        self.assertFalse(any(row["benefit_label"] == "Lounge access" for row in tracker["benefits"]))
        self.assertFalse(any(row["benefit_label"] == "Card benefit" for row in tracker["benefits"]))

    def test_sapphire_preferred_official_source_normalizes_noisy_benefits(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            card_benefits=[
                "Airline Travel Partners Chase Sapphire Preferred Credit Card Earn more than ever, same low annual fee.",
                "At checkout: Chase credit card members may have the option to create a payment plan.",
            ],
            source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Chase",
                product_name="Sapphire Preferred",
                product_id=product.id,
                date_opened=dt.date.today(),
                renewal_date=dt.date.today() + dt.timedelta(days=60),
                status="Active",
            )
        )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        by_label = {row["benefit_label"]: row for row in tracker["benefits"]}
        labels = set(by_label)

        self.assertIn("Hotel credit", labels)
        self.assertIn("Global Entry/TSA credit", labels)
        self.assertIn("DoorDash promo", labels)
        self.assertEqual(by_label["Hotel credit"]["amount_available"], 100)
        self.assertEqual(by_label["Global Entry/TSA credit"]["amount_available"], 120)
        self.assertFalse(any(row["benefit_label"] == "Travel credit" for row in tracker["benefits"]))
        self.assertFalse(tracker["missing"])

    def test_venture_x_trusted_source_normalizes_travel_and_access_benefits(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            card_benefits=[
                "Cardmembers receive a $300 annual statement credit for bookings made through Capital One Travel.",
                "Cardholders also receive 10,000 bonus miles every account anniversary.",
                "With the Venture X, you'll receive a statement credit of up to $120 for your TSA PreCheck or Global Entry application fee.",
                "The primary cardholder also receives a Priority Pass membership that unlocks access to 1,300-plus lounges worldwide.",
                "It provides lounge access, flexible miles and solid all-around benefits.",
            ],
            source_url="https://thepointsguy.com/credit-cards/reviews/capital-one-venture-x-card-review/",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="Capital One",
                product_name="Venture X",
                product_id=product.id,
                date_opened=dt.date.today(),
                renewal_date=dt.date.today() + dt.timedelta(days=30),
                status="Active",
            )
        )
        db.commit()

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        by_label = {row["benefit_label"]: row for row in tracker["benefits"]}
        labels = [row["benefit_label"] for row in tracker["benefits"]]

        self.assertIn("Capital One Travel credit", labels)
        self.assertIn("10k anniversary miles", labels)
        self.assertIn("Global Entry/TSA credit", labels)
        self.assertIn("Priority Pass", labels)
        self.assertEqual(labels.count("Priority Pass"), 1)
        self.assertNotIn("Lounge access", labels)
        self.assertEqual(by_label["Capital One Travel credit"]["amount_available"], 300)
        self.assertEqual(by_label["Global Entry/TSA credit"]["amount_available"], 120)
        self.assertEqual(by_label["Global Entry/TSA credit"]["action_label"], "Use $120")
        self.assertEqual(by_label["Priority Pass"]["action_label"], "Confirm")

        catalog_benefits = catalog_logic.product_to_dict(product)["card_benefits"]
        catalog_labels = [item["name"] for item in catalog_benefits]
        self.assertIn("$300 Capital One Travel credit", catalog_labels)
        self.assertIn("Global Entry/TSA credit", catalog_labels)
        self.assertNotIn("It provides lounge access, flexible miles and solid all-around benefits.", catalog_labels)

    def test_access_benefit_tracks_binary_confirmation(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            card_benefits=["Priority Pass Select lounge membership enrollment required"],
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        card = models.HeldCard(
            user="User A",
            issuer=product.issuer,
            product_name=product.product_name,
            product_id=product.id,
            date_opened=dt.date.today(),
            status="Active",
        )
        db.add(card)
        db.commit()
        db.refresh(card)

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        row = tracker["benefits"][0]

        self.assertEqual(row["tracking_kind"], "enrollment")
        self.assertEqual(row["amount_source"], "not_applicable")
        self.assertEqual(row["status"], "unconfirmed")
        self.assertEqual(row["action_label"], "Confirm")

        benefits.upsert_benefit_usage(
            db,
            "User A",
            schemas.BenefitUsageUpsert(
                held_card_id=card.id,
                benefit_key=row["benefit_key"],
                benefit_name=row["benefit_name"],
                period_key=row["period_key"],
                period_start=dt.date.fromisoformat(row["period_start"]),
                period_end=dt.date.fromisoformat(row["period_end"]),
                amount_available=None,
                amount_used=1,
                notes="Enrollment completed.",
            ),
        )

        refreshed = benefits.build_user_benefit_tracker(db, "User A")
        after = refreshed["benefits"][0]

        self.assertEqual(after["status"], "confirmed")
        self.assertEqual(after["action_label"], "Confirmed")
        self.assertEqual(after["priority"], "done")

    def test_recurring_benefit_timeframe_and_global_suppression(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="Gold Card",
            card_benefits=[
                "$10 monthly dining credit",
                "$100 Resy Credit: up to $50 January through June and up to $50 July through December.",
            ],
            source_url="https://example.test/amex-gold",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        card = models.HeldCard(
            user="User A",
            issuer="American Express",
            product_name="Gold Card",
            product_id=product.id,
            date_opened=dt.date.today(),
            status="Active",
        )
        db.add(card)
        db.commit()
        db.refresh(card)

        tracker = benefits.build_user_benefit_tracker(db, "User A")
        by_label = {row["benefit_label"]: row for row in tracker["benefits"]}
        monthly = by_label["Dining credit"]
        semiannual = by_label["Resy semiannual credit"]

        self.assertEqual(monthly["cadence"], "monthly")
        self.assertIn("Current month:", monthly["timeframe_note"])
        self.assertEqual(semiannual["cadence"], "semiannual")
        self.assertIn("Current", semiannual["timeframe_note"])
        self.assertIn(monthly["priority"], {"attention", "active"})

        benefits.upsert_benefit_usage(
            db,
            "User A",
            schemas.BenefitUsageUpsert(
                held_card_id=card.id,
                benefit_key=monthly["benefit_key"],
                benefit_name=monthly["benefit_name"],
                period_key=monthly["period_key"],
                suppressed=True,
                suppress_all=True,
                notes="Not worth tracking.",
            ),
        )

        refreshed = benefits.build_user_benefit_tracker(db, "User A")
        paused = {row["benefit_label"]: row for row in refreshed["benefits"]}["Dining credit"]
        alerts = benefits.build_benefit_attention(db, "User A", window_days=45)

        self.assertEqual(paused["status"], "suppressed")
        self.assertEqual(paused["priority"], "paused")
        self.assertEqual(paused["action_label"], "Paused")
        self.assertEqual(paused["suppression_scope"], "all")
        self.assertEqual(refreshed["summary"]["suppressed"], 1)
        self.assertFalse(any(alert.get("benefit_label") == "Dining credit" for alert in alerts))

    def test_benefit_attention_expiring_credit_and_anniversary_bonus(self):
        db = self._session()
        today = dt.date.today()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            card_benefits=[
                "$10 monthly Uber Cash",
                "10,000 anniversary bonus miles",
            ],
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        card = models.HeldCard(
            user="User A",
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            product_id=product.id,
            date_opened=today - dt.timedelta(days=365),
            renewal_date=today + dt.timedelta(days=20),
            status="Active",
        )
        db.add(card)
        db.commit()

        alerts = benefits.build_benefit_attention(db, "User A", window_days=45)
        actions = [alert.get("action", "") for alert in alerts]
        details = [alert.get("detail", "") for alert in alerts]

        self.assertTrue(any("Use $10 Uber Cash" in action for action in actions))
        self.assertTrue(any("expires" in detail for detail in details))
        self.assertTrue(any(action == "Anniversary bonus incoming" for action in actions))
        self.assertTrue(any("10,000 miles" in detail for detail in details))

    def test_benefit_attention_summarizes_long_source_text_for_dashboard(self):
        db = self._session()
        today = dt.date.today()
        long_name = (
            "$10 monthly Uber Cash for U.S. purchases with extra marketing text "
            "about enrollment terms conditions comparison tables and other source copy "
            "that should not be dumped into the dashboard notification"
        )
        product = models.CardProduct(
            issuer="American Express",
            product_name="Gold Card",
            card_benefits=[long_name],
            source_url="https://example.test/amex-gold",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer="American Express",
                product_name="Gold Card",
                product_id=product.id,
                date_opened=today,
                status="Active",
            )
        )
        db.commit()

        alerts = benefits.build_benefit_attention(db, "User A", window_days=45)
        expiring = next(alert for alert in alerts if alert["type"] == "benefit_expiring")

        self.assertEqual(expiring["benefit_label"], "Uber Cash")
        self.assertIn("Use $10 Uber Cash", expiring["action"])
        self.assertLessEqual(len(expiring["action"]), 40)
        self.assertNotIn("comparison tables", expiring["action"])
        self.assertNotIn("comparison tables", expiring["detail"])

    def test_household_ledger_groups_private_usage_by_card_and_benefit(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="Gold Card",
            card_benefits=["$10 monthly Uber Cash for U.S. purchases with extra marketing text"],
            source_url="https://example.test/amex-gold",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        cards = {}
        for user in ("User A", "User B"):
            card = models.HeldCard(
                user=user,
                issuer="American Express",
                product_name="Gold Card",
                product_id=product.id,
                date_opened=dt.date.today(),
                status="Active",
            )
            db.add(card)
            cards[user] = card
        db.commit()
        for card in cards.values():
            db.refresh(card)

        for user, used in (("User A", 10), ("User B", 5)):
            row = benefits.build_user_benefit_tracker(db, user)["benefits"][0]
            benefits.upsert_benefit_usage(
                db,
                user,
                schemas.BenefitUsageUpsert(
                    held_card_id=cards[user].id,
                    benefit_key=row["benefit_key"],
                    benefit_name=row["benefit_name"],
                    period_key=row["period_key"],
                    period_start=dt.date.fromisoformat(row["period_start"]),
                    period_end=dt.date.fromisoformat(row["period_end"]),
                    amount_available=row["amount_available"],
                    amount_used=used,
                    notes="monthly check",
                ),
            )

        ledger = benefits.build_benefits_ledger(db)
        group = ledger["tracker_groups"][0]
        users = {row["user"]: row for row in group["users"]}

        self.assertEqual(group["benefit_label"], "Uber Cash")
        self.assertEqual(group["amount_available"], 20)
        self.assertEqual(group["amount_used"], 15)
        self.assertEqual(group["amount_remaining"], 5)
        self.assertEqual(group["action_label"], "Use $5")
        self.assertEqual(users["User A"]["status"], "used")
        self.assertEqual(users["User B"]["status"], "partial")
        self.assertEqual(ledger["summary"]["known_remaining_value"], 5)

    def test_household_tracker_only_lists_users_who_hold_the_card(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Venture X Rewards Credit Card",
            card_benefits=["$300 annual Capital One Travel credit"],
            source_url="https://example.test/venture-x",
            last_verified=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                issuer=product.issuer,
                product_name=product.product_name,
                product_id=product.id,
                date_opened=dt.date.today(),
                status="Active",
            )
        )
        db.commit()

        ledger = benefits.build_benefits_ledger(db)
        group = ledger["tracker_groups"][0]
        users = [row["user"] for row in group["users"]]

        self.assertEqual(users, ["User A"])

    def test_household_overlap_is_not_close_cancel_reason(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Sapphire Preferred Card",
            card_benefits=["$50 annual hotel credit"],
            earn_multipliers={"dining": 3},
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        for user in ("User A", "User B"):
            db.add(
                models.HeldCard(
                    user=user,
                    issuer="Chase",
                    product_name="Sapphire Preferred Card",
                    product_id=product.id,
                    date_opened=dt.date.today(),
                    status="Active",
                )
            )
        db.commit()

        result = pipeline.build_pipeline(db, "User A")
        action = next(item for item in result["held_actions"] if item["product_name"] == "Sapphire Preferred Card")

        self.assertEqual(action["action"], "keep")
        self.assertIn("User B", action["household_overlap_users"])
        self.assertIn("not a close/cancel reason", action["reason"])

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
