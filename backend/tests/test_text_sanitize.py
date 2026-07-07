"""Text sanitation + hardened benefit gates, tested against REAL junk rows
found in the local PUBLIC catalog during the 2026-07-06 audit (values are
paraphrased public scrape artifacts — no private data)."""
from __future__ import annotations

import unittest

from backend.benefit_normalization import normalize_public_benefits
from backend.text_sanitize import (
    clean_benefit_value,
    clean_text,
    looks_like_scrape_junk,
)


class CleanTextTests(unittest.TestCase):
    def test_strips_replacement_and_trademark_chars(self):
        self.assertEqual(clean_text("Platinum Card�"), "Platinum Card")
        self.assertEqual(
            clean_text("Atmos® Rewards Ascent Visa Signature® credit card"),
            "Atmos Rewards Ascent Visa Signature credit card",
        )

    def test_repairs_cp1252_mojibake(self):
        self.assertEqual(clean_text("donâ€™t"), "don't")
        self.assertEqual(clean_text("A â€“ B"), "A – B")

    def test_collapses_whitespace_and_orphan_punctuation(self):
        self.assertEqual(clean_text("Premium  Global Assist ™  Hotline ,"), "Premium Global Assist Hotline,")

    def test_empty_and_none(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text("   "), "")


class CleanBenefitValueTests(unittest.TestCase):
    def test_trailing_comma_money(self):
        self.assertEqual(clean_benefit_value("$120,"), "$120")

    def test_plain_money_and_points(self):
        self.assertEqual(clean_benefit_value("$300"), "$300")
        self.assertEqual(clean_benefit_value("10,000 miles"), "10,000 miles")

    def test_junk_value_becomes_none(self):
        self.assertIsNone(clean_benefit_value("see terms"))
        self.assertIsNone(clean_benefit_value(""))


class ScrapeJunkTests(unittest.TestCase):
    def test_press_dateline(self):
        self.assertTrue(looks_like_scrape_junk(
            "SEATTLE, WA — Alaska Airlines' enhanced, combined loyalty program"
        ))

    def test_glued_navigation(self):
        self.assertTrue(looks_like_scrape_junk(
            "Credit Intel - Financial Education CenterBusiness Credit CardsView All Business Credit Cards"
        ))

    def test_marketing_pitch(self):
        self.assertTrue(looks_like_scrape_junk(
            "Venture Rewards Travel Card — Apply Today| Capital One Earn unlimited miles"
        ))
        self.assertTrue(looks_like_scrape_junk(
            "The Points Guy believes that credit cards can transform lives"
        ))

    def test_disclosure_fragments(self):
        self.assertTrue(looks_like_scrape_junk(
            "One credit will be processed per account every 4 years"
        ))
        self.assertTrue(looks_like_scrape_junk(
            "The credit will be posted to your account within 2 billing cycles"
        ))

    def test_real_benefits_pass(self):
        for good in (
            "$300 Capital One Travel credit",
            "Global Entry/TSA PreCheck credit",
            "$10 monthly DoorDash promo",
            "Priority Pass membership",
            "10k anniversary miles",
        ):
            self.assertFalse(looks_like_scrape_junk(good), good)


class HardenedBenefitGateTests(unittest.TestCase):
    """Feed the gate the exact junk shapes found in the audited catalog."""

    def _normalize(self, items):
        return normalize_public_benefits(
            "Capital One", "Capital One Venture Business", "https://www.capitalone.com/x", items
        ) or []

    def test_press_release_and_nav_rows_dropped(self):
        out = self._normalize([
            {"name": "SEATTLE, WA — Alaska Airlines' enhanced, combined loy...",
             "frequency": "unknown", "description": "press body"},
            {"name": "travel credit", "frequency": "unknown",
             "description": "Credit Intel - Financial Education CenterBusiness Credit CardsView All"},
            {"name": "With Atmos Rewards, our popular, long-time credit car...",
             "frequency": "unknown"},
        ])
        self.assertEqual(out, [])

    def test_disclosure_fragment_rows_dropped(self):
        out = self._normalize([
            {"name": "One credit will be processed per account every 4 year...",
             "frequency": "unknown"},
            {"name": "The credit will be posted to your account within 2 bi...",
             "frequency": "unknown"},
            {"name": "The actual amount of miles you earn will depend on yo",
             "frequency": "unknown"},
        ])
        self.assertEqual(out, [])

    def test_marketing_welded_name_dropped(self):
        out = self._normalize([
            {"name": "$240 Business credit: Get $20 credit per month on: Fe",
             "value": "$240", "frequency": "monthly"},
        ])
        self.assertEqual(out, [])

    def test_good_benefit_survives_junk_description(self):
        out = self._normalize([
            {"name": "$50 travel credit", "value": "$50", "frequency": "annual",
             "category": "travel",
             "description": "Venture Business | 2X Travel Rewards Business Card | Capital One Earn unlimited"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "$50 travel credit")
        self.assertNotIn("description", out[0])

    def test_trailing_comma_value_normalized_and_duplicates_collapse(self):
        out = self._normalize([
            {"name": "Global Entry/TSA PreCheck credit", "value": "$120,",
             "frequency": "unknown", "category": "travel"},
            {"name": "Global Entry/TSA PreCheck credit", "value": "$120",
             "frequency": "ongoing", "category": "travel"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["value"], "$120")
        self.assertEqual(out[0]["frequency"], "ongoing")

    def test_generic_name_without_specifics_dropped(self):
        out = self._normalize([
            {"name": "travel credit", "frequency": "unknown"},
        ])
        self.assertEqual(out, [])

    def test_specific_generic_name_with_value_survives(self):
        out = self._normalize([
            {"name": "travel credit", "value": "$50", "frequency": "annual", "category": "travel"},
        ])
        self.assertEqual(len(out), 1)

    def test_mojibake_in_name_cleaned(self):
        out = self._normalize([
            {"name": "$300 Capital One Travel® credit�", "value": "$300",
             "frequency": "annual", "category": "travel"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "$300 Capital One Travel credit")


if __name__ == "__main__":
    unittest.main()
