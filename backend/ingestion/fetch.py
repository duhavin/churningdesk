"""Concurrent cached HTTP fetch for PUBLIC pages.

The ingestion engine uses static HTTP only. It does not open a browser, does not
attempt login/CAPTCHA/private pages, and stores cache artifacts under data/.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_CHARS = 8000
DEFAULT_CACHE_TTL_HOURS = 24
DEFAULT_PER_DOMAIN_LIMIT = 2
DEFAULT_MAX_CONCURRENCY = 8


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    status_code: int
    fetched_at: str
    etag: str | None
    last_modified: str | None
    content_hash: str
    html: str
    from_cache: bool = False
    cache_status: str = "miss"


def _utc_now() -> dt.datetime:
    # Naive UTC (utcnow() is deprecated on 3.12+); kept naive for ISO formatting.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def _iso(ts: dt.datetime) -> str:
    return ts.isoformat() + "Z"


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def _content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_dir() -> Path:
    root = Path(os.getenv("CHURN_HTTP_CACHE_DIR", "data/http_cache"))
    root.mkdir(parents=True, exist_ok=True)
    (root / "pages").mkdir(parents=True, exist_ok=True)
    return root


class PageCache:
    def __init__(self) -> None:
        self.root = _cache_dir()
        self.index_path = self.root / "index.json"
        self._lock = threading.Lock()
        self._index = self._load_index()
        # When True, put()/touch() mark the index dirty instead of rewriting the
        # whole file each call; the caller flushes once at the end of a batch.
        self._defer_saves = False
        self._dirty = False

    def _load_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_index(self) -> None:
        if self._defer_saves:
            self._dirty = True
            return
        self._write_index()

    def _write_index(self) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def flush(self) -> None:
        """Persist the index once if any deferred writes are pending."""
        with self._lock:
            if self._dirty:
                self._write_index()
                self._dirty = False

    def get(self, url: str) -> FetchedPage | None:
        with self._lock:
            meta = self._index.get(_cache_key(url))
        if not meta:
            return None
        page_path = self.root / "pages" / meta.get("page_file", "")
        try:
            html = page_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return FetchedPage(
            url=url,
            final_url=meta.get("final_url") or url,
            status_code=int(meta.get("status_code") or 200),
            fetched_at=meta.get("fetched_at") or _iso(_utc_now()),
            etag=meta.get("etag"),
            last_modified=meta.get("last_modified"),
            content_hash=meta.get("content_hash") or _content_hash(html),
            html=html,
            from_cache=True,
            cache_status="fresh",
        )

    def put(self, page: FetchedPage) -> None:
        page_file = f"{page.content_hash}.html"
        page_path = self.root / "pages" / page_file
        if not page_path.exists():
            page_path.write_text(page.html, encoding="utf-8")
        meta = {
            "url": page.url,
            "final_url": page.final_url,
            "status_code": page.status_code,
            "fetched_at": page.fetched_at,
            "etag": page.etag,
            "last_modified": page.last_modified,
            "content_hash": page.content_hash,
            "page_file": page_file,
        }
        with self._lock:
            self._index[_cache_key(page.url)] = meta
            self._save_index()

    def touch(self, url: str, fetched_at: str) -> FetchedPage | None:
        with self._lock:
            key = _cache_key(url)
            meta = self._index.get(key)
            if not meta:
                return None
            meta["fetched_at"] = fetched_at
            self._index[key] = meta
            self._save_index()
        page = self.get(url)
        if page:
            page.cache_status = "revalidated"
        return page


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "unknown").lower()


def _is_fresh(page: FetchedPage, ttl_hours: int) -> bool:
    fetched_at = _parse_iso(page.fetched_at)
    if not fetched_at:
        return False
    return (_utc_now() - fetched_at) < dt.timedelta(hours=ttl_hours)


def fetch_page(
    url: str,
    *,
    cache: PageCache | None = None,
    timeout: float = 20.0,
    force: bool = False,
    cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS,
    client: httpx.Client | None = None,
) -> FetchedPage | None:
    """Fetch one public URL, using cache + HTTP validators when available.

    Pass a shared ``client`` to reuse connections (keep-alive) across many URLs.
    """
    cache = cache or PageCache()
    cached = cache.get(url)
    if cached and not force and _is_fresh(cached, cache_ttl_hours):
        cached.cache_status = "fresh"
        return cached

    headers = dict(_HEADERS)
    if cached and cached.etag:
        headers["If-None-Match"] = cached.etag
    if cached and cached.last_modified:
        headers["If-Modified-Since"] = cached.last_modified

    try:
        if client is not None:
            resp = client.get(url, headers=headers, timeout=timeout)
        else:
            with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as own:
                resp = own.get(url, headers=headers)
        if resp.status_code == 304 and cached:
            return cache.touch(url, _iso(_utc_now()))
        resp.raise_for_status()
        html = resp.text
    except (httpx.HTTPError, ValueError):
        if cached:
            cached.cache_status = "stale-if-error"
            return cached
        return None

    content_hash = _content_hash(html)
    page = FetchedPage(
        url=url,
        final_url=str(resp.url),
        status_code=resp.status_code,
        fetched_at=_iso(_utc_now()),
        etag=resp.headers.get("etag"),
        last_modified=resp.headers.get("last-modified"),
        content_hash=content_hash,
        html=html,
        from_cache=False,
        cache_status="miss" if not cached else "updated",
    )
    cache.put(page)
    return page


def fetch_many_pages(
    urls: list[str],
    *,
    timeout: float = 20.0,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    per_domain_limit: int = DEFAULT_PER_DOMAIN_LIMIT,
    force: bool = False,
    cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS,
) -> dict[str, FetchedPage]:
    """Fetch URLs concurrently, capped globally and per domain."""
    unique_urls = list(dict.fromkeys([u for u in urls if u]))
    if not unique_urls:
        return {}

    cache = PageCache()
    cache._defer_saves = True  # one index write at the end, not per page
    semaphores: dict[str, threading.BoundedSemaphore] = {}
    sem_lock = threading.Lock()

    def domain_sem(url: str) -> threading.BoundedSemaphore:
        host = _domain(url)
        with sem_lock:
            if host not in semaphores:
                semaphores[host] = threading.BoundedSemaphore(per_domain_limit)
            return semaphores[host]

    def one(url: str, client: httpx.Client) -> tuple[str, FetchedPage | None]:
        with domain_sem(url):
            return url, fetch_page(
                url,
                cache=cache,
                timeout=timeout,
                force=force,
                cache_ttl_hours=cache_ttl_hours,
                client=client,
            )

    out: dict[str, FetchedPage] = {}
    workers = max(1, min(max_concurrency, len(unique_urls)))
    # One shared client → connection pooling / keep-alive across all URLs.
    limits = httpx.Limits(max_connections=max_concurrency, max_keepalive_connections=max_concurrency)
    with httpx.Client(
        headers=_HEADERS, timeout=timeout, follow_redirects=True, limits=limits
    ) as client:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(one, url, client) for url in unique_urls]
            for future in as_completed(futures):
                url, page = future.result()
                if page:
                    out[url] = page
    cache.flush()
    return out


def fetch_clean_text(url: str, timeout: float = 20.0, max_chars: int = MAX_CHARS) -> str | None:
    """Compatibility helper: return readable plain text from a URL."""
    page = fetch_page(url, timeout=timeout)
    if not page:
        return None
    text = trafilatura.extract(page.html, include_comments=False, include_tables=True)
    if not text:
        return None
    return text[:max_chars]


def fetch_many(urls: list[str]) -> dict[str, str]:
    """Compatibility helper returning {url: clean_text}."""
    out: dict[str, str] = {}
    for url, page in fetch_many_pages(urls).items():
        text = trafilatura.extract(page.html, include_comments=False, include_tables=True)
        if text:
            out[url] = text[:MAX_CHARS]
    return out
