"""Run menu (§4.4, §10) — on-demand discovery / refresh actions.

These are the only endpoints that invoke the LLM ingestion engine. They return
503 with a clear message if ANTHROPIC_API_KEY is not configured, so the rest of
the app keeps working without it.
"""
from __future__ import annotations

import datetime as dt
from threading import Lock, Thread
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..db import SessionLocal, get_db
from ..ingestion import discover, rendered_fetch, schedule
from ..ingestion.extract import IngestionUnavailable
from ..product_identity import product_variant_key

router = APIRouter(prefix="/api/run", tags=["run"])

_refresh_lock = Lock()
_refresh_job: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "processed": 0,
    "total": 0,
    "skipped": 0,
    "current_product": None,
    "committed": 0,
    "proposed": 0,
    "errors": [],
    "warnings": [],
    "phase": "idle",
    "result": None,
}


def _utcnow() -> dt.datetime:
    # Naive UTC to match DateTime columns and the ingestion scheduler.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _job_snapshot() -> dict[str, Any]:
    with _refresh_lock:
        return dict(_refresh_job)


def _update_refresh_job(values: dict[str, Any]) -> None:
    with _refresh_lock:
        _refresh_job.update(values)


def _held_priority_refs(db: Session) -> dict[str, list]:
    """Private planner step: identify public product refs that deserve priority.

    The ingestion scheduler receives only product IDs/identity keys/variant
    keys, never HeldCard rows or private card fields.
    """
    held = db.scalars(
        select(models.HeldCard).where(models.HeldCard.status != "Closed")
    ).all()
    ids = sorted({card.product_id for card in held if card.product_id})
    keys = sorted({
        ((card.issuer or "").strip().lower(), (card.product_name or "").strip().lower())
        for card in held
    })
    variants = sorted({
        variant
        for card in held
        for variant in [product_variant_key(card.issuer, card.product_name)]
        if variant is not None
    })
    return {
        "priority_product_ids": ids,
        "priority_product_keys": keys,
        "priority_product_variants": variants,
    }


def _refresh_kwargs(payload: schemas.RefreshRequest, db: Session) -> dict[str, Any]:
    kwargs = {
        "issuer": payload.issuer,
        "limit": payload.limit,
        "only_stale": payload.only_stale,
        "product_ids": payload.product_ids,
        "source_urls": payload.source_urls,
        "use_web_search": payload.use_web_search,
        "refresh_stale_days": payload.refresh_stale_days,
        "force": payload.force,
        "batch_size": payload.batch_size,
        "web_fallback_limit": payload.web_fallback_limit,
        "llm_fallback": payload.llm_fallback,
        "include_incomplete": payload.include_incomplete,
        "incomplete_only": payload.incomplete_only,
        "peak_backfill": payload.peak_backfill,
        "refresh_valuations": payload.refresh_valuations,
        "valuations_only": payload.valuations_only,
        "use_rendered_fallback": payload.use_rendered_fallback,
    }
    if not payload.valuations_only:
        kwargs.update(_held_priority_refs(db))
    return kwargs


def _refresh_worker(payload: schemas.RefreshRequest) -> None:
    db = SessionLocal()
    try:
        _update_refresh_job(
            {
                "running": True,
                "started_at": _utcnow().isoformat(),
                "finished_at": None,
                "processed": 0,
                "total": 0,
                "skipped": 0,
                "current_product": None,
                "committed": 0,
                "proposed": 0,
                "errors": [],
                "warnings": [],
                "phase": "starting",
                "result": None,
            }
        )

        def progress(update: dict[str, Any]) -> None:
            _update_refresh_job(update)

        result = schedule.run_refresh(
            db,
            **_refresh_kwargs(payload, db),
            progress_callback=progress,
        )
        _update_refresh_job(
            {
                "running": False,
                "finished_at": _utcnow().isoformat(),
                "processed": result.get("products_checked", 0),
                "total": result.get("products_checked", 0),
                "skipped": result.get("products_skipped", 0),
                "committed": result.get("committed", 0),
                "proposed": result.get("proposed", 0),
                "errors": result.get("errors", []),
                "warnings": result.get("warnings", []),
                "current_product": None,
                "phase": "complete",
                "result": result,
            }
        )
    except Exception as exc:
        _update_refresh_job(
            {
                "running": False,
                "finished_at": _utcnow().isoformat(),
                "errors": [{"product": "refresh job", "error": str(exc)}],
                "warnings": [],
                "current_product": None,
                "phase": "failed",
            }
        )
    finally:
        db.close()


@router.get("/status")
def run_status():
    return {
        "llm_available": config.llm_available(),
        "model": config.ANTHROPIC_MODEL if config.llm_available() else None,
        "search_model": config.ANTHROPIC_SEARCH_MODEL if config.llm_available() else None,
        "web_search_enabled": config.WEB_SEARCH_ENABLED,
        "crawl4ai_enabled": config.CRAWL4AI_ENABLED,
        "crawl4ai_available": rendered_fetch.available() if config.CRAWL4AI_ENABLED else False,
        "crypto_available": config.crypto_available(),
        "ingestion_mode": "static_http_cache_parse_rendered_fallback_research_resolver_web_fallback",
    }


@router.post("/discover")
def run_discover(payload: schemas.DiscoverRequest, db: Session = Depends(get_db)):
    try:
        return discover.run_discovery(db, issuers=payload.issuers)
    except IngestionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/refresh")
def run_refresh(payload: schemas.RefreshRequest, db: Session = Depends(get_db)):
    if payload.background:
        with _refresh_lock:
            if _refresh_job["running"]:
                return {"status": "already_running", "job": dict(_refresh_job)}
        Thread(target=_refresh_worker, args=(payload,), daemon=True).start()
        return {"status": "started", "job": _job_snapshot()}

    try:
        return schedule.run_refresh(db, **_refresh_kwargs(payload, db))
    except IngestionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/refresh/status")
def refresh_status():
    return _job_snapshot()
