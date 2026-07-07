"""PUBLIC transfer-bonus research: one cached web search → sourced suggestions.

Finds CURRENT points transfer bonuses (e.g. "Amex MR → Avios +30% through
10/15") for the currencies the household can transfer from. Results are
SUGGESTIONS only — the UI applies them to transfer_partner rows via the
existing PUT endpoint after a human click, so nothing is ever adopted without
review and a source. No private data is involved anywhere in this flow.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import config, models

_CACHE: dict[str, tuple[dt.datetime, list[dict]]] = {}
_CACHE_TTL_HOURS = 12

_SYSTEM = (
    "You research PUBLIC, currently-running credit-card points transfer bonuses "
    "for a local tracker. Use only public sources (issuer pages, Doctor of "
    "Credit, Frequent Miler, US Credit Card Guide, The Points Guy). Cite URLs. "
    "Report only bonuses that are live today with a stated end date when known."
)


def _prompt(currencies: list[str]) -> str:
    listing = ", ".join(sorted(currencies)) or "major transferable currencies"
    return (
        "Find every points TRANSFER BONUS currently running from these programs: "
        f"{listing}.\n"
        "Respond with a JSON array only — one object per live bonus:\n"
        '[{"from_currency": "...", "to_program": "...", "bonus_pct": 30, '
        '"end_date": "YYYY-MM-DD or null", "source_url": "..."}]\n'
        "Rules: bonus_pct is the extra percentage (30 means 30% more points). "
        "Skip expired, rumored, or targeted-only bonuses. If none are running, "
        "return []."
    )


def _parse_suggestions(text: str) -> list[dict]:
    """Strict-ish parse: first JSON array in the text, each row validated."""
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        from_currency = str(item.get("from_currency") or "").strip()
        to_program = str(item.get("to_program") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        try:
            bonus_pct = float(item.get("bonus_pct"))
        except (TypeError, ValueError):
            continue
        if not from_currency or not to_program or not source_url.startswith("http"):
            continue
        if not (5 <= bonus_pct <= 200):
            continue  # implausible percentages are parser noise, not deals
        end_date = None
        raw_end = item.get("end_date")
        if raw_end:
            try:
                end_date = dt.date.fromisoformat(str(raw_end)[:10])
            except ValueError:
                end_date = None
        if end_date and end_date < dt.date.today():
            continue  # already over
        out.append(
            {
                "from_currency": from_currency,
                "to_program": to_program,
                "bonus_pct": bonus_pct,
                "end_date": end_date.isoformat() if end_date else None,
                "source_url": source_url,
            }
        )
    return out


def _match_partner(db: Session, suggestion: dict) -> models.TransferPartner | None:
    rows = db.scalars(select(models.TransferPartner)).all()
    want_from = suggestion["from_currency"].strip().lower()
    want_to = suggestion["to_program"].strip().lower()
    for row in rows:
        row_from = (row.from_currency or "").strip().lower()
        row_to = (row.to_program or "").strip().lower()
        if (want_from in row_from or row_from in want_from) and (
            want_to in row_to or row_to in want_to
        ):
            return row
    return None


def research_transfer_bonuses(db: Session, *, force: bool = False) -> dict:
    """One cached web search → validated, partner-matched suggestions.

    Never writes anything: the caller (UI) applies chosen suggestions through
    the normal transfer-partner update endpoint.
    """
    if not (config.WEB_SEARCH_ENABLED and config.llm_available()):
        return {
            "status": "unavailable",
            "note": "Web search or ANTHROPIC_API_KEY is not configured.",
            "suggestions": [],
        }

    currencies = sorted(
        {
            (row.from_currency or "").strip()
            for row in db.scalars(select(models.TransferPartner)).all()
            if (row.from_currency or "").strip()
        }
    )
    cache_key = "|".join(c.lower() for c in currencies)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cached = _CACHE.get(cache_key)
    if cached and not force and (now - cached[0]) < dt.timedelta(hours=_CACHE_TTL_HOURS):
        return {"status": "ok", "cached": True, "suggestions": cached[1]}

    from ...ingestion import extract

    result = extract._run_web_research(_SYSTEM, _prompt(currencies), max_uses=3, max_tokens=2500)
    suggestions = _parse_suggestions(result.get("text") or "")

    enriched: list[dict[str, Any]] = []
    for suggestion in suggestions:
        partner = _match_partner(db, suggestion)
        enriched.append(
            {
                **suggestion,
                "partner_id": partner.id if partner else None,
                "partner_label": (
                    f"{partner.from_currency} → {partner.to_program}" if partner else None
                ),
                "already_recorded": bool(
                    partner
                    and partner.bonus_pct == suggestion["bonus_pct"]
                    and (
                        (partner.bonus_end_date.isoformat() if partner.bonus_end_date else None)
                        == suggestion["end_date"]
                    )
                ),
            }
        )
    _CACHE[cache_key] = (now, enriched)
    return {"status": "ok", "cached": False, "suggestions": enriched}
