"""Optional rendered-page fallback using Crawl4AI.

This module is PUBLIC-only. It renders already-known public URLs after static
HTTP extraction fails; it does not discover URLs, use credentials, bypass
CAPTCHA, or read private household state.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import html as html_lib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .. import config
from .fetch import FetchedPage


@dataclass(slots=True)
class RenderedFetchReport:
    pages: dict[str, FetchedPage] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    attempted_urls: int = 0
    rendered_pages: int = 0


def available() -> bool:
    try:
        _ensure_base_dir()
        import crawl4ai  # noqa: F401

        return True
    except Exception:
        return False


def render_many_pages(
    urls: list[str],
    *,
    timeout_ms: int | None = None,
    max_urls: int | None = None,
) -> RenderedFetchReport:
    """Render public URLs with Crawl4AI and return FetchedPage-compatible rows."""
    clean_urls = [
        url
        for url in dict.fromkeys(urls)
        if url and _is_public_renderable_url(url)
    ]
    if max_urls is not None and max_urls >= 0:
        clean_urls = clean_urls[:max_urls]
    report = RenderedFetchReport(attempted_urls=len(clean_urls))
    if not clean_urls:
        return report
    if not config.CRAWL4AI_ENABLED:
        report.warnings.append(
            {
                "code": "crawl4ai_disabled",
                "message": "Rendered fallback requested but CRAWL4AI_ENABLED is false.",
            }
        )
        return report
    try:
        rendered = _run_async(_render_many(clean_urls, timeout_ms=timeout_ms or config.CRAWL4AI_TIMEOUT_MS))
    except Exception as exc:
        report.errors.append({"product": "crawl4ai", "error": str(exc)})
        return report
    for url, page_or_error in rendered.items():
        if isinstance(page_or_error, FetchedPage):
            report.pages[url] = page_or_error
        else:
            report.errors.append({"url": url, "error": str(page_or_error)})
    report.rendered_pages = len(report.pages)
    return report


def _build_crawler_setup(timeout_ms: int):
    """Canonical DEMONFLOW Crawl4AI stealth setup (toolbench/crawl4ai/README.md):
    magic=True, enable_stealth=True, random user-agent, persistent context, and
    the patchright-backed UndetectedAdapter strategy. Falls back to the default
    Playwright adapter (stealth config kept) if the adapter is unavailable, so
    scraping degrades instead of breaking.

    Returns (browser_config, run_config, strategy_or_None, warning_or_None).
    """
    from crawl4ai import BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.content_filter_strategy import PruningContentFilter

    profile_dir = Path(config.CRAWL4AI_BASE_DIR) / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    browser_config = BrowserConfig(
        headless=config.CRAWL4AI_HEADLESS,
        verbose=False,
        enable_stealth=True,
        user_agent_mode="random",
        use_persistent_context=True,
        user_data_dir=str(profile_dir.resolve()),
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        page_timeout=timeout_ms,
        word_count_threshold=1,
        magic=True,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=0.45,
                threshold_type="fixed",
                min_word_threshold=0,
            )
        ),
    )
    strategy = None
    warning = None
    try:
        from crawl4ai import UndetectedAdapter
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy

        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=browser_config,
            browser_adapter=UndetectedAdapter(),
        )
    except Exception as exc:  # pragma: no cover - environment-dependent
        warning = f"undetected_adapter_unavailable: {exc}"
    return browser_config, run_config, strategy, warning


async def _render_many(urls: list[str], *, timeout_ms: int) -> dict[str, FetchedPage | str]:
    _ensure_base_dir()
    try:
        from crawl4ai import AsyncWebCrawler
    except Exception as exc:
        return {url: f"crawl4ai_import_failed: {exc}" for url in urls}

    try:
        browser_config, run_config, strategy, _warning = _build_crawler_setup(timeout_ms)
    except Exception as exc:
        return {url: f"crawl4ai_config_failed: {exc}" for url in urls}

    crawler_kwargs = {"config": browser_config}
    if strategy is not None:
        crawler_kwargs["crawler_strategy"] = strategy
    out: dict[str, FetchedPage | str] = {}
    async with AsyncWebCrawler(**crawler_kwargs) as crawler:
        for url in urls:
            try:
                result = await crawler.arun(url=url, config=run_config)
                page = _page_from_result(url, result)
                if page is None:
                    out[url] = getattr(result, "error_message", None) or "crawl4ai returned no page text"
                else:
                    out[url] = page
            except Exception as exc:
                out[url] = str(exc)
    return out


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict = {}
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - defensive thread bridge
            error.append(exc)

    import threading

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result.get("value")


def _ensure_base_dir() -> None:
    base = Path(config.CRAWL4AI_BASE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(base.resolve()))


def _is_public_renderable_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    low = url.lower()
    blocked_terms = (
        "login",
        "signin",
        "sign-in",
        "account",
        "authenticate",
        "captcha",
        "recaptcha",
        "prequal",
        "pre-qual",
        "application",
    )
    return not any(term in low for term in blocked_terms)


def _page_from_result(url: str, result) -> FetchedPage | None:
    if getattr(result, "success", True) is False:
        return None
    rendered_html = (
        getattr(result, "cleaned_html", None)
        or getattr(result, "html", None)
        or _markdown_html(_markdown_text(getattr(result, "markdown", None)))
    )
    if not rendered_html or len(_plain_text(rendered_html)) < 80:
        return None
    final_url = str(getattr(result, "url", None) or url)
    fetched_at = _iso(_utc_now())
    return FetchedPage(
        url=url,
        final_url=final_url,
        status_code=int(getattr(result, "status_code", None) or 200),
        fetched_at=fetched_at,
        etag=None,
        last_modified=None,
        content_hash=_content_hash(rendered_html),
        html=rendered_html,
        from_cache=False,
        cache_status="crawl4ai-rendered",
    )


def _markdown_text(value) -> str:
    if value is None:
        return ""
    for attr in ("fit_markdown", "raw_markdown", "markdown"):
        text = getattr(value, attr, None)
        if text:
            return str(text)
    return str(value)


def _markdown_html(markdown: str) -> str:
    escaped = html_lib.escape(markdown or "")
    escaped = re.sub(r"\n{2,}", "</p><p>", escaped)
    escaped = escaped.replace("\n", "<br>")
    return f"<html><body><main><p>{escaped}</p></main></body></html>"


def _plain_text(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").strip()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def _iso(ts: dt.datetime) -> str:
    return ts.isoformat() + "Z"


def _content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()
