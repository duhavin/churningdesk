"""Award availability provider abstraction.

seats.aero is treated as an award availability/search source, not a booking
engine. Booking guidance remains manual.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import config, models

_LIVE_CACHE_TTL_SECONDS = 10 * 60
_LIVE_CACHE: dict[tuple[tuple[str, str], ...], tuple[dt.datetime, list["AwardSearchResult"]]] = {}


@dataclass
class AwardSearchResult:
    program: str
    cabin: str | None
    points_cost: int | None
    source: str
    freshness: str | None = None
    mode: str = "benchmark"
    note: str | None = None


class AwardProvider(Protocol):
    mode: str

    def search_award_space(
        self,
        db: Session,
        *,
        origin: str | None = None,
        region: str | None,
        program: str | None,
        cabin: str | None,
        dates: str | None = None,
    ) -> list[AwardSearchResult]:
        ...

    def estimate_cost(self, db: Session, target: models.TargetRedemption) -> AwardSearchResult:
        ...


def _programs(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


class BenchmarkProvider:
    mode = "benchmark"

    def search_award_space(
        self,
        db: Session,
        *,
        origin: str | None = None,
        region: str | None,
        program: str | None,
        cabin: str | None,
        dates: str | None = None,
    ) -> list[AwardSearchResult]:
        stmt = select(models.AwardBenchmark)
        rows = list(db.scalars(stmt).all())
        out: list[AwardSearchResult] = []
        for row in rows:
            if program and row.program and row.program.strip().lower() != program.strip().lower():
                continue
            if cabin and row.cabin_or_tier and row.cabin_or_tier.strip().lower() != cabin.strip().lower():
                continue
            if region and row.route and region.strip().lower() not in row.route.strip().lower():
                continue
            out.append(
                AwardSearchResult(
                    program=row.program or program or "Unknown program",
                    cabin=row.cabin_or_tier or cabin,
                    points_cost=row.est_cost_points,
                    source=row.source_url or "AwardBenchmark",
                    freshness=row.last_verified.isoformat() if row.last_verified else None,
                    mode=self.mode,
                    note="Benchmark estimate; add SEATS_AERO_API_KEY for live availability.",
                )
            )
        return out

    def estimate_cost(self, db: Session, target: models.TargetRedemption) -> AwardSearchResult:
        programs = _programs(target.preferred_programs)
        for program in programs or [None]:
            results = self.search_award_space(
                db,
                origin=target.origin,
                region=target.destination or target.region,
                program=program,
                cabin=target.cabin_or_tier,
                dates=_date_range(target),
            )
            if results:
                return min(results, key=lambda r: r.points_cost or 10**12)
        return AwardSearchResult(
            program=programs[0] if programs else "Unknown program",
            cabin=target.cabin_or_tier,
            points_cost=target.est_cost_points,
            source="TargetRedemption",
            freshness=None,
            mode=self.mode,
            note="Target estimate; add AwardBenchmark rows or SEATS_AERO_API_KEY for better availability data.",
        )


class SeatsAeroProvider:
    mode = "live"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else config.SEATS_AERO_API_KEY
        self.base_url = (base_url or config.SEATS_AERO_BASE_URL).rstrip("/")

    def search_award_space(
        self,
        db: Session,
        *,
        origin: str | None = None,
        region: str | None,
        program: str | None,
        cabin: str | None,
        dates: str | None = None,
    ) -> list[AwardSearchResult]:
        if not self.api_key:
            return []
        params = {
            "origin": origin,
            "region": region,
            "program": program,
            "cabin": cabin,
            "dates": dates,
        }
        params = {k: v for k, v in params.items() if v}
        cache_key = tuple(sorted((str(k), str(v)) for k, v in params.items()))
        cached = _LIVE_CACHE.get(cache_key)
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if cached and (now - cached[0]).total_seconds() < _LIVE_CACHE_TTL_SECONDS:
            return cached[1]
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=20) as client:
            res = client.get(f"{self.base_url}/search", params=params, headers=headers)
            res.raise_for_status()
            payload = res.json()
        items = payload.get("results", payload if isinstance(payload, list) else [])
        out: list[AwardSearchResult] = []
        for item in items:
            out.append(
                AwardSearchResult(
                    program=str(item.get("program") or program or ""),
                    cabin=item.get("cabin") or cabin,
                    points_cost=_int_or_none(item.get("points") or item.get("points_cost") or item.get("miles")),
                    source=item.get("source") or "seats.aero",
                    freshness=item.get("updated_at")
                    or item.get("freshness")
                    or now.isoformat(),
                    mode=self.mode,
                    note="Live availability search; booking remains manual.",
                )
            )
        _LIVE_CACHE[cache_key] = (now, out)
        return out

    def estimate_cost(self, db: Session, target: models.TargetRedemption) -> AwardSearchResult:
        programs = _programs(target.preferred_programs)
        for program in programs or [None]:
            results = self.search_award_space(
                db,
                origin=target.origin,
                region=target.destination or target.region,
                program=program,
                cabin=target.cabin_or_tier,
                dates=_date_range(target),
            )
            if results:
                return min(results, key=lambda r: r.points_cost or 10**12)
        return BenchmarkProvider().estimate_cost(db, target)


def _int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _date_range(target: models.TargetRedemption) -> str | None:
    if target.travel_start_date and target.travel_end_date:
        return f"{target.travel_start_date.isoformat()}/{target.travel_end_date.isoformat()}"
    if target.travel_start_date:
        return target.travel_start_date.isoformat()
    return None


def default_provider() -> AwardProvider:
    return SeatsAeroProvider() if config.SEATS_AERO_ENABLED else BenchmarkProvider()
