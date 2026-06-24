"""Benefits ledger from cards the household actually holds."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models
from ..benefit_normalization import normalize_public_benefits
from ..crypto import MissingKeyError
from ..product_identity import product_display_name, product_variant_key
from .decision_context import DecisionContext, key

BENEFIT_ALERT_WINDOW_DAYS = 45
BENEFIT_REFRESH_NOTICE_DAYS = 14
GLOBAL_BENEFIT_PERIOD_KEY = "__all__"


def _product_for_card(
    card: models.HeldCard,
    context: DecisionContext,
) -> models.CardProduct | None:
    return context.product_for_card(card)


def _cash_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", value)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _cash_amounts(text: str | None) -> list[float]:
    if not text:
        return []
    out: list[float] = []
    for match in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text):
        try:
            out.append(float(match.group(1).replace(",", "")))
        except ValueError:
            continue
    return out


def _points_value(text: str | None) -> tuple[int | None, str | None]:
    if not text:
        return None, None
    match = re.search(
        r"(\d{1,3}(?:,\d{3})+|\d{4,7})(?:\s+(?:anniversary|annual|bonus|award)){0,4}\s+(points?|miles?)",
        text,
        re.I,
    )
    if not match:
        return None, None
    try:
        amount = int(match.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = match.group(2).lower()
    return amount, "miles" if "mile" in unit else "points"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:160] or "benefit"


def _is_anniversary_benefit(text: str | None) -> bool:
    low = (text or "").lower()
    return any(term in low for term in ("anniversary", "cardmember year", "account anniversary", "renewal"))


def _cadence(text: str | None) -> str:
    low = (text or "").lower()
    if any(term in low for term in ("account anniversary year", "cardmember year")) and "credit" in low:
        return "cardmember_year"
    if _is_anniversary_benefit(text):
        return "anniversary"
    if any(term in low for term in ("monthly", "per month", "/month", "each month")):
        return "monthly"
    if any(term in low for term in ("semiannual", "semi-annual", "biannual", "bi-annual", "twice per year", "january through june", "july through december")):
        return "semiannual"
    if any(term in low for term in ("quarterly", "per quarter", "each quarter")):
        return "quarterly"
    if any(term in low for term in ("annually", "annual", "per year", "calendar year", "cardmember year", "each year")):
        return "annual"
    return "annual"


def _amount_for_period(text: str | None, cadence: str) -> float | None:
    amounts = _cash_amounts(text)
    if not amounts:
        return None
    low = (text or "").lower()
    if cadence in {"monthly", "semiannual", "quarterly"}:
        cadence_terms = {
            "monthly": ("monthly", "per month", "/month", "each month"),
            "semiannual": ("semiannual", "semi-annual", "biannual", "bi-annual", "twice per year"),
            "quarterly": ("quarterly", "per quarter", "each quarter"),
        }[cadence]
        for match in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text or ""):
            window = low[match.end() : match.end() + 80]
            if any(term in window for term in cadence_terms):
                try:
                    return float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
        if len(amounts) > 1:
            return min(amounts)
        divisor = {"monthly": 12, "semiannual": 2, "quarterly": 4}[cadence]
        if "annual" in low or "per year" in low:
            return round(amounts[0] / divisor, 2)
    return max(amounts)


def _period(cadence: str, today: dt.date | None = None) -> tuple[str, dt.date, dt.date]:
    today = today or dt.date.today()
    if cadence == "monthly":
        start = today.replace(day=1)
        if today.month == 12:
            end = dt.date(today.year, 12, 31)
        else:
            end = dt.date(today.year, today.month + 1, 1) - dt.timedelta(days=1)
        return f"{today.year}-{today.month:02d}", start, end
    if cadence == "quarterly":
        quarter = ((today.month - 1) // 3) + 1
        start_month = (quarter - 1) * 3 + 1
        start = dt.date(today.year, start_month, 1)
        end_month = start_month + 2
        end = dt.date(today.year, end_month + 1, 1) - dt.timedelta(days=1) if end_month < 12 else dt.date(today.year, 12, 31)
        return f"{today.year}-Q{quarter}", start, end
    if cadence == "semiannual":
        half = 1 if today.month <= 6 else 2
        start = dt.date(today.year, 1 if half == 1 else 7, 1)
        end = dt.date(today.year, 6, 30) if half == 1 else dt.date(today.year, 12, 31)
        return f"{today.year}-H{half}", start, end
    start = dt.date(today.year, 1, 1)
    end = dt.date(today.year, 12, 31)
    return str(today.year), start, end


def _cardmember_year_period(card: models.HeldCard, today: dt.date | None = None) -> tuple[str, dt.date, dt.date]:
    today = today or dt.date.today()
    source_date = card.renewal_date or card.date_opened
    if not source_date:
        return _period("annual", today)
    try:
        anchor = source_date.replace(year=today.year)
    except ValueError:
        anchor = dt.date(today.year, 2, 28)
    if anchor > today:
        end = anchor - dt.timedelta(days=1)
        try:
            start = anchor.replace(year=anchor.year - 1)
        except ValueError:
            start = dt.date(anchor.year - 1, 2, 28)
    else:
        start = anchor
        try:
            end = anchor.replace(year=anchor.year + 1) - dt.timedelta(days=1)
        except ValueError:
            end = dt.date(anchor.year + 1, 2, 28) - dt.timedelta(days=1)
    return f"cardmember-{start.isoformat()}:{end.isoformat()}", start, end


def _next_card_anniversary(card: models.HeldCard, today: dt.date | None = None) -> dt.date | None:
    today = today or dt.date.today()
    source_date = card.renewal_date or card.date_opened
    if not source_date:
        return None
    try:
        candidate = source_date.replace(year=today.year)
    except ValueError:
        candidate = dt.date(today.year, 2, 28)
    if candidate < today:
        try:
            candidate = candidate.replace(year=today.year + 1)
        except ValueError:
            candidate = dt.date(today.year + 1, 2, 28)
    return candidate


def _benefit_period(
    card: models.HeldCard,
    cadence: str,
    text: str | None,
    today: dt.date | None = None,
) -> tuple[str, dt.date, dt.date]:
    today = today or dt.date.today()
    if cadence == "cardmember_year":
        return _cardmember_year_period(card, today)
    if cadence == "anniversary" or _is_anniversary_benefit(text):
        anniversary = _next_card_anniversary(card, today)
        if anniversary:
            start = anniversary - dt.timedelta(days=BENEFIT_ALERT_WINDOW_DAYS)
            return f"anniversary-{anniversary.year}", start, anniversary
    return _period(cadence, today)


def _display_value(amount: float | None, points: int | None, points_unit: str | None, raw: str | None) -> str | None:
    if points:
        return f"{points:,} {points_unit or 'points'}"
    if amount is not None:
        return f"${amount:,.0f}"
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _verified_status(product: models.CardProduct) -> str:
    if not product.source_url or not product.last_verified:
        return "needs_source"
    age_days = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - product.last_verified).days
    if age_days > 30:
        return "stale"
    return "verified"


def _safe_usage_value(usage: models.BenefitUsage | None) -> float | None:
    if not usage:
        return None
    try:
        return usage.amount_used
    except MissingKeyError:
        return None


def _safe_usage_notes(usage: models.BenefitUsage | None) -> str | None:
    if not usage:
        return None
    try:
        return usage.notes
    except MissingKeyError:
        return None


def _safe_usage_available(usage: models.BenefitUsage | None) -> float | None:
    if not usage:
        return None
    return usage.amount_available


def _safe_usage_suppressed(usage: models.BenefitUsage | None) -> bool:
    if not usage:
        return False
    return bool(getattr(usage, "suppressed", False))


def _days_until(value: dt.date | None, today: dt.date) -> int | None:
    if value is None:
        return None
    return (value - today).days


def _month_day(value: dt.date) -> str:
    return value.strftime("%b ") + str(value.day)


def _timeframe_note(cadence: str, start: dt.date, end: dt.date) -> str:
    if cadence == "monthly":
        return f"Current month: {_month_day(start)}-{_month_day(end)}"
    if cadence == "semiannual":
        half = "first half" if start.month == 1 else "second half"
        return f"Current {half}: {_month_day(start)}-{_month_day(end)}"
    if cadence == "quarterly":
        quarter = ((start.month - 1) // 3) + 1
        return f"Current Q{quarter}: {_month_day(start)}-{_month_day(end)}"
    if cadence == "cardmember_year":
        return f"Cardmember year: {start.isoformat()} to {end.isoformat()}"
    if cadence == "annual":
        return f"Current year: {start.year}"
    return f"{start.isoformat()} to {end.isoformat()}"


def _status_label(status: str) -> str:
    labels = {
        "suppressed": "paused",
        "confirmed": "confirmed",
        "needs_amount": "needs amount",
        "unconfirmed": "open",
        "unused": "unused",
        "partial": "partial",
        "used": "used",
        "upcoming": "upcoming",
    }
    return labels.get(status, status.replace("_", " "))


def _benefit_priority(
    status: str,
    verified_status: str,
    days_remaining: int | None,
    is_anniversary: bool,
) -> str:
    if status == "suppressed":
        return "paused"
    if status in {"unused", "partial"} and days_remaining is not None and 0 <= days_remaining <= BENEFIT_ALERT_WINDOW_DAYS:
        return "attention"
    if status == "upcoming" and is_anniversary and days_remaining is not None and 0 <= days_remaining <= BENEFIT_ALERT_WINDOW_DAYS:
        return "attention"
    if status == "needs_amount":
        return "needs_data"
    if verified_status != "verified":
        return "needs_source"
    if status in {"used", "confirmed"}:
        return "done"
    return "active"


def _benefit_action_label(
    status: str,
    priority: str,
    amount_remaining: float | None,
    is_anniversary: bool,
) -> str:
    if status == "suppressed":
        return "Paused"
    if status == "confirmed":
        return "Confirmed"
    if status == "unconfirmed":
        return "Confirm"
    if status == "used":
        return "Done"
    if priority == "needs_data":
        return "Add amount"
    if priority == "needs_source":
        return "Review source"
    if is_anniversary:
        return "Watch"
    if amount_remaining is not None:
        return f"Use ${amount_remaining:,.0f}"
    return "Track"


def _tracking_kind(
    is_anniversary: bool,
    amount_available: float | None,
    points_amount: int | None,
    text: str | None = None,
) -> str:
    if is_anniversary or points_amount:
        return "anniversary"
    if amount_available is not None:
        return "cash_credit"
    low = (text or "").lower()
    if any(term in low for term in ("credit", "cash", "rebate")):
        return "source_review"
    if any(term in low for term in ("enroll", "activate", "registration required", "register")):
        return "enrollment"
    if any(term in low for term in ("priority pass", "lounge", "global entry", "tsa precheck", "clear")):
        return "access"
    if any(term in low for term in ("membership", "status", "dashpass", "doordash", "instacart")):
        return "membership"
    return "source_review"


def _benefit_status(
    amount_available: float | None,
    amount_used: float | None,
    amount_remaining: float | None,
    points_amount: int | None,
    cadence: str,
    tracking_kind: str,
) -> str:
    if amount_available is not None:
        if (amount_used or 0) <= 0:
            return "unused"
        if amount_remaining == 0:
            return "used"
        return "partial"
    if points_amount and cadence == "anniversary":
        return "upcoming"
    if tracking_kind in {"access", "enrollment", "membership"}:
        return "confirmed" if (amount_used or 0) > 0 else "unconfirmed"
    return "needs_amount"


def _clean_benefit_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"\[(?:text|title|meta|json-ld|table)\]\s*", "", text, flags=re.I)
    text = re.sub(r"Enrollment required\.?\s*", "Enrollment required. ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    return text or None


def _is_trackable_benefit(name: str, value: Any, description: Any) -> bool:
    text = " ".join(str(part or "") for part in (name, value, description)).strip()
    if not text:
        return False
    low = text.lower()
    noise_tokens = (
        "[json-ld]",
        "@context",
        "schema.org",
        "aggregaterating",
        "ratingcount",
        "feesandcommissionsspecification",
    )
    if any(token in low for token in noise_tokens):
        return False
    if "[title]" in low or "[meta]" in low:
        return False
    noise_phrases = (
        "doesn't include",
        "does not include",
        "don't include",
        "not include",
        "pay over time",
        "payment plan",
        "at checkout",
        "credit card members may have",
        "orders totaling",
        "break up credit card purchases",
        "eligible purchase with the",
        "start a plan",
        "airline travel partners",
        "same low annual fee",
        "pricing and terms",
        "fewer issuer",
        "compared to",
        "deciding between",
        "whether you'd use",
        "to access ",
        "to learn more",
        "please visit",
        "terms apply",
    )
    if any(phrase in low for phrase in noise_phrases):
        return False

    if _cash_amounts(text) or _points_value(text)[0]:
        return True

    cleaned_name = _clean_benefit_text(name) or ""
    if len(cleaned_name) > 160:
        return False
    benefit_terms = (
        "credit",
        "cash",
        "lounge",
        "anniversary",
        "bonus",
        "global entry",
        "tsa precheck",
        "clear",
        "uber",
        "dining",
        "resy",
        "dunkin",
        "hotel",
        "travel",
        "rental",
        "status",
        "membership",
        "doordash",
        "instacart",
        "benefit",
    )
    return any(term in low for term in benefit_terms)


def _benefit_rows(card: models.HeldCard, product: models.CardProduct) -> list[dict]:
    raw = normalize_public_benefits(
        product.issuer,
        product.product_name,
        product.source_url,
        product.card_benefits,
        allow_reference=_verified_status(product) == "verified",
    )
    if raw is None:
        raw = product.card_benefits if isinstance(product.card_benefits, list) else []
    rows: list[dict] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            name = item.strip()
            value = item.strip()
            frequency = None
            category = None
            description = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("benefit") or item.get("title") or f"Benefit {index + 1}").strip()
            value = item.get("value") or item.get("annual_value") or item.get("amount")
            frequency = item.get("frequency") or item.get("cadence")
            category = item.get("category") or item.get("type")
            description = item.get("description") or item.get("notes") or item.get("detail")
        else:
            continue
        if not name:
            continue
        cleaned_name = _clean_benefit_text(name) or name
        cleaned_description = _clean_benefit_text(description)
        if not _is_trackable_benefit(name, value, description):
            continue
        cash_value = _cash_value(value)
        rows.append(
            {
                "user": card.user,
                "card_id": card.id,
                "product_id": product.id,
                "issuer": card.issuer,
                "product_name": card.product_name,
                "display_name": product_display_name(card.issuer, card.product_name),
                "benefit_name": cleaned_name,
                "cash_value": cash_value,
                "raw_value": value,
                "frequency": frequency,
                "category": category,
                "description": cleaned_description,
                "source_url": product.source_url,
                "last_verified": product.last_verified.isoformat() if product.last_verified else None,
            }
        )
    return rows


def _usage_by_key(db: Session, user: str, held_ids: list[int]) -> dict[tuple[int, str, str], models.BenefitUsage]:
    if not held_ids:
        return {}
    rows = db.scalars(
        select(models.BenefitUsage)
        .where(models.BenefitUsage.user == user)
        .where(models.BenefitUsage.held_card_id.in_(held_ids))
    ).all()
    return {(row.held_card_id, row.benefit_key, row.period_key): row for row in rows}


def build_user_benefit_tracker(db: Session, user: str, context: DecisionContext | None = None) -> dict:
    context = context or DecisionContext.load(db)
    held = list(context.active_held_by_user.get(user, []))
    usages = _usage_by_key(db, user, [card.id for card in held])
    today = dt.date.today()
    rows: list[dict] = []
    missing: list[dict] = []
    for card in held:
        product = _product_for_card(card, context)
        if not product:
            missing.append(
                {
                    "held_card_id": card.id,
                    "issuer": card.issuer,
                    "product_name": card.product_name,
                    "display_name": product_display_name(card.issuer, card.product_name),
                    "reason": "No linked catalog product.",
                }
            )
            continue
        benefits = _benefit_rows(card, product)
        if not benefits:
            missing.append(
                {
                    "held_card_id": card.id,
                    "product_id": product.id,
                    "issuer": card.issuer,
                    "product_name": card.product_name,
                    "display_name": product_display_name(card.issuer, card.product_name),
                    "reason": "No verified benefits data on the catalog product.",
                    "source_url": product.source_url,
                    "last_verified": product.last_verified.isoformat() if product.last_verified else None,
                    "verified_status": _verified_status(product),
                }
            )
            continue
        for benefit in benefits:
            text = " ".join(
                str(part or "")
                for part in (
                    benefit.get("benefit_name"),
                    benefit.get("raw_value"),
                    benefit.get("frequency"),
                    benefit.get("category"),
                    benefit.get("description"),
                )
            )
            cadence = _cadence(text)
            period_key, period_start, period_end = _benefit_period(card, cadence, text, today)
            provisional_key = _slug(str(benefit["benefit_name"]))
            usage = usages.get((card.id, provisional_key, period_key))
            source_amount_available = _amount_for_period(text, cadence)
            if source_amount_available is None:
                source_amount_available = benefit.get("cash_value")
            points_amount, points_unit = _points_value(text)
            is_anniversary = cadence == "anniversary"
            manual_amount_available = _safe_usage_available(usage)
            amount_available = source_amount_available
            if amount_available is None and manual_amount_available is not None:
                amount_available = manual_amount_available
            amount_used = _safe_usage_value(usage)
            remaining = None
            progress = None
            if amount_available is not None:
                used = amount_used or 0.0
                remaining = round(max(amount_available - used, 0.0), 2)
                progress = round(min(max(used / amount_available, 0.0), 1.0), 4) if amount_available > 0 else None
            tracking_kind = _tracking_kind(is_anniversary, amount_available, points_amount, text)
            if amount_available is not None:
                amount_source = "source" if source_amount_available is not None else "manual_usage"
            elif tracking_kind in {"access", "enrollment", "membership", "anniversary"}:
                amount_source = "not_applicable"
            else:
                amount_source = "unknown"
            status = _benefit_status(amount_available, amount_used, remaining, points_amount, cadence, tracking_kind)
            verified_status = _verified_status(product)
            days_remaining = _days_until(period_end, today)
            priority = _benefit_priority(status, verified_status, days_remaining, is_anniversary)
            row = {
                "row_key": f"{card.id}:{provisional_key}:{period_key}",
                "usage_id": usage.id if usage else None,
                "user": user,
                "held_card_id": card.id,
                "product_id": product.id,
                "issuer": card.issuer,
                "product_name": card.product_name,
                "display_name": product_display_name(card.issuer, card.product_name),
                "benefit_key": provisional_key,
                "benefit_name": benefit["benefit_name"],
                "description": benefit.get("description"),
                "cadence": cadence,
                "period_key": period_key,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "due_date": period_end.isoformat(),
                "timeframe_note": _timeframe_note(cadence, period_start, period_end),
                "amount_available": amount_available,
                "amount_used": amount_used,
                "amount_remaining": remaining,
                "amount_source": amount_source,
                "points_amount": points_amount,
                "points_unit": points_unit,
                "display_value": _display_value(amount_available, points_amount, points_unit, benefit.get("raw_value")),
                "is_anniversary": is_anniversary,
                "progress": progress,
                "status": status,
                "status_label": _status_label(status),
                "priority": priority,
                "action_label": _benefit_action_label(status, priority, remaining, is_anniversary),
                "days_remaining": days_remaining,
                "tracking_kind": tracking_kind,
                "notes": _safe_usage_notes(usage),
                "suppressed": False,
                "suppression_scope": None,
                "source_url": product.source_url,
                "last_verified": product.last_verified.isoformat() if product.last_verified else None,
                "verified_status": verified_status,
            }
            row["benefit_label"] = _short_benefit_label(row)
            benefit_key = _slug(row["benefit_label"])
            row["benefit_key"] = benefit_key
            row["row_key"] = f"{card.id}:{benefit_key}:{period_key}"
            usage = usages.get((card.id, benefit_key, period_key)) or usage
            preference_usage = usages.get((card.id, benefit_key, GLOBAL_BENEFIT_PERIOD_KEY)) or usages.get(
                (card.id, provisional_key, GLOBAL_BENEFIT_PERIOD_KEY)
            )
            row["usage_id"] = usage.id if usage else None
            if preference_usage and not row.get("notes"):
                row["notes"] = _safe_usage_notes(preference_usage)
            if usage:
                manual_amount_available = _safe_usage_available(usage)
                row["notes"] = _safe_usage_notes(usage)
                amount_used = _safe_usage_value(usage)
                if row["amount_available"] is None and manual_amount_available is not None:
                    row["amount_available"] = manual_amount_available
                    row["amount_source"] = "manual_usage"
                amount_available = row["amount_available"]
                if amount_used is not None:
                    amount_used = max(float(amount_used), 0.0)
                    if amount_available is not None:
                        amount_used = min(amount_used, max(float(amount_available), 0.0))
                row["amount_used"] = amount_used
                remaining = None
                progress = None
                if amount_available is not None:
                    used = amount_used or 0.0
                    remaining = round(max(amount_available - used, 0.0), 2)
                    progress = round(min(max(used / amount_available, 0.0), 1.0), 4) if amount_available > 0 else None
                tracking_kind = _tracking_kind(
                    bool(row.get("is_anniversary")),
                    amount_available,
                    row.get("points_amount"),
                    text,
                )
                if amount_available is None and tracking_kind in {"access", "enrollment", "membership", "anniversary"}:
                    row["amount_source"] = "not_applicable"
                status = _benefit_status(
                    amount_available,
                    amount_used,
                    remaining,
                    row.get("points_amount"),
                    row.get("cadence"),
                    tracking_kind,
                )
                priority = _benefit_priority(status, verified_status, days_remaining, is_anniversary)
                row.update(
                    {
                        "amount_remaining": remaining,
                        "display_value": _display_value(amount_available, row.get("points_amount"), row.get("points_unit"), benefit.get("raw_value")),
                        "progress": progress,
                        "status": status,
                        "status_label": _status_label(status),
                        "priority": priority,
                        "action_label": _benefit_action_label(status, priority, remaining, is_anniversary),
                        "tracking_kind": tracking_kind,
                    }
                )
            suppressed = _safe_usage_suppressed(usage) or _safe_usage_suppressed(preference_usage)
            if suppressed:
                scope = "all" if _safe_usage_suppressed(preference_usage) else "period"
                row.update(
                    {
                        "suppressed": True,
                        "suppression_scope": scope,
                        "status": "suppressed",
                        "status_label": _status_label("suppressed"),
                        "priority": "paused",
                        "action_label": "Paused",
                    }
                )
            rows.append(row)
    deduped: dict[tuple[int, str, str], dict] = {}
    for row in rows:
        key_tuple = (row["held_card_id"], row["benefit_key"], row["period_key"])
        existing = deduped.get(key_tuple)
        if existing is None or _benefit_row_rank(row) > _benefit_row_rank(existing):
            deduped[key_tuple] = row
    rows = list(deduped.values())
    priority_order = {"attention": 0, "needs_data": 1, "needs_source": 2, "active": 3, "paused": 4, "done": 5}
    rows.sort(
        key=lambda row: (
            priority_order.get(row["priority"], 9),
            row.get("days_remaining") is None,
            row.get("days_remaining") if row.get("days_remaining") is not None else 9999,
            row["product_name"],
            row["benefit_name"],
        )
    )
    return {
        "benefits": rows,
        "missing": missing,
        "summary": {
            "tracked": len(rows),
            "used": len([row for row in rows if row["status"] == "used"]),
            "partial": len([row for row in rows if row["status"] == "partial"]),
            "unused": len([row for row in rows if row["status"] == "unused"]),
            "upcoming": len([row for row in rows if row["status"] == "upcoming"]),
            "confirmed": len([row for row in rows if row["status"] == "confirmed"]),
            "unconfirmed": len([row for row in rows if row["status"] == "unconfirmed"]),
            "suppressed": len([row for row in rows if row["status"] == "suppressed"]),
            "needs_amount": len([row for row in rows if row["status"] == "needs_amount"]),
            "missing_cards": len(missing),
            "attention": len([row for row in rows if row["priority"] == "attention"]),
            "needs_review": len([row for row in rows if row["priority"] in {"needs_data", "needs_source"}]),
            "manual_amounts": len([row for row in rows if row["amount_source"] == "manual_usage"]),
            "known_remaining_value": round(
                sum(row["amount_remaining"] or 0 for row in rows if row["status"] in {"unused", "partial"} and not row.get("suppressed")),
                2,
            ),
        },
    }


def _next_period_start(cadence: str, period_end: dt.date) -> dt.date | None:
    if cadence not in {"monthly", "quarterly", "semiannual", "annual", "cardmember_year"}:
        return None
    return period_end + dt.timedelta(days=1)


def _money_label(value: float | None) -> str | None:
    if value is None:
        return None
    return f"${value:,.0f}"


def _short_benefit_label(row: dict) -> str:
    text = " ".join(
        str(part or "")
        for part in (
            row.get("benefit_name"),
            row.get("description"),
            row.get("category"),
        )
    )
    text = _clean_benefit_text(text) or "benefit"
    text = re.sub(r"\bEnrollment required\.?\s*", "", text, flags=re.I)
    text = re.sub(r"[†‡*]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|.")
    low = text.lower()
    product_low = " ".join(str(row.get(part) or "") for part in ("issuer", "display_name", "product_name")).lower()
    amount = row.get("amount_available") or row.get("cash_value")
    if amount is None:
        amounts = _cash_amounts(text)
        amount = amounts[0] if amounts else None

    def amount_is(value: int) -> bool:
        try:
            return amount is not None and round(float(amount)) == value
        except (TypeError, ValueError):
            return False

    if "global entry" in low or "tsa precheck" in low:
        return "Global Entry/TSA credit"
    if "hotel" in low and "credit" in low:
        return "Hotel credit"
    if row.get("points_amount") or row.get("is_anniversary") or "anniversary" in low:
        points = row.get("points_amount")
        if points:
            unit = row.get("points_unit") or ("miles" if "mile" in low else "points")
            points_label = f"{int(points / 1000)}k" if points >= 1000 and points % 1000 == 0 else f"{points:,}"
            return f"{points_label} anniversary {unit}"
        return "anniversary bonus"

    is_amex_gold = ("amex" in product_low or "american express" in product_low) and "gold" in product_low
    if is_amex_gold:
        if "dining credit" in low or "grubhub" in low or "buffalo wild wings" in low or "five guys" in low or "cheesecake factory" in low:
            return "Dining credit"
        if "uber" in low:
            return "Uber Cash"
        if "dunkin" in low or (amount_is(7) and "monthly" in low):
            return "Dunkin monthly credit"
        if "resy" in low or (amount_is(50) and ("january through june" in low or row.get("cadence") == "semiannual")):
            return "Resy semiannual credit"
        if amount_is(100) and ("qualifying u.s" in low or "resy" in low):
            return "Resy credit"

    if "capital one travel" in low or ("travel credit" in low and "venture x" in product_low):
        return "Capital One Travel credit"
    if "uber" in low:
        return "Uber Cash"
    if "resy" in low:
        return "Resy credit"
    if "dunkin" in low:
        return "Dunkin monthly credit"
    if "doordash" in low and "promo" in low:
        return "DoorDash promo"
    if "dashpass" in low:
        return "DashPass"
    if "apple tv" in low:
        return "Apple TV"
    if "airline" in low and "credit" in low:
        return "Airline fee credit"
    if "travel" in low and "credit" in low:
        return "Travel credit"
    if "dining" in low and "credit" in low:
        return "Dining credit"
    if "january through june" in low and "july through december" in low and "credit" in low:
        return "Semiannual statement credit"
    if "statement credit" in low:
        if "monthly" in low:
            return "Monthly statement credit"
        if "semiannual" in low or "january through june" in low:
            return "Semiannual statement credit"
        return "Statement credit"
    if "clear" in low:
        return "CLEAR credit"
    if "priority pass" in low:
        return "Priority Pass"
    if "capital one lounge" in low:
        return "Capital One Lounge access"
    if "lounge" in low:
        return "Lounge access"

    text = re.sub(r"\b(up to|statement|annual|monthly|semiannual|credit|benefit)\b", " ", text, flags=re.I)
    text = re.split(r"[.;|:-]", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 44:
        text = text[:41].rstrip() + "..."
    return text or "benefit"


def _source_review_suffix(verified_status: str | None) -> str:
    return "" if verified_status == "verified" else " Source needs review."


def build_benefit_attention(db: Session, user: str, window_days: int = BENEFIT_ALERT_WINDOW_DAYS) -> list[dict]:
    tracker = build_user_benefit_tracker(db, user)
    today = dt.date.today()
    soon = today + dt.timedelta(days=window_days)
    alerts: list[dict] = []

    for row in tracker["benefits"]:
        if row.get("suppressed") or row.get("status") == "suppressed":
            continue
        try:
            period_start = dt.date.fromisoformat(row["period_start"])
            period_end = dt.date.fromisoformat(row["period_end"])
        except (TypeError, ValueError):
            continue
        display_name = row.get("display_name") or product_display_name(row.get("issuer"), row.get("product_name"))
        card = display_name
        verified_status = row.get("verified_status")
        source_suffix = _source_review_suffix(verified_status)
        benefit_label = _short_benefit_label(row)

        if row.get("is_anniversary"):
            if today <= period_end <= soon:
                value = row.get("display_value") or "benefit"
                action = "Anniversary bonus incoming"
                detail = f"{display_name} - {value} by {period_end.isoformat()}.{source_suffix}"
                alerts.append(
                    {
                        "type": "benefit_anniversary",
                        "severity": "info",
                        "card": card,
                        "benefit_name": row["benefit_name"],
                        "benefit_label": benefit_label,
                        "due_date": period_end.isoformat(),
                        "action": action,
                        "detail": detail,
                        "message": f"{action} - {detail}",
                    }
                )
            continue

        if row["status"] in {"unused", "partial"} and today <= period_end <= soon:
            remaining = _money_label(row.get("amount_remaining"))
            value = remaining or row.get("display_value") or "benefit"
            days = (period_end - today).days
            severity = "high" if days <= 10 else "info"
            action = f"Use {value} {benefit_label}".strip()
            detail = f"{display_name} - expires {period_end.isoformat()}.{source_suffix}"
            alerts.append(
                {
                    "type": "benefit_expiring",
                    "severity": severity,
                    "card": card,
                    "benefit_name": row["benefit_name"],
                    "benefit_label": benefit_label,
                    "due_date": period_end.isoformat(),
                    "action": action,
                    "detail": detail,
                    "message": f"{action} - {detail}",
                }
            )
            continue

        if row["status"] == "used":
            next_start = _next_period_start(row["cadence"], period_end)
            if next_start and today <= next_start <= today + dt.timedelta(days=BENEFIT_REFRESH_NOTICE_DAYS):
                action = f"{benefit_label} refreshes"
                detail = f"{display_name} - available again {next_start.isoformat()}.{source_suffix}"
                alerts.append(
                    {
                        "type": "benefit_refreshing",
                        "severity": "info",
                        "card": card,
                        "benefit_name": row["benefit_name"],
                        "benefit_label": benefit_label,
                        "due_date": next_start.isoformat(),
                        "action": action,
                        "detail": detail,
                        "message": f"{action} - {detail}",
                    }
                )

    for missing in tracker["missing"]:
        card = missing.get("display_name") or product_display_name(missing.get("issuer"), missing.get("product_name"))
        action = "Add benefit data"
        detail = f"{card} needs verified benefits before Dashboard can track credits."
        alerts.append(
            {
                "type": "benefit_data_missing",
                "severity": "info",
                "card": card,
                "action": action,
                "detail": detail,
                "message": f"{action} - {detail}",
            }
        )

    alerts.sort(key=lambda item: (item.get("due_date") or "9999-12-31", item["severity"] != "high"))
    return alerts


def upsert_benefit_usage(
    db: Session,
    user: str,
    payload,
) -> models.BenefitUsage:
    held = db.get(models.HeldCard, payload.held_card_id)
    if held is None or held.user != user:
        raise ValueError("Benefit usage must belong to one of the user's held cards.")
    row = db.scalar(
        select(models.BenefitUsage).where(
            models.BenefitUsage.user == user,
            models.BenefitUsage.held_card_id == payload.held_card_id,
            models.BenefitUsage.benefit_key == payload.benefit_key,
            models.BenefitUsage.period_key == (GLOBAL_BENEFIT_PERIOD_KEY if payload.suppress_all else payload.period_key),
        )
    )
    if row is None:
        row = models.BenefitUsage(
            user=user,
            held_card_id=payload.held_card_id,
            benefit_key=payload.benefit_key,
            benefit_name=payload.benefit_name,
            period_key=GLOBAL_BENEFIT_PERIOD_KEY if payload.suppress_all else payload.period_key,
        )
        db.add(row)
    amount_available = payload.amount_available
    if amount_available is not None:
        amount_available = max(float(amount_available), 0.0)
    amount_used = payload.amount_used
    if amount_used is not None:
        amount_used = max(float(amount_used), 0.0)
        if amount_available is not None:
            amount_used = min(amount_used, amount_available)

    row.benefit_name = payload.benefit_name
    if payload.suppress_all:
        row.period_start = None
        row.period_end = None
    else:
        row.period_start = payload.period_start
        row.period_end = payload.period_end
        row.amount_available = amount_available
        row.amount_used = amount_used

    if payload.suppressed is not None:
        row.suppressed = payload.suppressed
    row.notes = payload.notes
    db.commit()
    db.refresh(row)
    return row


def _household_group_status(rows: list[dict]) -> tuple[str, str]:
    active_rows = [row for row in rows if row.get("status") != "suppressed" and not row.get("suppressed")]
    if not active_rows and rows:
        return "paused", "Paused"
    if any(row.get("priority") == "attention" for row in active_rows):
        return "attention", "Use soon"
    if any(row.get("status") == "needs_amount" for row in active_rows):
        return "needs_data", "Add amount"
    if any(row.get("verified_status") != "verified" for row in active_rows):
        return "needs_source", "Review source"
    held_statuses = {row.get("status") for row in active_rows}
    if held_statuses and held_statuses <= {"used", "confirmed"}:
        return "done", "Done"
    if "unconfirmed" in held_statuses:
        return "active", "Confirm"
    if held_statuses and held_statuses <= {"upcoming"}:
        return "upcoming", "Watch"
    return "active", "Track"


def _money_total(rows: list[dict], field: str) -> float:
    return round(
        sum(
            float(row.get(field) or 0)
            for row in rows
            if row.get("amount_available") is not None and row.get("status") != "suppressed" and not row.get("suppressed")
        ),
        2,
    )


def _user_benefit_snapshot(row: dict) -> dict:
    amount_available = row.get("amount_available")
    amount_used = row.get("amount_used")
    amount_remaining = row.get("amount_remaining")
    return {
        "user": row["user"],
        "has_card": True,
        "held_card_id": row.get("held_card_id"),
        "status": row.get("status"),
        "status_label": row.get("status_label"),
        "action_label": row.get("action_label"),
        "amount_available": amount_available,
        "amount_used": amount_used,
        "amount_remaining": amount_remaining,
        "progress": row.get("progress"),
        "display_value": row.get("display_value"),
        "points_amount": row.get("points_amount"),
        "points_unit": row.get("points_unit"),
        "amount_source": row.get("amount_source"),
        "tracking_kind": row.get("tracking_kind"),
        "due_date": row.get("due_date"),
        "days_remaining": row.get("days_remaining"),
        "timeframe_note": row.get("timeframe_note"),
        "suppressed": row.get("suppressed"),
        "suppression_scope": row.get("suppression_scope"),
        "verified_status": row.get("verified_status"),
    }


def _benefit_row_rank(row: dict) -> tuple:
    return (
        row.get("amount_available") is not None,
        row.get("status") not in {"needs_amount", "unconfirmed"},
        row.get("verified_status") == "verified",
        row.get("source_url") is not None,
        -(len(str(row.get("benefit_name") or ""))),
    )


def _build_household_usage_groups(db: Session, context: DecisionContext) -> tuple[list[dict], list[dict], dict]:
    by_group: dict[str, dict] = {}
    missing: list[dict] = []
    trackers = {user: build_user_benefit_tracker(db, user, context) for user in config.USERS}
    for user, tracker in trackers.items():
        missing.extend({**item, "user": user} for item in tracker.get("missing", []))
        for row in tracker.get("benefits", []):
            variant = product_variant_key(row.get("issuer"), row.get("product_name")) or key(
                row.get("issuer"),
                row.get("product_name"),
            )
            benefit_label = row.get("benefit_label") or _short_benefit_label(row)
            group_key = f"{variant}:{_slug(benefit_label)}"
            group = by_group.setdefault(
                group_key,
                {
                    "group_key": group_key,
                    "issuer": row.get("issuer"),
                    "product_name": row.get("product_name"),
                    "display_name": row.get("display_name")
                    or product_display_name(row.get("issuer"), row.get("product_name")),
                    "benefit_key": row.get("benefit_key"),
                    "benefit_label": benefit_label,
                    "benefit_name": row.get("benefit_name"),
                    "cadence": row.get("cadence"),
                    "period_key": row.get("period_key"),
                    "due_date": row.get("due_date"),
                    "timeframe_note": row.get("timeframe_note"),
                    "display_value": row.get("display_value"),
                    "source_url": row.get("source_url"),
                    "last_verified": row.get("last_verified"),
                    "_user_rows": {},
                },
            )
            existing = group["_user_rows"].get(user)
            if existing is not None and _benefit_row_rank(existing) >= _benefit_row_rank(row):
                continue
            group["_user_rows"][user] = row
            if row.get("days_remaining") is not None and (
                group.get("days_remaining") is None or row["days_remaining"] < group["days_remaining"]
            ):
                group["days_remaining"] = row["days_remaining"]
                group["due_date"] = row.get("due_date")
                group["timeframe_note"] = row.get("timeframe_note")

    groups: list[dict] = []
    for group in by_group.values():
        rows = list(group.pop("_user_rows").values())
        users_by_name = {row["user"]: _user_benefit_snapshot(row) for row in rows}
        status, action = _household_group_status(rows)
        available_total = _money_total(rows, "amount_available")
        used_total = _money_total(rows, "amount_used")
        remaining_total = _money_total(rows, "amount_remaining")
        if remaining_total > 0:
            action = f"Use ${remaining_total:,.0f}"
        progress = round(min(max(used_total / available_total, 0.0), 1.0), 4) if available_total > 0 else None
        group.update(
            {
                "status": status,
                "action_label": action,
                "amount_available": available_total if available_total > 0 else None,
                "amount_used": used_total if available_total > 0 else None,
                "amount_remaining": remaining_total if available_total > 0 else None,
                "progress": progress,
                "users": [users_by_name[user] for user in config.USERS if user in users_by_name],
            }
        )
        groups.append(group)

    priority_order = {"attention": 0, "needs_data": 1, "needs_source": 2, "active": 3, "upcoming": 4, "paused": 5, "done": 6}
    groups.sort(
        key=lambda group: (
            priority_order.get(group.get("status"), 9),
            group.get("days_remaining") is None,
            group.get("days_remaining") if group.get("days_remaining") is not None else 9999,
            -(group.get("amount_remaining") or 0),
            group.get("display_name") or "",
            group.get("benefit_label") or "",
        )
    )
    summary = {
        "tracked_groups": len(groups),
        "need_action": len([group for group in groups if group["status"] in {"attention", "needs_data", "needs_source"}]),
        "known_available_value": round(sum(group.get("amount_available") or 0 for group in groups), 2),
        "known_used_value": round(sum(group.get("amount_used") or 0 for group in groups), 2),
        "known_remaining_value": round(sum(group.get("amount_remaining") or 0 for group in groups), 2),
        "cards_missing_benefits": len(missing),
    }
    return groups, missing, summary


def build_benefits_ledger(db: Session, context: DecisionContext | None = None) -> dict:
    context = context or DecisionContext.load(db)
    tracker_groups, tracker_missing, tracker_summary = _build_household_usage_groups(db, context)
    held = [card for cards in context.active_held_by_user.values() for card in cards]
    benefits: list[dict] = []
    missing: list[dict] = []
    for card in held:
        product = _product_for_card(card, context)
        if not product:
            missing.append(
                {
                    "user": card.user,
                    "card_id": card.id,
                    "issuer": card.issuer,
                    "product_name": card.product_name,
                    "display_name": product_display_name(card.issuer, card.product_name),
                    "reason": "No linked catalog product.",
                }
            )
            continue
        rows = _benefit_rows(card, product)
        if rows:
            benefits.extend(rows)
        else:
            missing.append(
                {
                    "user": card.user,
                    "card_id": card.id,
                    "product_id": product.id,
                    "issuer": card.issuer,
                    "product_name": card.product_name,
                    "display_name": product_display_name(card.issuer, card.product_name),
                    "reason": "No verified benefits data on the catalog product.",
                }
            )
    known_value = round(sum(row["cash_value"] or 0 for row in benefits), 2)
    benefits.sort(key=lambda row: (row["cash_value"] or 0, row["user"], row["product_name"]), reverse=True)
    return {
        "benefits": benefits,
        "missing": tracker_missing or missing,
        "tracker_groups": tracker_groups,
        "summary": {
            "total_benefits": len(benefits),
            "known_annual_value": known_value,
            "cards_missing_benefits": len(missing),
            **tracker_summary,
        },
    }
