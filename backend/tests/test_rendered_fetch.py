"""Pin the canonical DEMONFLOW Crawl4AI stealth configuration.

toolbench/crawl4ai/README.md is the workspace-canonical pattern: magic=True,
enable_stealth=True, random user-agent, persistent context, and the
patchright-backed UndetectedAdapter crawler strategy. A regression here means
the rendered fallback quietly lost its anti-bot posture (2026-07-16 owner
check: "magic + stealth + ultra hidden").
"""
from __future__ import annotations

import unittest
from pathlib import Path

from .. import config
from ..ingestion import rendered_fetch


class CanonicalStealthConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import crawl4ai  # noqa: F401
        except Exception:  # pragma: no cover - env without crawl4ai
            self.skipTest("crawl4ai not installed in this environment")

    def test_build_crawler_setup_matches_canonical_pattern(self) -> None:
        browser_config, run_config, strategy, warning = rendered_fetch._build_crawler_setup(45000)

        self.assertTrue(run_config.magic, "CrawlerRunConfig.magic must be True")
        self.assertTrue(browser_config.enable_stealth, "enable_stealth must be True")
        self.assertEqual(browser_config.user_agent_mode, "random")
        self.assertTrue(browser_config.use_persistent_context)
        self.assertEqual(browser_config.headless, config.CRAWL4AI_HEADLESS)

        profile = Path(browser_config.user_data_dir)
        base = Path(config.CRAWL4AI_BASE_DIR).resolve()
        self.assertEqual(profile, (base / "profile").resolve())
        self.assertTrue(profile.is_dir(), "persistent profile dir must exist")

    def test_undetected_adapter_strategy_selected(self) -> None:
        _browser, _run, strategy, warning = rendered_fetch._build_crawler_setup(45000)
        self.assertIsNone(warning, f"adapter fallback warning: {warning}")
        self.assertIsNotNone(strategy, "UndetectedAdapter strategy must be built")
        self.assertEqual(type(strategy.adapter).__name__, "UndetectedAdapter")


if __name__ == "__main__":
    unittest.main()
