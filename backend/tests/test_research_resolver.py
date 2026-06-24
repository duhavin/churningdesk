import os
import tempfile
import unittest
import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config, models
from backend.db import Base
from backend.ingestion import fetch, research_resolver, schedule


def _page(url: str, title: str, body: str) -> fetch.FetchedPage:
    html = f"""
    <html>
      <head>
        <title>{title}</title>
        <meta name="description" content="{title} welcome offer benefits" />
      </head>
      <body>
        <h1>{title}</h1>
        {body}
      </body>
    </html>
    """
    return fetch.FetchedPage(
        url=url,
        final_url=url,
        status_code=200,
        fetched_at="2026-06-19T00:00:00Z",
        etag=None,
        last_modified=None,
        content_hash=f"hash-{abs(hash(url))}",
        html=html,
        from_cache=False,
        cache_status="miss",
    )


def _priority(product: models.CardProduct) -> dict:
    return {"priority_product_ids": [product.id]}


class ResearchResolverTests(unittest.TestCase):
    def setUp(self):
        self._old_key = config.ANTHROPIC_API_KEY
        self._old_web = config.WEB_SEARCH_ENABLED
        config.ANTHROPIC_API_KEY = ""
        config.WEB_SEARCH_ENABLED = True

    def tearDown(self):
        config.ANTHROPIC_API_KEY = self._old_key
        config.WEB_SEARCH_ENABLED = self._old_web

    def test_research_plan_builds_four_focused_queries(self):
        product = models.CardProduct(
            id=1,
            issuer="American Express",
            product_name="American Express Gold Card",
        )

        plan = research_resolver.build_research_plan(product)
        by_type = {query.query_type: query.query for query in plan}

        self.assertEqual(set(by_type), set(research_resolver.QUERY_TYPES))
        self.assertIn("American Express Amex Gold welcome offer bonus spend annual fee", by_type["current_offer"])
        self.assertIn("site:americanexpress.com Amex Gold welcome offer benefits", by_type["issuer_page"])
        self.assertIn("Amex Gold highest ever offer peak bonus", by_type["peak_history"])
        self.assertIn("Amex Gold benefits credits earn rates terms", by_type["benefits"])

    def test_run_searches_caches_q_labeled_sections_per_query(self):
        queries = [
            research_resolver.ResearchQuery(product_id=1, query_type="current_offer", query="q current"),
            research_resolver.ResearchQuery(product_id=1, query_type="benefits", query="q benefits"),
        ]

        def fake_search(uncached, max_uses):
            return {
                "text": "[Q1]: Current offer is 75,000 points.\n[Q2]: Benefits include a $50 hotel credit.",
                "sources": [{"url": "https://example.test/card", "title": "source"}],
                "web_search_requests": 1,
            }

        with tempfile.TemporaryDirectory() as tmp:
            cache = research_resolver.SearchCache(root=Path(tmp))
            with patch.object(research_resolver, "_run_uncached_search", side_effect=fake_search):
                results, stats, errors, warnings = research_resolver.run_searches(queries, cache=cache)
            cached_results, cached_stats, cached_errors, cached_warnings = research_resolver.run_searches(queries, cache=cache)

        self.assertFalse(errors)
        self.assertFalse(warnings)
        self.assertFalse(cached_errors)
        self.assertFalse(cached_warnings)
        self.assertEqual(stats["query_sections_parsed"], 2)
        self.assertIn("75,000", results["q current"].text)
        self.assertNotIn("hotel credit", results["q current"].text)
        self.assertIn("hotel credit", results["q benefits"].text)
        self.assertEqual(cached_stats["cached_search_queries"], 2)
        self.assertTrue(cached_results["q current"].from_cache)

    def test_resolver_fetches_cited_pages_and_applies_verified_rows(self):
        db = self._session()
        products = [
            models.CardProduct(issuer="American Express", product_name="Delta SkyMiles Gold American Express Card"),
            models.CardProduct(issuer="Chase", product_name="Marriott Bonvoy Boundless Credit Card"),
            models.CardProduct(issuer="Capital One", product_name="Capital One Venture Business"),
            models.CardProduct(issuer="Chase", product_name="Chase Sapphire Preferred Card"),
            models.CardProduct(issuer="American Express", product_name="American Express Gold Card"),
        ]
        db.add_all(products)
        db.commit()
        for product in products:
            db.refresh(product)

        pages = {
            "https://www.americanexpress.com/us/credit-cards/card/delta-skymiles-gold-american-express-card/": _page(
                "https://www.americanexpress.com/us/credit-cards/card/delta-skymiles-gold-american-express-card/",
                "Delta SkyMiles Gold American Express Card",
                """
                <p>Welcome offer: Earn 70,000 bonus miles after spending $3,000 in the first 6 months.</p>
                <p>The highest ever public offer was 90,000 miles.</p>
                <p>$0 introductory annual fee, then $150 annual fee.</p>
                <p>Earn 2x miles at restaurants and 2x miles on Delta purchases.</p>
                <p>Benefits include first checked bag free, priority boarding, and a $200 Delta flight credit.</p>
                """,
            ),
            "https://creditcards.chase.com/travel-credit-cards/marriott-bonvoy/boundless": _page(
                "https://creditcards.chase.com/travel-credit-cards/marriott-bonvoy/boundless",
                "Marriott Bonvoy Boundless Credit Card",
                """
                <p>Welcome offer: Earn 125,000 bonus points after you spend $5,000 in 3 months.</p>
                <p>The highest ever public offer was 150,000 points.</p>
                <p>$95 annual fee.</p>
                <p>Earn 6x points at Marriott hotels and 3x points on gas, groceries, and dining.</p>
                <p>Benefits include one free night award every cardmember anniversary.</p>
                """,
            ),
            "https://www.capitalone.com/small-business/credit-cards/venture-business/": _page(
                "https://www.capitalone.com/small-business/credit-cards/venture-business/",
                "Capital One Venture Business",
                """
                <p>Welcome offer: Earn 150,000 bonus miles after spending $30,000 in the first 3 months.</p>
                <p>The highest ever public offer was 300,000 miles.</p>
                <p>$395 annual fee.</p>
                <p>Earn 2x miles on every purchase and 5x miles on travel booked through Capital One Travel.</p>
                <p>Benefits include airport lounge access and statement credits for Global Entry or TSA PreCheck.</p>
                """,
            ),
            "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred": _page(
                "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
                "Chase Sapphire Preferred Card",
                """
                <p>Welcome offer: Earn 75,000 bonus points after you spend $5,000 in the first 3 months.</p>
                <p>The highest ever public offer was 100,000 points.</p>
                <p>$95 annual fee.</p>
                <p>Earn 3x points on dining, 2x points on travel, and 1x points on everyday purchases.</p>
                <p>Benefits include a $50 annual hotel credit and trip cancellation insurance.</p>
                """,
            ),
            "https://www.americanexpress.com/us/credit-cards/card/gold-card/": _page(
                "https://www.americanexpress.com/us/credit-cards/card/gold-card/",
                "American Express Gold Card",
                """
                <p>Welcome offer: Earn 60,000 bonus points after spending $6,000 in the first 6 months.</p>
                <p>The highest ever public offer was 100,000 points.</p>
                <p>$325 annual fee.</p>
                <p>Earn 4x points at restaurants and 4x points at U.S. supermarkets.</p>
                <p>Benefits include monthly Uber Cash, monthly dining credit, and semiannual Resy credits.</p>
                """,
            ),
        }

        def fake_search(queries, max_uses):
            return {
                "text": "Public cited research for seeded cards.",
                "sources": [{"url": url, "title": "source", "cited_text": ""} for url in pages],
                "web_search_requests": 1,
            }

        def fake_fetch_many(urls, **kwargs):
            return {url: pages[url] for url in urls if url in pages}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHURN_SEARCH_CACHE_DIR": tmp}):
            with patch.object(research_resolver, "_run_uncached_search", side_effect=fake_search) as search_mock:
                with patch.object(research_resolver.fetch, "fetch_many_pages", side_effect=fake_fetch_many):
                    resolution = research_resolver.resolve_products(products, llm_fallback=False)

        self.assertFalse(resolution.errors)
        self.assertEqual(len(search_mock.call_args.args[0]), len(products) * 4)
        self.assertEqual(resolution.stats["cards_queued"], 5)
        self.assertEqual(resolution.stats["urls_fetched"], 5)
        self.assertGreaterEqual(resolution.stats["cards_resolved"], 5)

        rows_by_id = {item.product_id: item.row for item in resolution.product_results}
        for product in products:
            applied = schedule._apply_scan_row(db, product, rows_by_id[product.id])
            self.assertTrue(applied["committed"] or applied["proposed"], product.product_name)
        db.commit()

        by_name = {p.product_name: p for p in products}
        self.assertEqual(by_name["Delta SkyMiles Gold American Express Card"].currency, "Delta SkyMiles")
        self.assertEqual(by_name["Marriott Bonvoy Boundless Credit Card"].currency, "Marriott Bonvoy")
        self.assertEqual(by_name["Capital One Venture Business"].currency, "Capital One Miles")
        self.assertEqual(by_name["Chase Sapphire Preferred Card"].currency, "Chase Ultimate Rewards")
        self.assertEqual(by_name["American Express Gold Card"].currency, "Amex Membership Rewards")
        self.assertEqual(by_name["American Express Gold Card"].current_offer_points, 60000)
        self.assertEqual(by_name["American Express Gold Card"].peak_offer_points, 100000)
        self.assertTrue(by_name["American Express Gold Card"].card_benefits)
        self.assertTrue(by_name["American Express Gold Card"].earn_multipliers)

    def test_compact_llm_snippets_do_not_send_full_page(self):
        product = models.CardProduct(id=1, issuer="Test Bank", product_name="Test Rewards Card")
        url = "https://example.test/test-rewards"
        page = _page(
            url,
            "Test Rewards Card",
            "<p>Test Rewards Card benefits include a $100 annual travel credit.</p>"
            f"<p>{'x' * 5000}</p>",
        )

        def fake_search(queries, max_uses):
            return {
                "text": "Public cited research.",
                "sources": [{"url": url, "title": "source", "cited_text": ""}],
                "web_search_requests": 1,
            }

        captured = {}

        def fake_llm(inputs):
            captured["inputs"] = inputs
            return []

        old_key = config.ANTHROPIC_API_KEY
        config.ANTHROPIC_API_KEY = "test-key"
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHURN_SEARCH_CACHE_DIR": tmp}):
                with patch.object(research_resolver, "_run_uncached_search", side_effect=fake_search):
                    with patch.object(research_resolver.fetch, "fetch_many_pages", return_value={url: page}):
                        with patch.object(research_resolver.extract, "extract_offers_batch", side_effect=fake_llm):
                            research_resolver.resolve_products([product], llm_fallback=True)
        finally:
            config.ANTHROPIC_API_KEY = old_key

        snippets = captured["inputs"][0].snippets
        self.assertTrue(snippets)
        self.assertTrue(all(len(snippet) < 900 for snippet in snippets))
        self.assertFalse(any("x" * 1000 in snippet for snippet in snippets))

    def test_cited_research_text_feeds_llm_when_fetched_page_is_thin(self):
        product = models.CardProduct(id=1, issuer="American Express", product_name="American Express Gold Card")
        url = "https://www.americanexpress.com/us/credit-cards/card/gold-card/"
        page = _page(url, "American Express Gold Card", "<p>Account page shell.</p>")

        def fake_search(queries, max_uses):
            return {
                "text": (
                    "Amex Gold welcome offer and benefits. Amex Gold earns 4x points at restaurants "
                    "and 4x points at U.S. supermarkets. Benefits include monthly Uber Cash, monthly "
                    "dining credit, and semiannual Resy credits."
                ),
                "sources": [{"url": url, "title": "American Express Gold Card", "cited_text": ""}],
                "web_search_requests": 1,
            }

        captured = {}

        def fake_llm(inputs):
            captured["inputs"] = inputs
            return [
                research_resolver.extract.OfferScanRow(
                    issuer="American Express",
                    card_name="American Express Gold Card",
                    source_url=url,
                    offer_status="public",
                    confidence=0.88,
                    found=True,
                    earn_multipliers={"dining": 4, "groceries": 4},
                    best_category_uses={"dining": "4x", "groceries": "4x"},
                    card_benefits=["monthly Uber Cash", "monthly dining credit", "semiannual Resy credits"],
                    evidence_snippets={
                        "earn_multipliers": ["Amex Gold earns 4x points at restaurants and 4x points at U.S. supermarkets."],
                        "card_benefits": ["Benefits include monthly Uber Cash, monthly dining credit, and semiannual Resy credits."],
                    },
                )
            ]

        old_key = config.ANTHROPIC_API_KEY
        config.ANTHROPIC_API_KEY = "test-key"
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHURN_SEARCH_CACHE_DIR": tmp}):
                with patch.object(research_resolver, "_run_uncached_search", side_effect=fake_search):
                    with patch.object(research_resolver.fetch, "fetch_many_pages", return_value={url: page}):
                        with patch.object(research_resolver.extract, "extract_offers_batch", side_effect=fake_llm):
                            resolution = research_resolver.resolve_products([product], llm_fallback=True)
        finally:
            config.ANTHROPIC_API_KEY = old_key

        snippets = captured["inputs"][0].snippets
        self.assertTrue(any("web_research_text" in snippet for snippet in snippets))
        row = resolution.product_results[0].row
        self.assertEqual(row.source_url, url)
        self.assertEqual(row.fetched_at, "2026-06-19T00:00:00Z")
        self.assertEqual(row.content_hash, f"hash-{abs(hash(url))}")
        self.assertEqual(row.earn_multipliers["dining"], 4)
        self.assertTrue(row.card_benefits)

    def test_broad_roundup_does_not_commit_supplemental_fields(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = research_resolver.extract.OfferScanRow(
            issuer="Capital One",
            card_name="Capital One Venture X Rewards Credit Card",
            source_url="https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
            product_url="https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
            offer_status="public",
            confidence=0.9,
            found=True,
            bonus_amount=75000,
            bonus_unit="miles",
            card_benefits=["roundup boilerplate benefit text"],
            earn_multipliers={"travel": 5},
            best_category_uses={"travel": "5x"},
            evidence_snippets={
                "bonus_amount": ["Earn 75,000 miles."],
                "card_benefits": ["roundup boilerplate benefit text"],
                "earn_multipliers": ["5x travel"],
            },
        )

        schedule._apply_scan_row(db, product, row)
        db.commit()

        self.assertEqual(product.current_offer_points, 75000)
        self.assertIsNone(product.card_benefits)
        self.assertIsNone(product.earn_multipliers)
        self.assertIsNone(product.best_category_uses)

    def test_close_variant_business_source_does_not_update_personal_card(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        row = research_resolver.extract.OfferScanRow(
            issuer="Capital One",
            card_name="Capital One Venture X Rewards Credit Card",
            source_url="https://www.capitalone.com/small-business/credit-cards/venture-x-business/",
            product_url="https://www.capitalone.com/small-business/credit-cards/venture-x-business/",
            offer_status="public",
            confidence=0.9,
            found=True,
            bonus_amount=100000,
            bonus_unit="miles",
            card_benefits=["Venture X Business $300 travel credit"],
            earn_multipliers={"travel": 5},
            best_category_uses={"travel": "5x"},
            evidence_snippets={
                "bonus_amount": ["Earn 100,000 miles."],
                "card_benefits": ["Venture X Business $300 travel credit"],
                "earn_multipliers": ["5x travel"],
            },
        )

        applied = schedule._apply_scan_row(db, product, row)
        db.commit()

        self.assertEqual(applied["note"], "source_variant_conflict")
        self.assertFalse(applied["committed"])
        self.assertFalse(applied["proposed"])
        self.assertIsNone(product.current_offer_points)
        self.assertIsNone(product.card_benefits)
        self.assertIsNone(product.earn_multipliers)

    def test_verified_benefits_merge_instead_of_replacing_existing_list(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
            card_benefits=["Capital One Lounge access"],
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        pending_values = [
            "$300 annual Capital One Travel credit",
            "10,000 anniversary bonus miles",
        ]
        pending = models.ProposedChange(
            target_table="card_product",
            target_id=product.id,
            field="card_benefits",
            old_value=json.dumps(product.card_benefits),
            new_value=json.dumps(pending_values),
            source_url="https://thepointsguy.com/credit-cards/reviews/capital-one-venture-x-card-review",
            confidence=0.9,
            status="pending",
        )
        db.add(pending)
        db.commit()

        source = "https://thepointsguy.com/credit-cards/reviews/capital-one-venture-x-card-review"
        row = research_resolver.extract.OfferScanRow(
            issuer="Capital One",
            card_name="Capital One Venture X Rewards Credit Card",
            source_url=source,
            product_url=source,
            offer_status="unknown",
            confidence=0.9,
            source_priority=1,
            found=True,
            card_benefits=[
                "Capital One Lounge access",
                "$300 annual Capital One Travel credit",
                "10,000 anniversary bonus miles",
            ],
            evidence_snippets={
                "card_benefits": [
                    "Benefits include a $300 annual travel credit and 10,000 anniversary bonus miles."
                ],
            },
        )

        applied = schedule._apply_scan_row(db, product, row)
        db.commit()
        db.refresh(pending)

        self.assertIn("card_benefits", applied["committed"])
        self.assertEqual(pending.status, "approved")
        benefit_names = [item.get("name") for item in product.card_benefits]
        self.assertIn("Priority Pass", benefit_names)
        self.assertIn("$300 Capital One Travel credit", benefit_names)
        self.assertIn("10k anniversary miles", benefit_names)

    def test_verified_category_updates_replace_stale_multipliers(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            earn_multipliers={"dining": 5.0, "travel": 5.0},
            best_category_uses={"dining": "5x", "travel": "5x"},
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        source = "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred"
        row = research_resolver.extract.OfferScanRow(
            issuer="Chase",
            card_name="Chase Sapphire Preferred Card",
            source_url=source,
            product_url=source,
            offer_status="unknown",
            confidence=0.9,
            source_priority=1,
            found=True,
            earn_multipliers={"dining": 3.0, "travel": 5.0},
            best_category_uses={"dining": "3x", "travel": "5x"},
            evidence_snippets={
                "earn_multipliers": ["5x travel through Chase and 3x dining."],
                "best_category_uses": ["5x travel through Chase and 3x dining."],
            },
        )

        applied = schedule._apply_scan_row(db, product, row)
        db.commit()

        self.assertIn("earn_multipliers", applied["committed"])
        self.assertIn("best_category_uses", applied["committed"])
        self.assertEqual(product.earn_multipliers["dining"], 3.0)
        self.assertEqual(product.best_category_uses["dining"], "3x")

    def test_held_benefit_gap_bypasses_pending_review_and_cooldown(self):
        db = self._session()
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            current_offer_points=75000,
            peak_offer_points=100000,
            last_verified=now,
            last_web_search_at=now,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        db.add(
            models.ProposedChange(
                target_table="card_product",
                target_id=product.id,
                field="current_offer_points",
                old_value="75000",
                new_value="80000",
                source_url="https://example.test/source",
                confidence=0.9,
                status="pending",
            )
        )
        db.commit()

        source = "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred"
        row = research_resolver.extract.OfferScanRow(
            issuer="Chase",
            card_name="Chase Sapphire Preferred Card",
            source_url=source,
            product_url=source,
            offer_status="unknown",
            confidence=0.88,
            found=True,
            earn_multipliers={"dining": 3, "travel": 2},
            best_category_uses={"dining": "3x", "travel": "2x"},
            card_benefits=["$50 annual hotel credit", "trip cancellation insurance"],
            evidence_snippets={
                "earn_multipliers": ["Earn 3x on dining and 2x on travel."],
                "card_benefits": ["Benefits include a $50 annual hotel credit."],
                "best_category_uses": ["Earn 3x on dining and 2x on travel."],
            },
        )

        def fake_resolve(products, **kwargs):
            self.assertEqual([p.id for p in products], [product.id])
            return research_resolver.ResearchResolution(
                product_results=[
                    research_resolver.ProductResolution(
                        product_id=product.id,
                        row=row,
                        engine="test",
                        source_urls=[source],
                    )
                ],
                stats={
                    "search_queries": 4,
                    "cached_search_queries": 0,
                    "urls_from_search": 1,
                    "urls_fetched": 1,
                    "detail_urls_discovered": 0,
                    "detail_urls_fetched": 0,
                    "cached_urls_reused": 0,
                    "adapter_rows": 1,
                    "llm_batches": 0,
                    "llm_snippets_sent": 0,
                    "research_text_snippets": 0,
                    "cards_resolved": 1,
                    "cards_needs_data": 0,
                    "web_search_requests": 1,
                },
            )

        with patch.object(schedule.fetch, "fetch_many_pages", return_value={}):
            with patch.object(schedule.research_resolver, "resolve_products", side_effect=fake_resolve):
                result = schedule.run_refresh(
                    db,
                    product_ids=[product.id],
                    limit=None,
                    use_web_search=True,
                    llm_fallback=False,
                    include_incomplete=True,
                    refresh_valuations=False,
                    **_priority(product),
                )

        db.refresh(product)
        self.assertEqual(result["products_checked"], 1)
        self.assertIn("card_benefits", result["results"][0]["result"]["committed"])
        self.assertIn("earn_multipliers", result["results"][0]["result"]["committed"])
        self.assertEqual(product.earn_multipliers["dining"], 3.0)
        self.assertTrue(product.card_benefits)

    def test_static_held_supplemental_facts_commit_without_bonus_or_llm(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            source_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        db.commit()
        page = _page(
            product.source_url,
            "American Express Gold Card",
            """
            <p>Earn 4x points at restaurants and 4x points at U.S. supermarkets.</p>
            <p>Benefits include monthly Uber Cash, monthly dining credit, and semiannual Resy credits.</p>
            """,
        )

        with patch.object(schedule.fetch, "fetch_many_pages", return_value={product.source_url: page}):
            result = schedule.run_refresh(
                db,
                product_ids=[product.id],
                limit=None,
                use_web_search=False,
                llm_fallback=False,
                include_incomplete=True,
                force=True,
                refresh_valuations=False,
                **_priority(product),
            )

        db.refresh(product)
        committed = result["results"][0]["result"]["committed"]
        self.assertIn("card_benefits", committed)
        self.assertIn("earn_multipliers", committed)
        self.assertEqual(product.earn_multipliers["dining"], 4.0)
        self.assertTrue(product.card_benefits)

    def test_rendered_fallback_applies_after_static_page_misses(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Test Bank",
            product_name="Rendered Rewards Card",
            source_url="https://example.test/rendered-rewards",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        db.commit()
        static_page = _page(product.source_url, "Rendered Rewards Card", "<p>Apply online.</p>")
        rendered_page = _page(
            product.source_url,
            "Rendered Rewards Card",
            """
            <p>Earn 3x points on dining and 2x points on travel.</p>
            <p>Benefits include a $100 annual travel credit.</p>
            """,
        )
        report = schedule.rendered_fetch.RenderedFetchReport(
            pages={product.source_url: rendered_page},
            attempted_urls=1,
            rendered_pages=1,
        )
        old_enabled = config.CRAWL4AI_ENABLED
        config.CRAWL4AI_ENABLED = True
        try:
            with patch.object(schedule.fetch, "fetch_many_pages", return_value={product.source_url: static_page}):
                with patch.object(schedule.rendered_fetch, "render_many_pages", return_value=report) as rendered:
                    result = schedule.run_refresh(
                        db,
                        product_ids=[product.id],
                        limit=None,
                        use_rendered_fallback=True,
                        use_web_search=False,
                        llm_fallback=False,
                        include_incomplete=True,
                        force=True,
                        refresh_valuations=False,
                        **_priority(product),
                    )
        finally:
            config.CRAWL4AI_ENABLED = old_enabled

        db.refresh(product)
        rendered.assert_called_once()
        self.assertEqual(result["scan"]["rendered_pages"], 1)
        self.assertTrue(result["scan"]["rendered_fallback_used"])
        self.assertEqual(result["results"][0]["engine"], "crawl4ai")
        self.assertIn("earn_multipliers", result["results"][0]["result"]["committed"])
        self.assertIn("card_benefits", result["results"][0]["result"]["committed"])
        self.assertEqual(product.earn_multipliers["dining"], 3.0)
        self.assertTrue(product.card_benefits)

    def test_rendered_fallback_is_not_used_unless_requested(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Test Bank",
            product_name="Static Miss Card",
            source_url="https://example.test/static-miss",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        page = _page(product.source_url, "Static Miss Card", "<p>Apply online.</p>")

        with patch.object(schedule.fetch, "fetch_many_pages", return_value={product.source_url: page}):
            with patch.object(schedule.rendered_fetch, "render_many_pages", side_effect=AssertionError("should not render")):
                result = schedule.run_refresh(
                    db,
                    product_ids=[product.id],
                    limit=None,
                    use_rendered_fallback=False,
                    use_web_search=False,
                    llm_fallback=False,
                    include_incomplete=True,
                    force=True,
                    refresh_valuations=False,
                    **_priority(product),
                )

        self.assertEqual(result["scan"]["rendered_pages"], 0)
        self.assertFalse(result["scan"]["rendered_fallback_used"])

    def test_schema_limit_fallback_applies_safe_static_supplemental_facts(self):
        db = self._session()
        product = models.CardProduct(
            issuer="American Express",
            product_name="American Express Gold Card",
            source_url="https://www.americanexpress.com/us/credit-cards/card/gold-card/",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        db.commit()
        page = _page(
            product.source_url,
            "American Express Gold Card",
            """
            <p>Earn 4x points at restaurants and 4x points at U.S. supermarkets.</p>
            <p>Benefits include monthly Uber Cash, monthly dining credit, and semiannual Resy credits.</p>
            """,
        )

        old_key = config.ANTHROPIC_API_KEY
        config.ANTHROPIC_API_KEY = "test-key"
        try:
            with patch.object(schedule.fetch, "fetch_many_pages", return_value={product.source_url: page}):
                with patch.object(
                    schedule.extract,
                    "extract_offers_batch",
                    side_effect=schedule.extract.IngestionUnavailable("compiled grammar is too large"),
                ):
                    result = schedule.run_refresh(
                        db,
                        product_ids=[product.id],
                        limit=None,
                        use_web_search=False,
                        llm_fallback=True,
                        include_incomplete=True,
                        force=True,
                        refresh_valuations=False,
                        **_priority(product),
                    )
        finally:
            config.ANTHROPIC_API_KEY = old_key

        db.refresh(product)
        committed = result["results"][0]["result"]["committed"]
        self.assertIn("llm_schema_limit", {warning.get("code") for warning in result["warnings"]})
        self.assertIn("earn_multipliers", committed)
        self.assertIn("card_benefits", committed)
        self.assertEqual(result["scan"]["cards_resolved"], 1)
        self.assertEqual(result["scan"]["web_search_requests"], 0)
        self.assertEqual(product.earn_multipliers["dining"], 4.0)
        self.assertTrue(product.card_benefits)

    def test_product_specific_source_url_is_treated_as_product_page(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        url = "https://www.capitalone.com/credit-cards/venture-x/"
        db.add(
            models.SourceConfig(
                name="Venture X",
                url=url,
                product_id=product.id,
                kind="offer",
                active=True,
                priority=1,
            )
        )
        db.commit()
        page = _page(
            url,
            "Venture X Rewards | Capital One",
            """
            <p>Earn 10 miles per dollar on hotels and rental cars booked through Capital One Travel.</p>
            <p>Earn 5 miles per dollar on flights booked through Capital One Travel.</p>
            <p>Benefits include a $300 annual travel credit, 10,000 anniversary bonus miles, lounge access,
            and a Global Entry or TSA PreCheck credit.</p>
            """,
        )

        with patch.object(schedule.fetch, "fetch_many_pages", return_value={url: page}):
            result = schedule.run_refresh(
                db,
                product_ids=[product.id],
                limit=None,
                use_web_search=False,
                llm_fallback=False,
                include_incomplete=True,
                force=True,
                refresh_valuations=False,
                **_priority(product),
            )

        db.refresh(product)
        committed = result["results"][0]["result"]["committed"]
        self.assertIn("card_benefits", committed)
        self.assertIn("earn_multipliers", committed)
        self.assertEqual(product.earn_multipliers["travel"], 10.0)
        self.assertTrue(product.card_benefits)

    def test_static_supplemental_rows_use_llm_structuring_when_available(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Test Bank",
            product_name="Useful Rewards Card",
            source_url="https://example.test/useful-rewards",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        db.commit()
        page = _page(
            product.source_url,
            "Useful Rewards Card",
            """
            <p>Earn 3x points on dining and 2x points on travel.</p>
            <p>Benefits include a $100 annual travel credit.</p>
            """,
        )

        captured = {}

        def fake_llm(inputs):
            captured["inputs"] = inputs
            return [
                research_resolver.extract.OfferScanRow(
                    issuer="Test Bank",
                    card_name="Useful Rewards Card",
                    source_url=product.source_url,
                    offer_status="unknown",
                    confidence=0.9,
                    found=True,
                    earn_multipliers={"dining": 3, "travel": 2},
                    best_category_uses={"dining": "3x", "travel": "2x"},
                    card_benefits=[
                        {
                            "name": "$100 travel credit",
                            "value": "$100",
                            "frequency": "annual",
                            "category": "travel",
                            "evidence": "Benefits include a $100 annual travel credit.",
                        }
                    ],
                    evidence_snippets={
                        "earn_multipliers": ["Earn 3x points on dining and 2x points on travel."],
                        "card_benefits": ["Benefits include a $100 annual travel credit."],
                    },
                )
            ]

        old_key = config.ANTHROPIC_API_KEY
        config.ANTHROPIC_API_KEY = "test-key"
        try:
            with patch.object(schedule.fetch, "fetch_many_pages", return_value={product.source_url: page}):
                with patch.object(schedule.extract, "extract_offers_batch", side_effect=fake_llm):
                    result = schedule.run_refresh(
                        db,
                        product_ids=[product.id],
                        limit=None,
                        use_web_search=False,
                        llm_fallback=True,
                        include_incomplete=True,
                        force=True,
                        refresh_valuations=False,
                        **_priority(product),
                    )
        finally:
            config.ANTHROPIC_API_KEY = old_key

        db.refresh(product)
        self.assertTrue(captured["inputs"])
        self.assertEqual(result["scan"]["llm_batches"], 1)
        self.assertIn("card_benefits", result["results"][0]["result"]["committed"])
        self.assertIsInstance(product.card_benefits[0], dict)
        self.assertEqual(product.card_benefits[0]["name"], "$100 travel credit")
        self.assertEqual(product.card_benefits[0]["frequency"], "annual")

    def test_static_best_row_prefers_safe_product_source_for_supplemental_facts(self):
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
        )
        official_url = "https://www.capitalone.com/credit-cards/venture-x/"
        roundup_url = "https://thepointsguy.com/credit-cards/best/"
        pages = {
            official_url: schedule.static_parse.parse_page(
                _page(
                    official_url,
                    "Venture X Rewards | Capital One",
                    """
                    <p>Benefits include a $300 annual travel credit, 10,000 anniversary bonus miles,
                    Capital One Lounge access, Priority Pass lounge access, and Global Entry or TSA PreCheck credit.</p>
                    <p>Referral program terms may apply.</p>
                    """,
                )
            ),
            roundup_url: schedule.static_parse.parse_page(
                _page(
                    roundup_url,
                    "Best Credit Cards",
                    """
                    <p>Capital One Venture X Rewards Credit Card earns 2 miles per dollar on every purchase.
                    Benefits include the $300 Capital One Travel credit and 10,000 anniversary miles.</p>
                    """,
                )
            ),
        }

        best, _ = schedule._scan_product_static(
            product,
            pages,
            source_urls=[roundup_url],
            product_source_urls=[official_url],
        )

        self.assertIsNotNone(best)
        self.assertEqual(best.source_url, official_url)
        self.assertNotEqual(best.offer_status, "affiliate")
        self.assertTrue(schedule._can_apply(best))

    def test_static_best_row_prefers_safe_supplemental_when_offer_exists(self):
        product = models.CardProduct(
            issuer="Chase",
            product_name="Chase Sapphire Preferred Card",
            current_offer_points=75000,
            peak_offer_points=100000,
        )
        issuer_url = "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred"
        roundup_url = "https://thepointsguy.com/credit-cards/best/"
        pages = {
            issuer_url: schedule.static_parse.parse_page(
                _page(
                    issuer_url,
                    "Chase Sapphire Preferred Credit Card",
                    """
                    <p>Enjoy 5x points on travel purchased through Chase, 3x points on dining,
                    and 2x points on other travel.</p>
                    <p>Benefits include trip cancellation insurance and a Chase Travel hotel credit.</p>
                    """,
                )
            ),
            roundup_url: schedule.static_parse.parse_page(
                _page(
                    roundup_url,
                    "Best Credit Cards",
                    """
                    <p>Chase Sapphire Preferred Card public offer: earn 75,000 bonus points
                    after spending $5,000 in 3 months.</p>
                    """,
                )
            ),
        }

        best, _ = schedule._scan_product_static(
            product,
            pages,
            source_urls=[roundup_url],
            product_source_urls=[issuer_url],
        )

        self.assertIsNotNone(best)
        self.assertEqual(best.source_url, issuer_url)
        self.assertEqual(best.earn_multipliers["dining"], 3.0)

    def test_static_refresh_follows_roundup_review_link_for_held_benefit_gap(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        db.add(
            models.HeldCard(
                user="User A",
                product_id=product.id,
                issuer=product.issuer,
                product_name=product.product_name,
                date_opened=dt.date(2025, 1, 1),
                status="Active",
            )
        )
        db.commit()
        roundup_url = "https://thepointsguy.com/credit-cards/best/"
        detail_url = "https://thepointsguy.com/credit-cards/reviews/capital-one-venture-x-card-review"
        pages = {
            roundup_url: _page(
                roundup_url,
                "Best Credit Cards",
                f"""
                <p>Capital One Venture X Rewards Credit Card is a premium travel card.</p>
                <a href="{detail_url}">Read our full review</a>
                """,
            ),
            detail_url: _page(
                detail_url,
                "Capital One Venture X Rewards Credit Card Review",
                """
                <p>Earn 10x miles on hotels and rental cars booked through Capital One Travel.</p>
                <p>Earn 5x miles on flights booked through Capital One Travel.</p>
                <p>Earn 2x miles on every purchase.</p>
                <p>Benefits include a $300 annual travel credit, 10,000 anniversary bonus miles,
                airport lounge access, and Global Entry or TSA PreCheck credit.</p>
                """,
            ),
        }
        calls: list[list[str]] = []

        def fake_fetch_many(urls, **kwargs):
            calls.append(list(urls))
            return {url: pages[url] for url in urls if url in pages}

        with patch.object(schedule.fetch, "fetch_many_pages", side_effect=fake_fetch_many):
            result = schedule.run_refresh(
                db,
                product_ids=[product.id],
                source_urls=[roundup_url],
                limit=None,
                use_web_search=False,
                llm_fallback=False,
                include_incomplete=True,
                force=True,
                refresh_valuations=False,
                **_priority(product),
            )

        db.refresh(product)
        fetched = [url for call in calls for url in call]
        self.assertIn(detail_url.rstrip("/"), [url.rstrip("/") for url in fetched])
        self.assertEqual(result["scan"]["research_resolver"]["detail_urls_discovered"], 1)
        self.assertIn("earn_multipliers", result["results"][0]["result"]["committed"])
        self.assertIn("card_benefits", result["results"][0]["result"]["committed"])
        self.assertEqual(product.earn_multipliers["travel"], 10.0)
        self.assertEqual(product.earn_multipliers["everyday"], 2.0)

    def test_doctorofcredit_roundup_follows_matching_card_review_link(self):
        db = self._session()
        product = models.CardProduct(
            issuer="Capital One",
            product_name="Capital One Venture X Rewards Credit Card",
        )
        db.add(product)
        db.commit()
        db.refresh(product)

        roundup_url = "https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/"
        detail_url = "https://www.doctorofcredit.com/capital-one-venture-x-rewards-card-review"
        pages = {
            roundup_url: _page(
                roundup_url,
                "Best Current Credit Card Sign Up Bonuses",
                f"""
                <p>Capital One Venture X has a current offer listed here.</p>
                <a href="{detail_url}">Capital One Venture X Rewards Card Review</a>
                """,
            ),
            detail_url: _page(
                detail_url,
                "Capital One Venture X Rewards Card Review",
                """
                <p>Welcome offer: Earn 75,000 bonus miles after spending $4,000 in 3 months.</p>
                <p>Earn 10x miles on hotels and rental cars booked through Capital One Travel.</p>
                <p>Earn 5x miles on flights booked through Capital One Travel.</p>
                <p>Earn 2x miles on every purchase.</p>
                <p>Benefits include a $300 annual travel credit, 10,000 anniversary bonus miles,
                airport lounge access, and Global Entry or TSA PreCheck credit.</p>
                """,
            ),
        }

        def fake_search(queries, max_uses):
            return {
                "text": "Capital One Venture X public research.",
                "sources": [{"url": roundup_url, "title": "DoC bonuses", "cited_text": ""}],
                "web_search_requests": 1,
            }

        calls = []

        def fake_fetch_many(urls, **kwargs):
            calls.append(list(urls))
            return {url: pages[url] for url in urls if url in pages}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHURN_SEARCH_CACHE_DIR": tmp}):
            with patch.object(research_resolver, "_run_uncached_search", side_effect=fake_search):
                with patch.object(research_resolver.fetch, "fetch_many_pages", side_effect=fake_fetch_many):
                    resolution = research_resolver.resolve_products([product], llm_fallback=False)

        fetched = [url for call in calls for url in call]
        self.assertIn(roundup_url, fetched)
        self.assertIn(detail_url, fetched)
        self.assertEqual(resolution.stats["detail_urls_discovered"], 1)
        self.assertEqual(resolution.stats["detail_urls_fetched"], 1)

        row = resolution.product_results[0].row
        self.assertEqual(row.source_url, detail_url)
        self.assertTrue(row.card_benefits)
        self.assertTrue(row.earn_multipliers)
        applied = schedule._apply_scan_row(db, product, row)
        db.commit()
        self.assertIn("card_benefits", applied["committed"])
        self.assertIn("earn_multipliers", applied["committed"])
        self.assertIn("$300 annual travel credit", " ".join(product.card_benefits))

    def test_doctorofcredit_read_our_review_link_uses_row_context(self):
        product = models.CardProduct(
            id=1,
            issuer="American Express",
            product_name="The Business Platinum Card from American Express",
            ownership="Business",
        )
        roundup_url = "https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/"
        detail_url = (
            "https://www.doctorofcredit.com/"
            "american-express-refreshes-platinum-business-platinum-cards-895-fee-175k-200k-bonus-many-new-credits"
        )
        pages = {
            roundup_url: _page(
                roundup_url,
                "Best Current Credit Card Sign Up Bonuses",
                f"""
                <table>
                  <tr>
                    <td>American Express Business Platinum card has new credits and a higher fee.</td>
                    <td><a href="{detail_url}">Read our review</a></td>
                  </tr>
                </table>
                """,
            ),
            detail_url: _page(
                detail_url,
                "American Express refreshes Platinum and Business Platinum cards",
                """
                <p>The Business Platinum Card from American Express includes many new credits.</p>
                <p>Earn 5x points on flights and prepaid hotels booked through Amex Travel.</p>
                <p>Benefits include airline fee credits, statement credits, lounge access, and CLEAR credit.</p>
                """,
            ),
        }

        def fake_search(queries, max_uses):
            return {
                "text": "Business Platinum public research.",
                "sources": [{"url": roundup_url, "title": "DoC bonuses", "cited_text": ""}],
                "web_search_requests": 1,
            }

        calls = []

        def fake_fetch_many(urls, **kwargs):
            calls.append(list(urls))
            return {url: pages[url] for url in urls if url in pages}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHURN_SEARCH_CACHE_DIR": tmp}):
            with patch.object(research_resolver, "_run_uncached_search", side_effect=fake_search):
                with patch.object(research_resolver.fetch, "fetch_many_pages", side_effect=fake_fetch_many):
                    resolution = research_resolver.resolve_products([product], llm_fallback=False)

        fetched = [url for call in calls for url in call]
        self.assertIn(detail_url, fetched)
        self.assertEqual(resolution.stats["detail_urls_discovered"], 1)
        row = resolution.product_results[0].row
        self.assertEqual(row.source_url, detail_url)
        self.assertTrue(row.card_benefits)

    def test_failed_search_records_error_without_aborting(self):
        products = [
            models.CardProduct(id=1, issuer="Chase", product_name="Chase Sapphire Preferred Card"),
            models.CardProduct(id=2, issuer="American Express", product_name="American Express Gold Card"),
        ]

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CHURN_SEARCH_CACHE_DIR": tmp}):
            with patch.object(research_resolver, "_run_uncached_search", side_effect=RuntimeError("search down")):
                resolution = research_resolver.resolve_products(products, llm_fallback=False)

        self.assertEqual(len(resolution.product_results), 2)
        self.assertTrue(resolution.errors)
        self.assertEqual(resolution.stats["cards_needs_data"], 2)
        self.assertTrue(all(result.row and not result.row.found for result in resolution.product_results))

    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()


if __name__ == "__main__":
    unittest.main()
